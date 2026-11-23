from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import (
    ServicioOftalmologico,
    ServicioRealizado,
)
from app.modules.gestion_pagos.models.models import Pago, PagoAjuste, PagoDetalle


def obtener_servicio(db: Session, servicio_id: int):
    return db.get(ServicioOftalmologico, servicio_id)


def _cargar_relaciones():
    return (
        selectinload(ServicioRealizado.servicio),
        selectinload(ServicioRealizado.paciente),
        selectinload(ServicioRealizado.oftalmologo),
    )


def obtener_registro(db: Session, registro_id: int, *, bloquear: bool = False):
    stmt = (
        select(ServicioRealizado)
        .options(*_cargar_relaciones())
        .where(ServicioRealizado.id == registro_id)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def guardar_registro(db: Session, registro: ServicioRealizado):
    db.add(registro)
    db.flush()
    # Carga relaciones dentro de la transacción, antes de serializar/confirmar.
    return obtener_registro(db, registro.id)


def tiene_pago_asociado(db: Session, registro_id: int) -> bool:
    """Indica si el servicio sigue comprometido por un pago.

    CU36: un pago PENDIENTE (posible captura), APROBADO (dinero recibido o
    cobertura vigente), un intento Stripe rechazado aun conciliable o cualquier
    ajuste financiero abierto siguen bloqueando. Un pago ANULADO o totalmente
    reembolsado ya no bloquea: el servicio vuelve a estar pendiente de pago.
    """
    comprometido = db.scalar(
        select(PagoDetalle.id)
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id == registro_id,
            or_(
                Pago.estado.in_(("PENDIENTE", "APROBADO")),
                (Pago.pasarela == "STRIPE") & (Pago.estado == "RECHAZADO"),
            ),
        )
        .limit(1)
    )
    if comprometido is not None:
        return True
    abierto = db.scalar(
        select(PagoAjuste.id)
        .join(PagoDetalle, PagoDetalle.pago_id == PagoAjuste.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id == registro_id,
            PagoAjuste.estado.in_(("PENDIENTE_PROVEEDOR", "DESCONOCIDO")),
        )
        .limit(1)
    )
    return abierto is not None


def listar_registros(
    db: Session, *, paciente_id=None, servicio_id=None, consulta_clinica_id=None,
    oftalmologo_id=None, estado=True, desde=None, hasta=None, page=1, page_size=20,
):
    filtros = []
    for columna, valor in (
        (ServicioRealizado.paciente_id, paciente_id),
        (ServicioRealizado.servicio_id, servicio_id),
        (ServicioRealizado.consulta_clinica_id, consulta_clinica_id),
        (ServicioRealizado.oftalmologo_id, oftalmologo_id),
        (ServicioRealizado.estado, estado),
    ):
        if valor is not None:
            filtros.append(columna == valor)
    if desde is not None:
        filtros.append(ServicioRealizado.fecha_realizacion >= desde)
    if hasta is not None:
        filtros.append(ServicioRealizado.fecha_realizacion <= hasta)

    total = db.scalar(select(func.count()).select_from(ServicioRealizado).where(*filtros))
    stmt = (
        select(ServicioRealizado)
        .options(*_cargar_relaciones())
        .where(*filtros)
        .order_by(
            ServicioRealizado.fecha_realizacion.desc().nulls_last(),
            ServicioRealizado.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return db.scalars(stmt).all(), total
