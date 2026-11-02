from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_pagos.models.models import (
    Cuota,
    IntentoPagoCuota,
    Pago,
    PagoDetalle,
    PlanPago,
    PlanPagoServicio,
)


def _carga_plan():
    return (
        selectinload(PlanPago.servicios)
        .selectinload(PlanPagoServicio.servicio_realizado)
        .selectinload(ServicioRealizado.servicio),
        selectinload(PlanPago.cuotas)
        .selectinload(Cuota.intentos)
        .selectinload(IntentoPagoCuota.pago),
    )


def obtener_plan_por_clave(db: Session, clave: str, *, bloquear: bool = False):
    stmt = select(PlanPago).options(*_carga_plan()).where(
        PlanPago.clave_idempotencia == clave,
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt.execution_options(populate_existing=True))


def obtener_plan(db: Session, plan_id: int, *, bloquear: bool = False):
    stmt = select(PlanPago).options(*_carga_plan()).where(PlanPago.id == plan_id)
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt.execution_options(populate_existing=True))


def listar_planes(
    db: Session,
    *,
    paciente_id: int | None = None,
    estado: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    filtros = []
    if paciente_id is not None:
        filtros.append(PlanPago.paciente_id == paciente_id)
    if estado is not None:
        filtros.append(PlanPago.estado == estado)
    total = db.scalar(select(func.count()).select_from(PlanPago).where(*filtros)) or 0
    stmt = (
        select(PlanPago)
        .options(*_carga_plan())
        .where(*filtros)
        .order_by(PlanPago.fecha_creacion.desc(), PlanPago.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(db.scalars(stmt).all()), int(total)


def crear_plan(
    db: Session,
    *,
    paciente_id: int,
    consulta_clinica_id: int,
    monto_total,
    cantidad_cuotas: int,
    observaciones: str | None,
    creado_por: int,
    clave_idempotencia: str,
):
    plan = PlanPago(
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
        monto_total=monto_total,
        moneda="BOB",
        cantidad_cuotas=cantidad_cuotas,
        estado="ACTIVO",
        observaciones=observaciones,
        creado_por=creado_por,
        clave_idempotencia=clave_idempotencia,
    )
    db.add(plan)
    db.flush()
    return plan


def crear_servicios_plan(db: Session, plan_id: int, servicios):
    filas = [
        PlanPagoServicio(
            plan_pago_id=plan_id,
            servicio_realizado_id=servicio.id,
            monto_financiado=servicio.precio_aplicado,
            activo=True,
        )
        for servicio in servicios
    ]
    db.add_all(filas)
    db.flush()
    return filas


def crear_cuotas(db: Session, plan_id: int, cuotas):
    filas = [
        Cuota(
            plan_pago_id=plan_id,
            numero_cuota=item.numero_cuota,
            monto_programado=item.monto_programado,
            fecha_vencimiento=item.fecha_vencimiento,
            estado="PENDIENTE",
        )
        for item in sorted(cuotas, key=lambda item: item.numero_cuota)
    ]
    db.add_all(filas)
    db.flush()
    return filas


def reservas_activas_por_servicio(
    db: Session,
    servicio_ids: Iterable[int],
) -> dict[int, int]:
    ids = list(servicio_ids)
    if not ids:
        return {}
    filas = db.execute(
        select(PlanPagoServicio.servicio_realizado_id, PlanPagoServicio.plan_pago_id)
        .where(
            PlanPagoServicio.servicio_realizado_id.in_(ids),
            PlanPagoServicio.activo.is_(True),
        )
    ).all()
    return {servicio_id: plan_id for servicio_id, plan_id in filas}


def montos_aprobados_por_servicio(
    db: Session,
    servicio_ids: Iterable[int],
) -> dict[int, object]:
    ids = list(servicio_ids)
    if not ids:
        return {}
    filas = db.execute(
        select(
            PagoDetalle.servicio_realizado_id,
            func.coalesce(func.sum(PagoDetalle.monto_aplicado), 0),
        )
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id.in_(ids),
            Pago.estado == "APROBADO",
        )
        .group_by(PagoDetalle.servicio_realizado_id)
    ).all()
    return dict(filas)


def obtener_cuota(db: Session, cuota_id: int, *, bloquear: bool = False):
    stmt = (
        select(Cuota)
        .options(
            selectinload(Cuota.plan)
            .selectinload(PlanPago.servicios)
            .selectinload(PlanPagoServicio.servicio_realizado)
            .selectinload(ServicioRealizado.servicio),
            selectinload(Cuota.plan).selectinload(PlanPago.cuotas),
            selectinload(Cuota.intentos).selectinload(IntentoPagoCuota.pago),
        )
        .where(Cuota.id == cuota_id)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt.execution_options(populate_existing=True))


def bloquear_plan_y_cuota(db: Session, cuota_id: int):
    """Lock order financiero: plan primero, cuota despues."""
    plan_id = db.scalar(select(Cuota.plan_pago_id).where(Cuota.id == cuota_id))
    if plan_id is None:
        return None
    db.scalar(
        select(PlanPago)
        .where(PlanPago.id == plan_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return obtener_cuota(db, cuota_id, bloquear=True)


def bloquear_cuotas_plan(db: Session, plan_id: int):
    return list(db.scalars(
        select(Cuota)
        .where(Cuota.plan_pago_id == plan_id)
        .order_by(Cuota.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all())


def obtener_intento_por_pago(db: Session, pago_id: int, *, bloquear=False):
    stmt = (
        select(IntentoPagoCuota)
        .options(
            selectinload(IntentoPagoCuota.pago).selectinload(Pago.detalles),
            selectinload(IntentoPagoCuota.cuota)
            .selectinload(Cuota.plan)
            .selectinload(PlanPago.cuotas),
            selectinload(IntentoPagoCuota.cuota)
            .selectinload(Cuota.plan)
            .selectinload(PlanPago.servicios),
        )
        .where(IntentoPagoCuota.pago_id == pago_id)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt.execution_options(populate_existing=True))


def bloquear_operacion_por_pago(db: Session, pago_id: int):
    """Lock a financed payment using the global plan -> cuota -> intento -> pago order."""
    cuota_id = db.scalar(
        select(IntentoPagoCuota.cuota_id)
        .where(IntentoPagoCuota.pago_id == pago_id)
    )
    if cuota_id is None:
        return None, None, None
    cuota = bloquear_plan_y_cuota(db, cuota_id)
    intento = obtener_intento_por_pago(db, pago_id, bloquear=True)
    pago = db.scalar(
        select(Pago)
        .options(selectinload(Pago.detalles))
        .where(Pago.id == pago_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return cuota, intento, pago


def obtener_intento_por_clave(db: Session, clave: str, *, bloquear=False):
    stmt = (
        select(IntentoPagoCuota)
        .join(Pago, Pago.id == IntentoPagoCuota.pago_id)
        .options(selectinload(IntentoPagoCuota.pago), selectinload(IntentoPagoCuota.cuota))
        .where(Pago.clave_idempotencia == clave)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt.execution_options(populate_existing=True))


def crear_intento(db: Session, cuota_id: int, pago_id: int):
    intento = IntentoPagoCuota(
        cuota_id=cuota_id,
        pago_id=pago_id,
        aplicado_a_cuota=False,
    )
    db.add(intento)
    db.flush()
    return intento


def intentos_potencialmente_cobrables(cuota: Cuota, *, excluir_pago_id=None):
    return [
        item for item in cuota.intentos
        if item.pago_id != excluir_pago_id
        and (
            item.pago.estado in {"PENDIENTE", "APROBADO"}
            or (item.pago.pasarela == "STRIPE" and item.pago.estado == "RECHAZADO")
        )
    ]


def cancelar_plan(db: Session, plan: PlanPago):
    plan.estado = "CANCELADO"
    for cuota in plan.cuotas:
        if cuota.estado == "PENDIENTE":
            cuota.estado = "CANCELADA"
    for servicio in plan.servicios:
        servicio.activo = False
    db.flush()
    return plan


def completar_plan_si_corresponde(db: Session, plan: PlanPago) -> bool:
    if plan.estado == "ACTIVO" and plan.cuotas and all(
        cuota.estado == "PAGADA" for cuota in plan.cuotas
    ):
        plan.estado = "COMPLETADO"
        db.flush()
        return True
    return False
