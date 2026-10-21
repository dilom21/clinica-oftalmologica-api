from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_historial_clinico.repositories import controles as repo
from app.modules.gestion_historial_clinico.repositories import repository as clinico_repo
from app.modules.gestion_historial_clinico.schemas.controles import (
    ControlMedicoActualizar,
    ControlMedicoCrear,
    ControlMedicoRespuesta,
)
from app.modules.gestion_pacientes.repositories.repository import obtener_paciente_por_id
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


_ZONA_CLINICA = ZoneInfo("America/La_Paz")
_ROL_OFTALMOLOGO = "oftalmologo"


def _ahora_local() -> datetime:
    return datetime.now(_ZONA_CLINICA)


def _fecha_consulta_local(consulta) -> datetime:
    fecha = consulta.fecha_consulta
    if not isinstance(fecha, datetime):
        raise HTTPException(
            status_code=409,
            detail="La consulta no tiene una fecha válida para programar controles",
        )
    # Las fechas clínicas se registran en UTC; SQLite pierde el offset en tests.
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=timezone.utc)
    try:
        return fecha.astimezone(_ZONA_CLINICA)
    except (OverflowError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="La consulta no tiene una fecha válida para programar controles",
        ) from exc


def _autorizar_oftalmologo(usuario: Usuario):
    if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
        raise HTTPException(
            status_code=403,
            detail="Solo un oftalmólogo puede administrar controles médicos",
        )


def _obtener_contexto_clinico(db: Session, consulta_id: int, usuario: Usuario):
    consulta = clinico_repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404, detail="Consulta clínica no encontrada o inactiva",
        )

    oftalmologo = consulta.oftalmologo
    if oftalmologo is None or not oftalmologo.estado:
        raise HTTPException(
            status_code=403,
            detail="El oftalmólogo responsable no tiene un perfil activo",
        )
    if oftalmologo.usuario_id != usuario.id:
        raise HTTPException(
            status_code=403,
            detail="La consulta no pertenece al oftalmólogo autenticado",
        )

    historial = clinico_repo.obtener_historial_activo_por_id(
        db, consulta.historial_clinico_id,
    )
    if historial is None:
        raise HTTPException(
            status_code=404, detail="Historial clínico no encontrado o inactivo",
        )
    if clinico_repo.obtener_paciente_activo_por_id(db, historial.paciente_id) is None:
        raise HTTPException(
            status_code=409,
            detail="El paciente asociado al historial clínico no está activo",
        )
    if _fecha_consulta_local(consulta) > _ahora_local():
        raise HTTPException(
            status_code=409,
            detail="El control debe asociarse a una atención clínica previa",
        )
    return consulta, historial


def _validar_fecha_programada(fecha: date, consulta, *, exigir_fecha_actual: bool):
    if fecha < _fecha_consulta_local(consulta).date():
        raise HTTPException(
            status_code=422,
            detail="La fecha del control no puede ser anterior a la consulta clínica",
        )
    if exigir_fecha_actual and fecha < _ahora_local().date():
        raise HTTPException(
            status_code=422,
            detail="La fecha programada no puede ser anterior a la fecha actual",
        )


def programar_control(
    db: Session,
    consulta_id: int,
    datos: ControlMedicoCrear,
    usuario: Usuario,
):
    try:
        _autorizar_oftalmologo(usuario)
        consulta, historial = _obtener_contexto_clinico(db, consulta_id, usuario)
        _validar_fecha_programada(
            datos.fecha_programada, consulta, exigir_fecha_actual=True,
        )
        control = repo.crear_control(
            db,
            consulta_clinica_id=consulta.id,
            paciente_id=historial.paciente_id,
            oftalmologo_id=consulta.oftalmologo_id,
            datos=datos,
        )
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="PROGRAMAR_CONTROL_MEDICO",
            entidad_afectada="control_medico",
            id_registro_afectado=control.id,
            descripcion="Control médico programado desde una consulta clínica",
        )
        # Preparar la respuesta antes del commit evita lecturas luego de confirmar.
        respuesta = ControlMedicoRespuesta.model_validate(control)
        db.commit()
        return respuesta
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="No se pudo guardar el control médico",
        ) from exc
    except Exception:
        db.rollback()
        raise


def actualizar_control(
    db: Session,
    control_id: int,
    datos: ControlMedicoActualizar,
    usuario: Usuario,
):
    try:
        _autorizar_oftalmologo(usuario)
        control = repo.obtener_control_por_id(db, control_id)
        if control is None:
            raise HTTPException(status_code=404, detail="Control médico no encontrado")
        if control.consulta_clinica_id is None:
            raise HTTPException(
                status_code=409,
                detail="El control no tiene una consulta clínica previa asociada",
            )
        consulta, historial = _obtener_contexto_clinico(
            db, control.consulta_clinica_id, usuario,
        )
        if (
            control.paciente_id != historial.paciente_id
            or control.oftalmologo_id != consulta.oftalmologo_id
        ):
            raise HTTPException(
                status_code=409,
                detail="El control no coincide con el paciente y oftalmólogo de la consulta",
            )

        cambios = datos.model_dump(exclude_unset=True)
        fecha = cambios.get("fecha_programada", control.fecha_programada)
        reprograma = (
            fecha != control.fecha_programada
            or (cambios.get("estado") == "PROGRAMADO" and control.estado != "PROGRAMADO")
        )
        _validar_fecha_programada(fecha, consulta, exigir_fecha_actual=reprograma)
        control = repo.actualizar_control(db, control, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="ACTUALIZAR_CONTROL_MEDICO",
            entidad_afectada="control_medico",
            id_registro_afectado=control.id,
            descripcion="Control médico actualizado",
        )
        respuesta = ControlMedicoRespuesta.model_validate(control)
        db.commit()
        return respuesta
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="No se pudo guardar el control médico",
        ) from exc
    except Exception:
        db.rollback()
        raise


def listar_controles(
    db: Session,
    *,
    paciente_id: int | None = None,
    consulta_clinica_id: int | None = None,
    estado: str | None = None,
):
    try:
        if paciente_id is not None:
            if obtener_paciente_por_id(db, paciente_id) is None:
                raise HTTPException(status_code=404, detail="Paciente no encontrado")
        if consulta_clinica_id is not None:
            consulta = clinico_repo.obtener_consulta_por_id(db, consulta_clinica_id)
            if consulta is None:
                raise HTTPException(status_code=404, detail="Consulta clínica no encontrada")
        return repo.listar_controles(
            db,
            paciente_id=paciente_id,
            consulta_clinica_id=consulta_clinica_id,
            estado=estado,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="No se pudieron consultar los controles médicos",
        ) from exc


def listar_controles_por_consulta(db: Session, consulta_id: int):
    return listar_controles(db, consulta_clinica_id=consulta_id)


def consultar_control(db: Session, control_id: int):
    try:
        control = repo.obtener_control_por_id(db, control_id)
        if control is None:
            raise HTTPException(status_code=404, detail="Control médico no encontrado")
        return control
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="No se pudo consultar el control médico",
        ) from exc


def obtener_oftalmologo_actual(db: Session, usuario: Usuario):
    try:
        if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
            return None
        return repo.obtener_oftalmologo_activo_por_usuario_id(db, usuario.id)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail="No se pudo consultar el oftalmólogo responsable",
        ) from exc
