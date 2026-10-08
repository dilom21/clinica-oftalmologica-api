from collections.abc import Iterable

from sqlalchemy import exists, select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import (
    ConsultaClinica,
    HistorialClinico,
    ServicioRealizado,
)
from app.modules.gestion_pagos.models.models import Pago, PagoDetalle


def _carga_servicio():
    return (
        selectinload(ServicioRealizado.consulta_clinica)
        .selectinload(ConsultaClinica.oftalmologo)
    )


def obtener_servicios_realizados_por_ids(
    db: Session,
    servicio_realizado_ids: Iterable[int],
):
    ids = list(servicio_realizado_ids)
    if not ids:
        return []
    stmt = (
        select(ServicioRealizado)
        .options(selectinload(ServicioRealizado.servicio), _carga_servicio())
        .where(ServicioRealizado.id.in_(ids))
        .order_by(ServicioRealizado.id.asc())
    )
    return db.scalars(stmt).all()


def obtener_servicios_realizados_por_ids_para_actualizacion(
    db: Session,
    servicio_realizado_ids: Iterable[int],
):
    """Bloquea servicios en orden estable durante la transaccion actual."""
    ids = sorted(set(servicio_realizado_ids))
    if not ids:
        return []
    stmt = (
        select(ServicioRealizado)
        .options(selectinload(ServicioRealizado.servicio), _carga_servicio())
        .where(ServicioRealizado.id.in_(ids))
        .order_by(ServicioRealizado.id.asc())
        .with_for_update()
    )
    return db.scalars(stmt).all()


def listar_servicios_realizados_por_consulta(
    db: Session,
    consulta_clinica_id: int,
):
    stmt = (
        select(ServicioRealizado)
        .options(selectinload(ServicioRealizado.servicio), _carga_servicio())
        .where(ServicioRealizado.consulta_clinica_id == consulta_clinica_id)
        .order_by(
            ServicioRealizado.fecha_realizacion.desc(),
            ServicioRealizado.id.desc(),
        )
    )
    return db.scalars(stmt).all()


def listar_servicios_realizados_por_paciente(db: Session, paciente_id: int):
    stmt = (
        select(ServicioRealizado)
        .join(
            ConsultaClinica,
            ConsultaClinica.id == ServicioRealizado.consulta_clinica_id,
        )
        .join(
            HistorialClinico,
            HistorialClinico.id == ConsultaClinica.historial_clinico_id,
        )
        .options(selectinload(ServicioRealizado.servicio), _carga_servicio())
        .where(
            ServicioRealizado.paciente_id == paciente_id,
            HistorialClinico.paciente_id == paciente_id,
            ConsultaClinica.estado.is_(True),
            ServicioRealizado.consulta_clinica_id.is_not(None),
        )
        .order_by(
            ConsultaClinica.fecha_consulta.desc(),
            ConsultaClinica.id.desc(),
            ServicioRealizado.fecha_realizacion.desc(),
            ServicioRealizado.id.desc(),
        )
    )
    return db.scalars(stmt).all()


def obtener_ids_servicios_cubiertos_por_pago_aprobado(
    db: Session,
    servicio_realizado_ids: Iterable[int],
) -> set[int]:
    ids = list(servicio_realizado_ids)
    if not ids:
        return set()
    stmt = (
        select(PagoDetalle.servicio_realizado_id)
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id.in_(ids),
            Pago.estado == "APROBADO",
        )
        .distinct()
    )
    return set(db.scalars(stmt).all())


def servicio_cubierto_por_pago_aprobado(
    db: Session,
    servicio_realizado_id: int,
) -> bool:
    stmt = select(
        exists().where(
            PagoDetalle.servicio_realizado_id == servicio_realizado_id,
            PagoDetalle.pago_id == Pago.id,
            Pago.estado == "APROBADO",
        )
    )
    return bool(db.scalar(stmt))


