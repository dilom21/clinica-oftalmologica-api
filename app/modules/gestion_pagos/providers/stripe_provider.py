from typing import Any

import stripe

from app.core import config
from app.modules.gestion_pagos.providers.base import (
    ErrorFirmaWebhook,
    ErrorProveedorPago,
    IntentoPagoProveedor,
    ProveedorNoConfigurado,
    ProveedorPagoBase,
)


class StripeProveedor(ProveedorPagoBase):
    def __init__(
        self,
        *,
        secret_key: str | None = None,
        webhook_secret: str | None = None,
    ):
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret

    def _clave_api(self) -> str:
        clave = self._secret_key or config.STRIPE_SECRET_KEY
        if not clave:
            raise ProveedorNoConfigurado("Stripe no esta configurado")
        return clave

    def _secreto_webhook(self) -> str:
        secreto = self._webhook_secret or config.STRIPE_WEBHOOK_SECRET
        if not secreto:
            raise ProveedorNoConfigurado("El webhook de Stripe no esta configurado")
        return secreto

    def validar_configuracion(self, *, webhook: bool = False) -> None:
        if webhook:
            self._secreto_webhook()
        else:
            self._clave_api()

    @staticmethod
    def _normalizar_intencion(objeto) -> IntentoPagoProveedor:
        return IntentoPagoProveedor(
            id=objeto.id,
            client_secret=getattr(objeto, "client_secret", None),
            amount=int(objeto.amount),
            currency=str(objeto.currency).lower(),
            status=str(objeto.status),
        )

    def crear_intencion(
        self,
        *,
        amount: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> IntentoPagoProveedor:
        try:
            intento = stripe.PaymentIntent.create(
                amount=amount,
                currency=currency,
                automatic_payment_methods={"enabled": True},
                metadata=metadata,
                api_key=self._clave_api(),
                idempotency_key=idempotency_key,
            )
            return self._normalizar_intencion(intento)
        except ProveedorNoConfigurado:
            raise
        except stripe.StripeError as error:
            raise ErrorProveedorPago(
                "No fue posible crear la intencion de pago en Stripe"
            ) from error

    def recuperar_intencion(self, payment_intent_id: str) -> IntentoPagoProveedor:
        try:
            intento = stripe.PaymentIntent.retrieve(
                payment_intent_id,
                api_key=self._clave_api(),
            )
            return self._normalizar_intencion(intento)
        except ProveedorNoConfigurado:
            raise
        except stripe.StripeError as error:
            raise ErrorProveedorPago(
                "No fue posible recuperar la intencion de pago de Stripe"
            ) from error

    def cancelar_intencion(self, payment_intent_id: str) -> IntentoPagoProveedor:
        try:
            intento = stripe.PaymentIntent.retrieve(
                payment_intent_id,
                api_key=self._clave_api(),
            )
            cancelado = intento.cancel(api_key=self._clave_api())
            return self._normalizar_intencion(cancelado)
        except ProveedorNoConfigurado:
            raise
        except stripe.StripeError as error:
            raise ErrorProveedorPago(
                "No fue posible cancelar la intencion de pago en Stripe"
            ) from error

    def verificar_webhook(
        self,
        payload: bytes,
        firma: str | None,
    ) -> dict[str, Any]:
        try:
            evento = stripe.Webhook.construct_event(
                payload,
                firma,
                self._secreto_webhook(),
            )
            return evento.to_dict()
        except ProveedorNoConfigurado:
            raise
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise ErrorFirmaWebhook("Firma webhook Stripe invalida") from error


def obtener_proveedor_stripe() -> ProveedorPagoBase:
    return StripeProveedor()
