"""CU36 - Unico calculo reutilizable de importe vigente de pagos.

Invariantes (D2):

1. El importe base (`pago.monto` / `pago_detalle.monto_aplicado`) se cuenta
   una sola vez y solo mientras el pago representa dinero capturado
   (`APROBADO` o `REEMBOLSADO`).
2. Solo los ajustes `CONFIRMADO` modifican el importe vigente.
3. Un pago `ANULADO` por ausencia de captura aporta cero y nunca recibe
   ademas un ajuste negativo: su ajuste de anulacion vale cero.
4. Un pago `REEMBOLSADO` conserva la base historica y se compensa con ajustes
   negativos confirmados hasta llegar a cero (la base no se excluye).
5. Un reembolso parcial deja un importe vigente positivo y el pago `APROBADO`.
6. Un ajuste pendiente, fallido o desconocido no se contabiliza.

Ningun consumidor debe volver a sumar `monto_aplicado` de pagos `APROBADO`
por su cuenta: todos usan las funciones de este modulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.modules.gestion_pagos.models.models import (
    ESTADOS_AJUSTE_ABIERTOS,
    ESTADOS_PAGO_CAPTURADO,
    Pago,
    PagoAjuste,
    PagoAjusteDetalle,
    PagoDetalle,
)


CERO = Decimal("0.00")


def _decimal(valor) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor if valor is not None else 0))


def expresion_monto_base(monto_columna, estado_columna):
    """Importe base contabilizado: cero si el pago no representa captura."""
    return case(
        (estado_columna.in_(ESTADOS_PAGO_CAPTURADO), monto_columna),
        else_=0,
    )


def expresion_vigente_por_servicio(servicio_realizado_id_columna):
    """Expresion SQL correlacionada del importe vigente de un servicio."""
    base = (
        select(
            func.coalesce(
                func.sum(
                    expresion_monto_base(
                        PagoDetalle.monto_aplicado, Pago.estado,
                    )
                ),
                0,
            )
        )
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(PagoDetalle.servicio_realizado_id == servicio_realizado_id_columna)
        .scalar_subquery()
    )
    ajustes = (
        select(func.coalesce(func.sum(PagoAjusteDetalle.monto), 0))
        .join(PagoAjuste, PagoAjuste.id == PagoAjusteDetalle.ajuste_id)
        .where(
            PagoAjusteDetalle.servicio_realizado_id == servicio_realizado_id_columna,
            PagoAjuste.estado == "CONFIRMADO",
        )
        .scalar_subquery()
    )
    return base + ajustes


def montos_vigentes_por_servicio(
    db: Session,
    servicio_realizado_ids,
) -> dict[int, Decimal]:
    """Importe vigente por servicio realizado (una sola consulta)."""
    ids = [int(item) for item in servicio_realizado_ids]
    if not ids:
        return {}
    filas = db.execute(
        select(PagoDetalle.servicio_realizado_id)
        .where(PagoDetalle.servicio_realizado_id.in_(ids))
        .distinct()
    ).all()
    presentes = {fila[0] for fila in filas}
    resultado = {servicio_id: CERO for servicio_id in ids}
    if not presentes:
        return resultado

    base = dict(
        db.execute(
            select(
                PagoDetalle.servicio_realizado_id,
                func.coalesce(
                    func.sum(
                        expresion_monto_base(
                            PagoDetalle.monto_aplicado, Pago.estado,
                        )
                    ),
                    0,
                ),
            )
            .join(Pago, Pago.id == PagoDetalle.pago_id)
            .where(PagoDetalle.servicio_realizado_id.in_(presentes))
            .group_by(PagoDetalle.servicio_realizado_id)
        ).all()
    )
    ajustes = dict(
        db.execute(
            select(
                PagoAjusteDetalle.servicio_realizado_id,
                func.coalesce(func.sum(PagoAjusteDetalle.monto), 0),
            )
            .join(PagoAjuste, PagoAjuste.id == PagoAjusteDetalle.ajuste_id)
            .where(
                PagoAjusteDetalle.servicio_realizado_id.in_(presentes),
                PagoAjuste.estado == "CONFIRMADO",
            )
            .group_by(PagoAjusteDetalle.servicio_realizado_id)
        ).all()
    )
    for servicio_id in presentes:
        resultado[servicio_id] = (
            _decimal(base.get(servicio_id, 0))
            + _decimal(ajustes.get(servicio_id, 0))
        )
    return resultado


def ajustes_confirmados_pago(db: Session, pago_id: int) -> Decimal:
    return _decimal(db.scalar(
        select(func.coalesce(func.sum(PagoAjuste.importe), 0)).where(
            PagoAjuste.pago_id == pago_id,
            PagoAjuste.estado == "CONFIRMADO",
        )
    ))


def monto_base_pago(pago: Pago) -> Decimal:
    if pago.estado in ESTADOS_PAGO_CAPTURADO:
        return _decimal(pago.monto)
    return CERO


def monto_vigente_pago(db: Session, pago: Pago) -> Decimal:
    return monto_base_pago(pago) + ajustes_confirmados_pago(db, pago.id)


def reembolsado_confirmado_pago(db: Session, pago_id: int) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(PagoAjuste.importe), 0)).where(
            PagoAjuste.pago_id == pago_id,
            PagoAjuste.estado == "CONFIRMADO",
            PagoAjuste.tipo.in_(("DEVOLUCION", "REEMBOLSO")),
        )
    )
    return abs(_decimal(total))


def desglose_vigente_pago(db: Session, pago: Pago) -> dict[int, Decimal]:
    """Importe vigente por servicio de un pago concreto."""
    desglose: dict[int, Decimal] = {}
    for detalle in pago.detalles:
        base = (
            _decimal(detalle.monto_aplicado)
            if pago.estado in ESTADOS_PAGO_CAPTURADO
            else CERO
        )
        desglose[detalle.servicio_realizado_id] = base
    if not desglose:
        return desglose
    filas = db.execute(
        select(
            PagoAjusteDetalle.servicio_realizado_id,
            func.coalesce(func.sum(PagoAjusteDetalle.monto), 0),
        )
        .join(PagoAjuste, PagoAjuste.id == PagoAjusteDetalle.ajuste_id)
        .where(
            PagoAjuste.pago_id == pago.id,
            PagoAjuste.estado == "CONFIRMADO",
        )
        .group_by(PagoAjusteDetalle.servicio_realizado_id)
    ).all()
    for servicio_id, monto in filas:
        desglose[servicio_id] = (
            desglose.get(servicio_id, CERO) + _decimal(monto)
        )
    return desglose


def pago_tiene_ajuste_abierto(db: Session, pago_id: int) -> bool:
    return bool(db.scalar(
        select(PagoAjuste.id).where(
            PagoAjuste.pago_id == pago_id,
            PagoAjuste.estado.in_(ESTADOS_AJUSTE_ABIERTOS),
        ).limit(1)
    ))


def ids_pagos_con_ajuste_abierto(db: Session, pago_ids) -> set[int]:
    ids = [int(item) for item in pago_ids]
    if not ids:
        return set()
    return set(db.scalars(
        select(PagoAjuste.pago_id).where(
            PagoAjuste.pago_id.in_(ids),
            PagoAjuste.estado.in_(ESTADOS_AJUSTE_ABIERTOS),
        ).distinct()
    ).all())


@dataclass(frozen=True)
class ResumenImportes:
    base: Decimal
    ajustes_confirmados: Decimal
    vigente: Decimal
    reembolsado: Decimal
    ajuste_abierto: bool


def resumenes_por_pago(db: Session, pago_ids) -> dict[int, ResumenImportes]:
    """Resumen financiero en lote para listados sin N+1."""
    ids = [int(item) for item in pago_ids]
    if not ids:
        return {}
    base: dict[int, Decimal] = {}
    for pago_id, monto, estado in db.execute(
        select(Pago.id, Pago.monto, Pago.estado).where(Pago.id.in_(ids))
    ).all():
        base[pago_id] = (
            _decimal(monto) if estado in ESTADOS_PAGO_CAPTURADO else CERO
        )

    confirmados: dict[int, Decimal] = {}
    reembolsados: dict[int, Decimal] = {}
    abiertos: set[int] = set()
    for pago_id, tipo, estado, total in db.execute(
        select(
            PagoAjuste.pago_id,
            PagoAjuste.tipo,
            PagoAjuste.estado,
            func.coalesce(func.sum(PagoAjuste.importe), 0),
        )
        .where(PagoAjuste.pago_id.in_(ids))
        .group_by(PagoAjuste.pago_id, PagoAjuste.tipo, PagoAjuste.estado)
    ).all():
        if estado in ESTADOS_AJUSTE_ABIERTOS:
            abiertos.add(pago_id)
        if estado != "CONFIRMADO":
            continue
        confirmados[pago_id] = confirmados.get(pago_id, CERO) + _decimal(total)
        if tipo in {"DEVOLUCION", "REEMBOLSO"}:
            reembolsados[pago_id] = (
                reembolsados.get(pago_id, CERO) + abs(_decimal(total))
            )

    return {
        pago_id: ResumenImportes(
            base=base.get(pago_id, CERO),
            ajustes_confirmados=confirmados.get(pago_id, CERO),
            vigente=base.get(pago_id, CERO) + confirmados.get(pago_id, CERO),
            reembolsado=reembolsados.get(pago_id, CERO),
            ajuste_abierto=pago_id in abiertos,
        )
        for pago_id in ids
    }


def resumen_pago(db: Session, pago: Pago) -> ResumenImportes:
    return resumenes_por_pago(db, [pago.id])[pago.id]


__all__ = [
    "CERO",
    "ResumenImportes",
    "ajustes_confirmados_pago",
    "desglose_vigente_pago",
    "expresion_monto_base",
    "expresion_vigente_por_servicio",
    "ids_pagos_con_ajuste_abierto",
    "monto_base_pago",
    "monto_vigente_pago",
    "montos_vigentes_por_servicio",
    "pago_tiene_ajuste_abierto",
    "reembolsado_confirmado_pago",
    "resumen_pago",
    "resumenes_por_pago",
]