def listar_servicios_pendientes_pago(
    db: Session,
    *,
    paciente_id: int | None = None,
    consulta_clinica_id: int | None = None,
):
    pago_aprobado = (
        select(PagoDetalle.id)
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id == ServicioRealizado.id,
            Pago.estado == "APROBADO",
        )
        .correlate(ServicioRealizado)
    )
    stmt = (
        select(ServicioRealizado)
        .options(selectinload(ServicioRealizado.servicio), _carga_servicio())
        .where(
            ServicioRealizado.estado.is_(True),
            ServicioRealizado.consulta_clinica_id.is_not(None),
            ServicioRealizado.precio_aplicado.is_not(None),
            ServicioRealizado.precio_aplicado > 0,
            ~exists(pago_aprobado),
        )
    )
    if paciente_id is not None:
        stmt = stmt.where(ServicioRealizado.paciente_id == paciente_id)
    if consulta_clinica_id is not None:
        stmt = stmt.where(
            ServicioRealizado.consulta_clinica_id == consulta_clinica_id,
        )
    stmt = stmt.order_by(
        ServicioRealizado.fecha_realizacion.desc(),
        ServicioRealizado.id.desc(),
    )
    return db.scalars(stmt).all()


def obtener_pago_por_id(db: Session, pago_id: int):
    return db.scalar(
        select(Pago)
        .options(selectinload(Pago.detalles))
        .where(Pago.id == pago_id)
    )


def obtener_pago_por_id_con_servicios(db: Session, pago_id: int):
    return db.scalar(
        select(Pago)
        .options(
            selectinload(Pago.detalles).selectinload(
                PagoDetalle.servicio_realizado,
            )
        )
        .where(Pago.id == pago_id)
        .execution_options(populate_existing=True)
    )


