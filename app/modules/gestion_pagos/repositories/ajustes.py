"""CU36 - Persistencia del libro de ajustes financieros."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_pagos.models.models import (
    PagoAjuste,
    PagoAjusteDetalle,
)


def _carga_ajuste():
    return (
        selectinload(PagoAjuste.detalles)
        .selectinload(PagoAjusteDetalle.servicio_realizado),
    )


def obtener_ajuste_por_clave(
    db: Session, clave: str, *, bloquear: bool = False,
) -> PagoAjuste | None:
    stmt = (
        select(PagoAjuste)
        .options(*_carga_ajuste())
        .where(PagoAjuste.clave_idempotencia == clave)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def obtener_ajuste(
    db: Session, ajuste_id: int, *, bloquear: bool = False,
) -> PagoAjuste | None:
    stmt = (
        select(PagoAjuste)
        .options(*_carga_ajuste())
        .where(PagoAjuste.id == ajuste_id)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def obtener_ajuste_por_referencia(
    db: Session, referencia_externa: str,
) -> PagoAjuste | None:
    return db.scalar(
        select(PagoAjuste)
        .options(*_carga_ajuste())
        .where(PagoAjuste.referencia_externa == referencia_externa)
        .execution_options(populate_existing=True)
    )


def listar_ajustes_pago(
    db: Session, pago_id: int, *, estado: str | None = None,
) -> list[PagoAjuste]:
    filtros = [PagoAjuste.pago_id == pago_id]
    if estado is not None:
        filtros.append(PagoAjuste.estado == estado)
    stmt = (
        select(PagoAjuste)
        .options(*_carga_ajuste())
        .where(*filtros)
        .order_by(PagoAjuste.fecha_creacion.asc(), PagoAjuste.id.asc())
    )
    return list(db.scalars(stmt).all())


def crear_ajuste(
    db: Session,
    *,
    pago_id: int,
    tipo: str,
    naturaleza: str,
    efecto_caja: str,
    estado: str,
    importe,
    moneda: str,
    motivo: str,
    evidencia: str | None,
    clave_idempotencia: str,
    creado_por: int | None,
    revertir_aplicacion_cuota: bool = False,
    confirmado_por: int | None = None,
    fecha_confirmacion=None,
    observaciones: str | None = None,
) -> PagoAjuste:
    ajuste = PagoAjuste(
        pago_id=pago_id,
        tipo=tipo,
        naturaleza=naturaleza,
        efecto_caja=efecto_caja,
        estado=estado,
        importe=importe,
        moneda=moneda,
        motivo=motivo,
        evidencia=evidencia,
        clave_idempotencia=clave_idempotencia,
        revertir_aplicacion_cuota=revertir_aplicacion_cuota,
        creado_por=creado_por,
        confirmado_por=confirmado_por,
        fecha_confirmacion=fecha_confirmacion,
        observaciones=observaciones,
    )
    db.add(ajuste)
    db.flush()
    return ajuste


def crear_detalles(db: Session, ajuste_id: int, filas) -> list[PagoAjusteDetalle]:
    detalles = [
        PagoAjusteDetalle(
            ajuste_id=ajuste_id,
            servicio_realizado_id=servicio_id,
            monto=monto,
        )
        for servicio_id, monto in filas
    ]
    db.add_all(detalles)
    db.flush()
    return detalles


def detalles_por_ajuste(db: Session, ajuste_id: int) -> list[PagoAjusteDetalle]:
    return list(db.scalars(
        select(PagoAjusteDetalle)
        .options(selectinload(PagoAjusteDetalle.servicio_realizado))
        .where(PagoAjusteDetalle.ajuste_id == ajuste_id)
        .order_by(PagoAjusteDetalle.id.asc())
    ).all())


__all__ = [
    "crear_ajuste",
    "crear_detalles",
    "detalles_por_ajuste",
    "listar_ajustes_pago",
    "obtener_ajuste",
    "obtener_ajuste_por_clave",
    "obtener_ajuste_por_referencia",
]
