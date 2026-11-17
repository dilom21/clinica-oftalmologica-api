from datetime import datetime, timezone
from decimal import Decimal
from math import ceil

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pacientes.repositories import repository as paciente_repo
from app.modules.gestion_pagos.repositories import administrativos as repo
from app.modules.gestion_pagos.repositories import repository as pago_repo
from app.modules.gestion_pagos.schemas.administrativos import (
    NotificacionPagoRespuesta,
    PagoAdministrativoCrear,
    PagoAdministrativoRespuesta,
    PagosAdministrativosPagina,
    ResumenServiciosPendientes,
    ServicioPendienteAdministrativo,
)
from app.modules.gestion_pagos.services import notificaciones
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def _actor_administrativo(usuario):
    if nombre_rol_actual(usuario) not in {
        "admin", "administrador", "recepcionista",
    }:
        raise HTTPException(
            403,
            "Se requiere rol Administrador o Recepcionista",
        )


def _decimal(valor):
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def _contexto_pago(pago):
    if not pago.detalles:
        raise HTTPException(409, "El pago no tiene servicios asociados")
    servicios = [detalle.servicio_realizado for detalle in pago.detalles]
    if any(item is None for item in servicios):
        raise HTTPException(409, "El pago tiene detalles inconsistentes")
    pacientes = {item.paciente_id for item in servicios}
    consultas = {item.consulta_clinica_id for item in servicios}
    if len(pacientes) != 1 or None in consultas or len(consultas) != 1:
        raise HTTPException(409, "El pago no tiene paciente y consulta consistentes")
    return next(iter(pacientes)), next(iter(consultas))


def _respuesta(db: Session, pago) -> PagoAdministrativoRespuesta:
    paciente_id, consulta_id = _contexto_pago(pago)
    notificacion = repo.obtener_notificacion_por_pago(db, pago.id)
    return PagoAdministrativoRespuesta(
        id=pago.id,
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_id,
        fecha_creacion=pago.fecha_creacion,
        fecha_hora_pago=pago.fecha_hora_pago,
        monto=_decimal(pago.monto),
        moneda=pago.moneda,
        metodo_pago=pago.metodo_pago,
        estado=pago.estado,
        referencia_bancaria=(
            pago.referencia_transaccion
            if pago.metodo_pago in {"TRANSFERENCIA", "QR"}
            else None
        ),
        observaciones=pago.observaciones,
        servicio_realizado_ids=[d.servicio_realizado_id for d in pago.detalles],
        notificacion=(
            NotificacionPagoRespuesta.model_validate(notificacion)
            if notificacion is not None else None
        ),
    )


def listar_servicios_pendientes(
    db: Session,
    usuario,
    *,
    paciente_id: int,
    consulta_clinica_id: int | None = None,
):
    _actor_administrativo(usuario)
    paciente = paciente_repo.obtener_paciente_por_id(db, paciente_id)
    if paciente is None or not paciente.estado:
        raise HTTPException(404, "Paciente activo no encontrado")
    servicios = pago_repo.listar_servicios_pendientes_pago(
        db,
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
    )
    conflictos = repo.conflictos_por_servicio(db, [item.id for item in servicios])
    items = [
        ServicioPendienteAdministrativo(
            servicio_realizado_id=item.id,
            paciente_id=item.paciente_id,
            consulta_clinica_id=item.consulta_clinica_id,
            nombre_servicio=item.servicio.nombre,
            fecha_realizacion=item.fecha_realizacion,
            precio_aplicado=_decimal(item.precio_aplicado),
            cobrable=item.id not in conflictos,
            conflicto_pago=conflictos.get(item.id),
        )
        for item in servicios
    ]
    return ResumenServiciosPendientes(
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
        servicios=items,
        total=sum(
            (item.precio_aplicado for item in items if item.cobrable),
            Decimal("0.00"),
        ),
    )


def _validar_reintento(existente, datos: PagoAdministrativoCrear):
    ids = sorted(detalle.servicio_realizado_id for detalle in existente.detalles)
    referencia = existente.referencia_transaccion
    esperado = datos.referencia_bancaria if datos.metodo_pago != "EFECTIVO" else None
    if (
        existente.metodo_pago != datos.metodo_pago
        or ids != sorted(datos.servicio_realizado_ids)
        or referencia != esperado
        or existente.observaciones != datos.observaciones
    ):
        raise HTTPException(409, "La clave idempotente ya fue usada con otros datos")


def _conflictos(db: Session, ids: list[int], *, excluir=None):
    conflictos = repo.pagos_conflictivos_para_servicios(
        db, ids, excluir_pago_id=excluir,
    )
    if not conflictos:
        return
    if any(item.pasarela == "STRIPE" for item in conflictos):
        raise HTTPException(
            409,
            "Existe un intento Stripe activo o potencialmente cobrable; concilielo antes del pago manual",
        )
    raise HTTPException(409, "Uno o mas servicios ya estan reservados o pagados")


