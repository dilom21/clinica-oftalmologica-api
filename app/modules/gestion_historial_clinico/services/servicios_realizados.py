from datetime import datetime, timezone
from math import ceil

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_agenda_citas.repositories import repository as agenda_repo
from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_historial_clinico.repositories import repository as clinica_repo
from app.modules.gestion_historial_clinico.repositories import servicios_realizados as repo
from app.modules.gestion_historial_clinico.schemas.servicios_realizados import (
    PrecioAplicado,
    ServicioRealizadoActualizar,
    ServicioRealizadoCrear,
    ServicioRealizadoRespuesta,
    ServiciosRealizadosPagina,
    ServiciosRealizadosLoteCrear,
)
from app.modules.gestion_pacientes.repositories import repository as pacientes_repo
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def _oftalmologo_autenticado(db: Session, usuario: Usuario):
    if nombre_rol_actual(usuario) != "oftalmologo":
        raise HTTPException(403, "Solo un oftalmólogo puede registrar o modificar servicios realizados")
    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(db, usuario.id)
    if oftalmologo is None:
        raise HTTPException(403, "El usuario no tiene un perfil de oftalmólogo activo")
    return oftalmologo


def _utc(fecha: datetime):
    # PostgreSQL entrega fechas con zona. SQLite de las pruebas puede omitirla.
    return fecha.replace(tzinfo=timezone.utc) if fecha.tzinfo is None else fecha.astimezone(timezone.utc)


def _validar_servicio(db: Session, servicio_id: int):
    servicio = repo.obtener_servicio(db, servicio_id)
    if servicio is None:
        raise HTTPException(404, "Servicio oftalmológico no encontrado")
    if not servicio.estado:
        raise HTTPException(409, "El servicio oftalmológico está inactivo")
    return servicio


def _resolver_precio(datos, servicio, registro=None):
    # El importe histórico se conserva al editar otros datos del mismo servicio.
    if registro is not None and registro.servicio_id == datos.servicio_id:
        precio = registro.precio_aplicado
    else:
        try:
            precio = TypeAdapter(PrecioAplicado).validate_python(servicio.precio)
        except ValidationError:
            raise HTTPException(
                409, "El servicio no tiene un precio válido. Configúralo en el catálogo CU21 antes de registrarlo",
            ) from None
    if datos.precio_aplicado is not None and datos.precio_aplicado != precio:
        raise HTTPException(
            409, "El precio aplicado no puede modificarse. Actualiza el formulario para usar el precio del servicio",
        )
    return precio


def _validar_contexto(db: Session, datos, oftalmologo_id: int, fecha: datetime):
    paciente = pacientes_repo.obtener_paciente_por_id(db, datos.paciente_id)
    if paciente is None:
        raise HTTPException(404, "Paciente no encontrado")
    if not paciente.estado:
        raise HTTPException(409, "El paciente está inactivo")
    if _utc(fecha) > datetime.now(timezone.utc):
        raise HTTPException(422, "La fecha de realización no puede ser futura")

    if datos.consulta_clinica_id is None:
        return
    consulta = clinica_repo.obtener_consulta_por_id(db, datos.consulta_clinica_id)
    if consulta is None:
        raise HTTPException(404, "Consulta clínica no encontrada")
    if not consulta.estado:
        raise HTTPException(409, "La consulta clínica está inactiva")
    if consulta.oftalmologo_id != oftalmologo_id:
        raise HTTPException(403, "La consulta no pertenece al oftalmólogo autenticado")
    historial = clinica_repo.obtener_historial_activo_por_id(db, consulta.historial_clinico_id)
    if historial is None:
        raise HTTPException(409, "El historial de la consulta está inactivo")
    if historial.paciente_id != datos.paciente_id:
        raise HTTPException(409, "La consulta no corresponde al paciente seleccionado")
    if consulta.fecha_consulta is not None and _utc(fecha) < _utc(consulta.fecha_consulta):
        raise HTTPException(422, "La realización no puede ser anterior a la consulta")


def _validar_referencias(db: Session, datos, oftalmologo_id: int, fecha: datetime):
    servicio = _validar_servicio(db, datos.servicio_id)
    _validar_contexto(db, datos, oftalmologo_id, fecha)
    return servicio


def _obtener_registro(db: Session, registro_id: int, *, bloquear=False):
    registro = repo.obtener_registro(db, registro_id, bloquear=bloquear)
    if registro is None:
        raise HTTPException(404, "Servicio realizado no encontrado")
    return registro


def _validar_sin_pago_asociado(db: Session, registro_id: int):
    if repo.tiene_pago_asociado(db, registro_id):
        raise HTTPException(
            409,
            "El servicio realizado esta vinculado a un pago y no puede modificarse ni anularse",
        )


def _guardar_y_auditar(db: Session, registro, usuario: Usuario, accion: str):
    registro = repo.guardar_registro(db, registro)
    registrar_bitacora(
        db=db, usuario_id=usuario.id, accion=accion,
        entidad_afectada="servicio_realizado", id_registro_afectado=registro.id,
        descripcion="Operación sobre servicio realizado",
    )
    return ServicioRealizadoRespuesta.model_validate(registro)


def _confirmar(db: Session, registro, usuario: Usuario, accion: str):
    respuesta = _guardar_y_auditar(db, registro, usuario, accion)
    db.commit()
    return respuesta


