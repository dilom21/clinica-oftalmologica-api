from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_pagos.models.models import NotificacionPago, Pago, PagoDetalle
from app.modules.gestion_usuarios_seguridad.models.models import Usuario


def obtener_pago_por_clave(db: Session, clave: str, *, bloquear: bool = False):
    stmt = select(Pago).options(selectinload(Pago.detalles)).where(
        Pago.clave_idempotencia == clave,
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def pagos_conflictivos_para_servicios(
    db: Session,
    servicio_ids: Iterable[int],
    *,
    excluir_pago_id: int | None = None,
):
    ids = list(servicio_ids)
    stmt = (
        select(Pago)
        .join(PagoDetalle, PagoDetalle.pago_id == Pago.id)
        .options(selectinload(Pago.detalles))
        .where(
            PagoDetalle.servicio_realizado_id.in_(ids),
            or_(
                Pago.estado == "APROBADO",
                Pago.estado == "PENDIENTE",
                (Pago.pasarela == "STRIPE") & (Pago.estado == "RECHAZADO"),
            ),
        )
        .order_by(Pago.id.asc())
        .with_for_update()
    )
    if excluir_pago_id is not None:
        stmt = stmt.where(Pago.id != excluir_pago_id)
    return list(db.scalars(stmt).unique().all())


def conflictos_por_servicio(db: Session, servicio_ids: Iterable[int]):
    ids = list(servicio_ids)
    if not ids:
        return {}
    filas = db.execute(
        select(
            PagoDetalle.servicio_realizado_id,
            Pago.pasarela,
            Pago.estado,
        )
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id.in_(ids),
            or_(
                Pago.estado == "PENDIENTE",
                (Pago.pasarela == "STRIPE") & (Pago.estado == "RECHAZADO"),
            ),
        )
    ).all()
    resultado = {}
    for servicio_id, pasarela, estado in filas:
        conflicto = (
            "INTENTO_STRIPE_POTENCIALMENTE_COBRABLE"
            if pasarela == "STRIPE"
            else "PAGO_MANUAL_PENDIENTE"
        )
        # Stripe tiene prioridad informativa sobre un pendiente manual.
        if servicio_id not in resultado or pasarela == "STRIPE":
            resultado[servicio_id] = conflicto
    return resultado


def crear_pago_manual(
    db: Session,
    *,
    monto,
    metodo_pago: str,
    estado: str,
    referencia: str | None,
    observaciones: str | None,
    clave_idempotencia: str,
    fecha_hora_pago=None,
):
    pago = Pago(
        monto=monto,
        moneda="BOB",
        metodo_pago=metodo_pago,
        estado=estado,
        pasarela=None,
        referencia_transaccion=referencia,
        observaciones=observaciones,
        clave_idempotencia=clave_idempotencia,
        fecha_hora_pago=fecha_hora_pago,
    )
    db.add(pago)
    db.flush()
    return pago


def obtener_pago_administrativo(db: Session, pago_id: int, *, bloquear=False):
    stmt = (
        select(Pago)
        .options(
            selectinload(Pago.detalles)
            .selectinload(PagoDetalle.servicio_realizado)
            .selectinload(ServicioRealizado.servicio),
        )
        .where(Pago.id == pago_id)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def listar_pagos(
    db: Session,
    *,
    paciente_id=None,
    metodo_pago=None,
    estado=None,
    desde=None,
    hasta=None,
    page=1,
    page_size=20,
):
    filtros = []
    if metodo_pago is not None:
        filtros.append(Pago.metodo_pago == metodo_pago)
    if estado is not None:
        filtros.append(Pago.estado == estado)
    if desde is not None:
        filtros.append(Pago.fecha_creacion >= desde)
    if hasta is not None:
        filtros.append(Pago.fecha_creacion <= hasta)
    if paciente_id is not None:
        filtros.append(exists().where(
            PagoDetalle.pago_id == Pago.id,
            PagoDetalle.servicio_realizado_id == ServicioRealizado.id,
            ServicioRealizado.paciente_id == paciente_id,
        ))
    total = db.scalar(select(func.count()).select_from(Pago).where(*filtros)) or 0
    stmt = (
        select(Pago)
        .options(
            selectinload(Pago.detalles)
            .selectinload(PagoDetalle.servicio_realizado)
            .selectinload(ServicioRealizado.servicio),
        )
        .where(*filtros)
        .order_by(Pago.fecha_creacion.desc(), Pago.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), int(total)


def obtener_destinatario_pago(db: Session, pago_id: int):
    filas = db.execute(
        select(Paciente.id, Usuario.correo, Usuario.estado)
        .join(ServicioRealizado, ServicioRealizado.paciente_id == Paciente.id)
        .join(PagoDetalle, PagoDetalle.servicio_realizado_id == ServicioRealizado.id)
        .outerjoin(Usuario, Usuario.id == Paciente.usuario_id)
        .where(PagoDetalle.pago_id == pago_id)
        .distinct()
    ).all()
    return filas


def obtener_notificacion_por_pago(db: Session, pago_id: int, *, bloquear=False):
    stmt = select(NotificacionPago).where(
        NotificacionPago.pago_id == pago_id,
        NotificacionPago.tipo == "COMPROBANTE_PAGO",
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def crear_notificacion(
    db: Session,
    *,
    pago_id: int,
    destinatario: str | None,
    clave: str,
    estado: str = "PENDIENTE",
    error: str | None = None,
):
    item = NotificacionPago(
        pago_id=pago_id,
        tipo="COMPROBANTE_PAGO",
        estado=estado,
        destinatario=destinatario,
        clave_idempotencia=clave,
        proveedor="RESEND",
        intentos=0,
        ultimo_error=error,
    )
    db.add(item)
    db.flush()
    return item


def obtener_notificacion_por_proveedor_id(db: Session, proveedor_id: str, *, bloquear=False):
    stmt = select(NotificacionPago).where(
        NotificacionPago.proveedor_mensaje_id == proveedor_id,
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def reclamar_notificacion(db: Session, ahora: datetime, bloqueado_hasta: datetime):
    stmt = (
        select(NotificacionPago)
        .where(
            or_(
                (NotificacionPago.estado.in_(("PENDIENTE", "FALLIDO")))
                & or_(
                    NotificacionPago.proximo_intento.is_(None),
                    NotificacionPago.proximo_intento <= ahora,
                ),
                (NotificacionPago.estado == "PROCESANDO")
                & (NotificacionPago.bloqueado_hasta < ahora),
            ),
            NotificacionPago.destinatario.is_not(None),
            NotificacionPago.intentos < 8,
        )
        .order_by(NotificacionPago.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    item = db.scalar(stmt)
    if item is not None:
        item.estado = "PROCESANDO"
        item.intentos += 1
        item.bloqueado_hasta = bloqueado_hasta
        item.fecha_actualizacion = ahora
        db.flush()
    return item
