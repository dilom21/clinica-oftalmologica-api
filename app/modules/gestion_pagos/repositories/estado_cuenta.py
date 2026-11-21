from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_pagos.models.models import (
    Cuota,
    IntentoPagoCuota,
    Pago,
    PagoDetalle,
    PlanPago,
)
from app.modules.gestion_pagos.repositories import repository as pagos_repo


def obtener_paciente(db: Session, paciente_id: int):
    return db.scalar(select(Paciente).where(Paciente.id == paciente_id))


def listar_servicios(db: Session, paciente_id: int):
    # Reuse the established patient/clinical-history ownership join so legacy
    # rows whose service patient conflicts with the consultation are not
    # exposed or charged to the wrong patient.
    return pagos_repo.listar_servicios_realizados_por_paciente(db, paciente_id)


def listar_planes(db: Session, paciente_id: int):
    stmt = (
        select(PlanPago)
        .options(
            selectinload(PlanPago.servicios),
            selectinload(PlanPago.cuotas),
        )
        .where(PlanPago.paciente_id == paciente_id)
        .order_by(PlanPago.fecha_creacion, PlanPago.id)
    )
    return list(db.scalars(stmt).all())


def pagos_y_detalles(db: Session, servicio_ids: list[int]):
    if not servicio_ids:
        return []
    stmt = (
        select(PagoDetalle, Pago, IntentoPagoCuota)
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .outerjoin(IntentoPagoCuota, IntentoPagoCuota.pago_id == Pago.id)
        .where(PagoDetalle.servicio_realizado_id.in_(servicio_ids))
        .order_by(Pago.id, PagoDetalle.id)
    )
    return list(db.execute(stmt).all())


def listar_intentos_paciente(db: Session, paciente_id: int):
    stmt = (
        select(IntentoPagoCuota, Pago, Cuota, PlanPago)
        .join(Pago, Pago.id == IntentoPagoCuota.pago_id)
        .join(Cuota, Cuota.id == IntentoPagoCuota.cuota_id)
        .join(PlanPago, PlanPago.id == Cuota.plan_pago_id)
        .where(PlanPago.paciente_id == paciente_id)
        .order_by(Pago.id)
    )
    return list(db.execute(stmt).all())