def obtener_pago_por_referencia_para_actualizacion(
    db: Session,
    referencia_transaccion: str,
):
    return db.scalar(
        select(Pago)
        .options(
            selectinload(Pago.detalles).selectinload(
                PagoDetalle.servicio_realizado,
            )
        )
        .where(Pago.referencia_transaccion == referencia_transaccion)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def listar_pagos_stripe_candidatos_reintento(
    db: Session,
    servicio_realizado_ids: Iterable[int],
):
    """Pagos locales que pueden reservar o conservar un intento cobrable."""
    ids = list(servicio_realizado_ids)
    if not ids:
        return []
    pagos_ids = select(PagoDetalle.pago_id).where(
        PagoDetalle.servicio_realizado_id.in_(ids),
    )
    stmt = (
        select(Pago)
        .options(selectinload(Pago.detalles))
        .where(
            Pago.id.in_(pagos_ids),
            Pago.estado.in_(("PENDIENTE", "RECHAZADO")),
            Pago.pasarela == "STRIPE",
        )
        .order_by(Pago.id.asc())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return db.scalars(stmt).all()


def obtener_pagos_aprobados_para_servicios_excluyendo(
    db: Session,
    servicio_realizado_ids: Iterable[int],
    *,
    pago_id_excluido: int,
) -> set[int]:
    ids = list(servicio_realizado_ids)
    if not ids:
        return set()
    stmt = (
        select(PagoDetalle.pago_id)
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(
            PagoDetalle.servicio_realizado_id.in_(ids),
            Pago.estado == "APROBADO",
            Pago.id != pago_id_excluido,
        )
        .distinct()
    )
    return set(db.scalars(stmt).all())


def crear_pago_stripe_pendiente(
    db: Session,
    *,
    monto,
    moneda: str,
):
    pago = Pago(
        monto=monto,
        moneda=moneda.upper(),
        metodo_pago="TARJETA",
        estado="PENDIENTE",
        pasarela="STRIPE",
        referencia_transaccion=None,
        fecha_hora_pago=None,
    )
    db.add(pago)
    db.flush()
    return pago


def crear_detalles_pago(
    db: Session,
    *,
    pago_id: int,
    servicios,
):
    detalles = [
        PagoDetalle(
            pago_id=pago_id,
            servicio_realizado_id=servicio.id,
            monto_aplicado=servicio.precio_aplicado,
        )
        for servicio in servicios
    ]
    db.add_all(detalles)
    db.flush()
    return detalles


def asignar_referencia_transaccion(
    db: Session,
    pago: Pago,
    referencia_transaccion: str,
):
    pago.referencia_transaccion = referencia_transaccion
    db.flush()
    return pago


def actualizar_estado_pago(
    db: Session,
    pago: Pago,
    *,
    estado: str,
    fecha_hora_pago=None,
):
    pago.estado = estado
    if fecha_hora_pago is not None:
        pago.fecha_hora_pago = fecha_hora_pago
    db.flush()
    return pago


def listar_detalles_pago(db: Session, pago_id: int):
    stmt = (
        select(PagoDetalle)
        .options(selectinload(PagoDetalle.servicio_realizado))
        .where(PagoDetalle.pago_id == pago_id)
        .order_by(PagoDetalle.id.asc())
    )
    return db.scalars(stmt).all()


def obtener_pago_por_referencia_transaccion(
    db: Session,
    referencia_transaccion: str,
):
    return db.scalar(
        select(Pago).where(
            Pago.referencia_transaccion == referencia_transaccion,
        )
    )


def listar_pagos_por_servicio_realizado(
    db: Session,
    servicio_realizado_id: int,
):
    stmt = (
        select(Pago)
        .join(PagoDetalle, PagoDetalle.pago_id == Pago.id)
        .where(PagoDetalle.servicio_realizado_id == servicio_realizado_id)
        .order_by(Pago.fecha_creacion.desc(), Pago.id.desc())
    )
    return db.scalars(stmt).all()


def consulta_pertenece_a_paciente(
    db: Session,
    consulta_clinica_id: int,
    paciente_id: int,
) -> bool:
    stmt = select(
        exists().where(
            ConsultaClinica.id == consulta_clinica_id,
            ConsultaClinica.historial_clinico_id == HistorialClinico.id,
            HistorialClinico.paciente_id == paciente_id,
        )
    )
    return bool(db.scalar(stmt))


# =========================================================
# ETAPA 8.1 - HISTORIAL Y COMPROBANTES (solo lectura)
# La pertenencia de un pago a un paciente se deriva de:
# pago_detalle -> servicio_realizado.paciente_id
# =========================================================


def listar_ids_pagos_de_paciente(db: Session, paciente_id: int) -> set[int]:
    """Identificadores de pagos que tienen al menos un detalle del paciente.

    Es solo un pre-filtro: el servicio verifica luego que *todos* los
    detalles del pago pertenezcan al paciente antes de exponerlo.
    """
    stmt = (
        select(PagoDetalle.pago_id)
        .join(
            ServicioRealizado,
            ServicioRealizado.id == PagoDetalle.servicio_realizado_id,
        )
        .where(ServicioRealizado.paciente_id == paciente_id)
        .distinct()
    )
    return set(db.scalars(stmt).all())


def obtener_pagos_con_detalles(db: Session, pago_ids: Iterable[int]):
    """Pagos con sus detalles y servicios cargados (sin N+1)."""
    ids = list(pago_ids)
    if not ids:
        return []
    stmt = (
        select(Pago)
        .options(
            selectinload(Pago.detalles)
            .selectinload(PagoDetalle.servicio_realizado)
            .selectinload(ServicioRealizado.servicio),
        )
        .where(Pago.id.in_(ids))
        .order_by(Pago.fecha_creacion.desc(), Pago.id.desc())
    )
    return db.scalars(stmt).all()


def obtener_pago_para_comprobante(db: Session, pago_id: int):
    """Pago con detalles, servicio del catalogo y consulta clinica cargados."""
    return db.scalar(
        select(Pago)
        .options(
            selectinload(Pago.detalles)
            .selectinload(PagoDetalle.servicio_realizado)
            .selectinload(ServicioRealizado.servicio),
            selectinload(Pago.detalles)
            .selectinload(PagoDetalle.servicio_realizado)
            .selectinload(ServicioRealizado.consulta_clinica),
        )
        .where(Pago.id == pago_id)
        .execution_options(populate_existing=True)
    )