def _traducir_integridad(exc: IntegrityError):
    # Una referencia eliminada concurrentemente se informa como conflicto.
    # Otros errores de BD no se disfrazan de errores del cliente.
    codigo = getattr(exc.orig, "sqlstate", None)
    if codigo == "23503" or "FOREIGN KEY constraint failed" in str(exc.orig):
        raise HTTPException(409, "Una referencia del servicio realizado ya no está disponible") from exc
    raise exc


def registrar(db: Session, datos: ServicioRealizadoCrear, usuario: Usuario):
    try:
        oftalmologo = _oftalmologo_autenticado(db, usuario)
        fecha = _utc(datos.fecha_realizacion or datetime.now(timezone.utc))
        servicio = _validar_referencias(db, datos, oftalmologo.id, fecha)
        precio = _resolver_precio(datos, servicio)
        registro = ServicioRealizado(
            servicio_id=datos.servicio_id, paciente_id=datos.paciente_id,
            consulta_clinica_id=datos.consulta_clinica_id, oftalmologo_id=oftalmologo.id,
            fecha_realizacion=fecha, precio_aplicado=precio,
            observaciones=datos.observaciones, estado=True,
        )
        return _confirmar(db, registro, usuario, "REGISTRAR_SERVICIO_REALIZADO")
    except IntegrityError as exc:
        db.rollback()
        _traducir_integridad(exc)
    except Exception:
        db.rollback()
        raise


def registrar_lote(db: Session, datos: ServiciosRealizadosLoteCrear, usuario: Usuario):
    """Valida todas las filas y confirma servicios y auditoría en una transacción."""
    try:
        oftalmologo = _oftalmologo_autenticado(db, usuario)
        fecha = _utc(datos.fecha_realizacion or datetime.now(timezone.utc))
        _validar_contexto(db, datos, oftalmologo.id, fecha)
        precios = []
        for linea in datos.servicios:
            servicio = _validar_servicio(db, linea.servicio_id)
            precios.append(_resolver_precio(linea, servicio))
        respuestas = []
        for linea, precio in zip(datos.servicios, precios, strict=True):
            registro = ServicioRealizado(
                servicio_id=linea.servicio_id, paciente_id=datos.paciente_id,
                consulta_clinica_id=datos.consulta_clinica_id, oftalmologo_id=oftalmologo.id,
                fecha_realizacion=fecha, precio_aplicado=precio,
                observaciones=linea.observaciones, estado=True,
            )
            respuestas.append(_guardar_y_auditar(
                db, registro, usuario, "REGISTRAR_SERVICIO_REALIZADO",
            ))
        db.commit()
        return respuestas
    except IntegrityError as exc:
        db.rollback()
        _traducir_integridad(exc)
    except Exception:
        db.rollback()
        raise


def consultar(db: Session, registro_id: int):
    return ServicioRealizadoRespuesta.model_validate(_obtener_registro(db, registro_id))


def listar(db: Session, **filtros):
    desde, hasta = filtros.get("desde"), filtros.get("hasta")
    if desde is not None and hasta is not None and desde > hasta:
        raise HTTPException(422, "desde no puede ser posterior a hasta")
    for campo in ("desde", "hasta"):
        if filtros.get(campo) is not None:
            filtros[campo] = _utc(filtros[campo])
    items, total = repo.listar_registros(db, **filtros)
    page, page_size = filtros.get("page", 1), filtros.get("page_size", 20)
    return ServiciosRealizadosPagina(
        items=items, total=total, page=page, page_size=page_size,
        total_pages=ceil(total / page_size),
    )


def actualizar(db: Session, registro_id: int, datos: ServicioRealizadoActualizar, usuario: Usuario):
    try:
        oftalmologo = _oftalmologo_autenticado(db, usuario)
        registro = _obtener_registro(db, registro_id, bloquear=True)
        if registro.oftalmologo_id != oftalmologo.id:
            raise HTTPException(403, "El servicio realizado pertenece a otro oftalmólogo")
        _validar_sin_pago_asociado(db, registro.id)
        if not registro.estado:
            raise HTTPException(409, "No se puede modificar un servicio realizado anulado")
        fecha = _utc(datos.fecha_realizacion or registro.fecha_realizacion or datetime.now(timezone.utc))
        servicio = _validar_referencias(db, datos, oftalmologo.id, fecha)
        precio = _resolver_precio(datos, servicio, registro)
        registro.servicio_id = datos.servicio_id
        registro.paciente_id = datos.paciente_id
        registro.consulta_clinica_id = datos.consulta_clinica_id
        registro.fecha_realizacion = fecha
        registro.observaciones = datos.observaciones
        registro.precio_aplicado = precio
        return _confirmar(db, registro, usuario, "ACTUALIZAR_SERVICIO_REALIZADO")
    except IntegrityError as exc:
        db.rollback()
        _traducir_integridad(exc)
    except Exception:
        db.rollback()
        raise


def anular(db: Session, registro_id: int, usuario: Usuario):
    try:
        oftalmologo = _oftalmologo_autenticado(db, usuario)
        registro = _obtener_registro(db, registro_id, bloquear=True)
        if registro.oftalmologo_id != oftalmologo.id:
            raise HTTPException(403, "El servicio realizado pertenece a otro oftalmólogo")
        _validar_sin_pago_asociado(db, registro.id)
        if registro.estado is False:
            respuesta = ServicioRealizadoRespuesta.model_validate(registro)
            db.rollback()
            return respuesta
        registro.estado = False
        return _confirmar(db, registro, usuario, "ANULAR_SERVICIO_REALIZADO")
    except Exception:
        db.rollback()
        raise