def registrar_pago(
    db: Session,
    datos: PagoAdministrativoCrear,
    clave_idempotencia: str,
    usuario,
    ip: str | None = None,
):
    _actor_administrativo(usuario)
    try:
        existente = repo.obtener_pago_por_clave(db, clave_idempotencia, bloquear=True)
        if existente is not None:
            _validar_reintento(existente, datos)
            pago_id = existente.id
            db.rollback()
            return _respuesta(db, repo.obtener_pago_administrativo(db, pago_id))

        bloqueados = pago_repo.obtener_servicios_realizados_por_ids_para_actualizacion(
            db, datos.servicio_realizado_ids,
        )
        seleccion = pagos_service.validar_seleccion_servicios(
            db,
            datos.servicio_realizado_ids,
            paciente_id_esperado=datos.paciente_id,
            servicios_precargados=bloqueados,
        )
        _conflictos(db, datos.servicio_realizado_ids)
        aprobado = datos.metodo_pago == "EFECTIVO"
        pago = repo.crear_pago_manual(
            db,
            monto=seleccion.total,
            metodo_pago=datos.metodo_pago,
            estado="APROBADO" if aprobado else "PENDIENTE",
            referencia=datos.referencia_bancaria,
            observaciones=datos.observaciones,
            clave_idempotencia=clave_idempotencia,
            fecha_hora_pago=datetime.now(timezone.utc) if aprobado else None,
        )
        pago_repo.crear_detalles_pago(
            db, pago_id=pago.id, servicios=seleccion.servicios,
        )
        if aprobado:
            notificaciones.programar_comprobante(db, pago.id)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="REGISTRAR_PAGO_ADMINISTRATIVO",
            entidad_afectada="pago",
            id_registro_afectado=pago.id,
            descripcion=f"Pago {datos.metodo_pago} registrado en estado {pago.estado}",
        )
        db.commit()
        return _respuesta(db, repo.obtener_pago_administrativo(db, pago.id))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Referencia bancaria o clave idempotente duplicada") from exc
    except Exception:
        db.rollback()
        raise


def confirmar_pago(db: Session, pago_id: int, usuario, ip: str | None = None):
    _actor_administrativo(usuario)
    try:
        pago = repo.obtener_pago_administrativo(db, pago_id, bloquear=True)
        if pago is None or pago.pasarela is not None:
            raise HTTPException(404, "Pago administrativo no encontrado")
        if pago.metodo_pago not in {"TRANSFERENCIA", "QR"}:
            raise HTTPException(409, "Solo transferencia o QR pendientes requieren confirmacion")
        if pago.estado == "APROBADO":
            db.rollback()
            return _respuesta(db, pago)
        if pago.estado != "PENDIENTE":
            raise HTTPException(409, f"No se puede confirmar un pago {pago.estado}")
        ids = [detalle.servicio_realizado_id for detalle in pago.detalles]
        bloqueados = pago_repo.obtener_servicios_realizados_por_ids_para_actualizacion(db, ids)
        seleccion = pagos_service.validar_seleccion_servicios(
            db, ids, servicios_precargados=bloqueados,
        )
        if seleccion.total != _decimal(pago.monto):
            raise HTTPException(409, "El monto del pago ya no coincide con sus servicios")
        _conflictos(db, ids, excluir=pago.id)
        pago_repo.actualizar_estado_pago(
            db, pago, estado="APROBADO", fecha_hora_pago=datetime.now(timezone.utc),
        )
        notificaciones.programar_comprobante(db, pago.id)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="CONFIRMAR_PAGO_ADMINISTRATIVO",
            entidad_afectada="pago",
            id_registro_afectado=pago.id,
            descripcion=f"Pago {pago.metodo_pago} verificado y aprobado",
        )
        db.commit()
        return _respuesta(db, repo.obtener_pago_administrativo(db, pago.id))
    except Exception:
        db.rollback()
        raise


def consultar(db: Session, pago_id: int, usuario):
    _actor_administrativo(usuario)
    pago = repo.obtener_pago_administrativo(db, pago_id)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    return _respuesta(db, pago)


def listar(db: Session, usuario, **filtros):
    _actor_administrativo(usuario)
    desde, hasta = filtros.get("desde"), filtros.get("hasta")
    if desde is not None and hasta is not None and desde > hasta:
        raise HTTPException(422, "desde no puede ser posterior a hasta")
    pagos, total = repo.listar_pagos(db, **filtros)
    page, page_size = filtros.get("page", 1), filtros.get("page_size", 20)
    return PagosAdministrativosPagina(
        items=[_respuesta(db, pago) for pago in pagos],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=ceil(total / page_size),
    )


def generar_comprobante(db: Session, pago_id: int, usuario):
    _actor_administrativo(usuario)
    pago = repo.obtener_pago_administrativo(db, pago_id)
    if pago is None:
        raise HTTPException(404, "Comprobante no disponible")
    return pagos_service.generar_comprobante_interno(db, pago_id)


def estado_notificacion_paciente(db: Session, pago_id: int, usuario):
    pago = repo.obtener_pago_administrativo(db, pago_id)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    paciente = pagos_service._resolver_paciente_actual(db, usuario)
    paciente_pago, _ = _contexto_pago(pago)
    if paciente_pago != paciente.id:
        raise HTTPException(404, "Pago no encontrado")
    item = repo.obtener_notificacion_por_pago(db, pago_id)
    if item is None:
        raise HTTPException(404, "Notificacion no disponible")
    return NotificacionPagoRespuesta.model_validate(item)


def estado_notificacion_administrativa(db: Session, pago_id: int, usuario):
    _actor_administrativo(usuario)
    pago = repo.obtener_pago_administrativo(db, pago_id)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    item = repo.obtener_notificacion_por_pago(db, pago_id)
    if item is None:
        raise HTTPException(404, "Notificacion no disponible")
    return NotificacionPagoRespuesta.model_validate(item)


def reintentar_notificacion(db: Session, pago_id: int, usuario):
    _actor_administrativo(usuario)
    try:
        item = notificaciones.solicitar_reintento(db, pago_id)
        db.commit()
        return NotificacionPagoRespuesta.model_validate(item)
    except Exception:
        db.rollback()
        raise
