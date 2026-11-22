"""CU36 - Persistencia de webhooks Stripe huerfanos (E-3).

Un evento huerfano es un refund verificado por firma que no pudo resolverse a
un tenant/ajuste (por ejemplo, un refund creado desde el dashboard de Stripe).
Se guarda antes de responder a Stripe y solo puede atribuirse manualmente con
un refund_id explicito, evidencia y rol administrador.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.gestion_pagos.models.models import StripeWebhookHuerfano


def registrar_huerfano(
    db: Session,
    *,
    stripe_event_id: str,
    event_type: str,
    refund_id: str,
    payment_intent_id: str | None,
    amount: int,
    currency: str,
    status: str,
    evento: dict,
    motivo: str,
) -> StripeWebhookHuerfano:
    """Inserta el evento de forma idempotente por ``stripe_event_id``.

    Debe invocarse al inicio de la transaccion del webhook: si la clave de
    evento ya existe se descarta lo pendiente y se devuelve el registro previo.
    """
    existente = db.scalar(
        select(StripeWebhookHuerfano).where(
            StripeWebhookHuerfano.stripe_event_id == stripe_event_id,
        )
    )
    if existente is not None:
        return existente

    registro = StripeWebhookHuerfano(
        stripe_event_id=stripe_event_id,
        event_type=event_type,
        refund_id=refund_id,
        payment_intent_id=payment_intent_id,
        amount=amount,
        currency=currency,
        status=status,
        payload=json.dumps(evento, sort_keys=True, ensure_ascii=True, default=str),
        estado="PENDIENTE",
        motivo=motivo,
    )
    db.add(registro)
    try:
        db.flush()
    except IntegrityError:
        # Otro proceso registro el mismo evento en paralelo.
        db.rollback()
        registro = db.scalar(
            select(StripeWebhookHuerfano).where(
                StripeWebhookHuerfano.stripe_event_id == stripe_event_id,
            )
        )
        if registro is None:
            raise
    return registro


def obtener_huerfano(
    db: Session, huerfano_id: int, *, bloquear: bool = False,
) -> StripeWebhookHuerfano | None:
    stmt = (
        select(StripeWebhookHuerfano)
        .where(StripeWebhookHuerfano.id == huerfano_id)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def listar_pendientes(
    db: Session, *, limite: int = 100,
) -> list[StripeWebhookHuerfano]:
    return list(db.scalars(
        select(StripeWebhookHuerfano)
        .where(StripeWebhookHuerfano.estado == "PENDIENTE")
        .order_by(
            StripeWebhookHuerfano.creado_en.asc(),
            StripeWebhookHuerfano.id.asc(),
        )
        .limit(limite)
    ).all())


def obtener_por_refund(
    db: Session, refund_id: str,
) -> list[StripeWebhookHuerfano]:
    return list(db.scalars(
        select(StripeWebhookHuerfano)
        .where(StripeWebhookHuerfano.refund_id == refund_id)
        .order_by(StripeWebhookHuerfano.id.asc())
    ).all())


def marcar_atribuido(
    db: Session,
    huerfano: StripeWebhookHuerfano,
    *,
    ajuste_id: int,
    motivo: str,
) -> None:
    from datetime import datetime, timezone

    huerfano.estado = "ATRIBUIDO"
    huerfano.ajuste_id = ajuste_id
    huerfano.motivo = motivo
    huerfano.actualizado_en = datetime.now(timezone.utc)
    db.flush()


def marcar_atribuidos_por_refund(
    db: Session,
    refund_id: str,
    *,
    ajuste_id: int,
    motivo: str,
) -> int:
    total = 0
    for huerfano in obtener_por_refund(db, refund_id):
        if huerfano.estado == "PENDIENTE":
            marcar_atribuido(db, huerfano, ajuste_id=ajuste_id, motivo=motivo)
            total += 1
    return total


def obtener_ajuste_por_refund(db: Session, refund_id: str):
    from app.modules.gestion_pagos.models.models import PagoAjuste

    return db.scalar(
        select(PagoAjuste).where(
            PagoAjuste.referencia_externa == refund_id,
        )
    )


# --------------------------------------------------------------------------
# I-2: cuarentena central (Control Plane) para refunds sin tenant resoluble.
# Nunca se escribe en la base legacy: el Control Plane es el unico destino
# cuando el PaymentIntent no permite demostrar a que empresa pertenece.
# --------------------------------------------------------------------------
def registrar_huerfano_central(
    db: Session,
    *,
    stripe_event_id: str,
    event_type: str,
    refund_id: str,
    payment_intent_id: str | None,
    amount: int,
    currency: str,
    status: str,
    evento: dict,
    motivo: str,
):
    from app.modules.administracion_saas.models import StripeWebhookHuerfanoCentral

    existente = db.scalar(
        select(StripeWebhookHuerfanoCentral).where(
            StripeWebhookHuerfanoCentral.stripe_event_id == stripe_event_id,
        )
    )
    if existente is not None:
        return existente
    registro = StripeWebhookHuerfanoCentral(
        stripe_event_id=stripe_event_id,
        event_type=event_type,
        refund_id=refund_id,
        payment_intent_id=payment_intent_id,
        amount=amount,
        currency=currency,
        status=status,
        payload=json.dumps(evento, sort_keys=True, ensure_ascii=True, default=str),
        estado="PENDIENTE",
        motivo=motivo,
    )
    db.add(registro)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        registro = db.scalar(
            select(StripeWebhookHuerfanoCentral).where(
                StripeWebhookHuerfanoCentral.stripe_event_id == stripe_event_id,
            )
        )
        if registro is None:
            raise
    return registro


def obtener_huerfano_central(
    db: Session, huerfano_id: int, *, bloquear: bool = False,
):
    from app.modules.administracion_saas.models import StripeWebhookHuerfanoCentral

    stmt = (
        select(StripeWebhookHuerfanoCentral)
        .where(StripeWebhookHuerfanoCentral.id == huerfano_id)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def listar_pendientes_central(db: Session, *, limite: int = 100):
    from app.modules.administracion_saas.models import StripeWebhookHuerfanoCentral

    return list(db.scalars(
        select(StripeWebhookHuerfanoCentral)
        .where(StripeWebhookHuerfanoCentral.estado == "PENDIENTE")
        .order_by(
            StripeWebhookHuerfanoCentral.creado_en.asc(),
            StripeWebhookHuerfanoCentral.id.asc(),
        )
        .limit(limite)
    ).all())


def marcar_atribuido_central(
    db: Session,
    huerfano,
    *,
    empresa_id: int,
    tenant_database_id: int,
    motivo: str,
    usuario_id: int | None,
    ajuste_id: int | None = None,
) -> None:
    from datetime import datetime, timezone

    huerfano.estado = "ATRIBUIDO"
    huerfano.empresa_id = empresa_id
    huerfano.tenant_database_id = tenant_database_id
    if ajuste_id is not None:
        huerfano.ajuste_id = ajuste_id
    huerfano.motivo = motivo
    huerfano.atribuido_por = usuario_id
    huerfano.actualizado_en = datetime.now(timezone.utc)
    db.flush()


__all__ = [
    "listar_pendientes",
    "listar_pendientes_central",
    "marcar_atribuido",
    "marcar_atribuido_central",
    "marcar_atribuidos_por_refund",
    "obtener_ajuste_por_refund",
    "obtener_huerfano",
    "obtener_huerfano_central",
    "obtener_por_refund",
    "registrar_huerfano",
    "registrar_huerfano_central",
]
