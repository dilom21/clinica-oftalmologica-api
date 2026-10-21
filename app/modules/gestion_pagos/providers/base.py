from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


ESTADOS_INTENCION_REUTILIZABLES = frozenset(
    {
        "requires_payment_method",
        "requires_confirmation",
        "requires_action",
        "requires_capture",
        "processing",
    }
)
ESTADOS_INTENCION_TERMINALES = frozenset({"succeeded", "canceled"})


def es_intencion_reutilizable(estado: str) -> bool:
    return estado in ESTADOS_INTENCION_REUTILIZABLES


def es_intencion_terminal(estado: str) -> bool:
    return estado in ESTADOS_INTENCION_TERMINALES


class ProveedorNoConfigurado(RuntimeError):
    pass


class ErrorProveedorPago(RuntimeError):
    pass


class ErrorFirmaWebhook(ValueError):
    pass


@dataclass(frozen=True)
class IntentoPagoProveedor:
    id: str
    client_secret: str | None
    amount: int
    currency: str
    status: str


class ProveedorPagoBase(ABC):
    @abstractmethod
    def validar_configuracion(self, *, webhook: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def crear_intencion(
        self,
        *,
        amount: int,
        currency: str,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> IntentoPagoProveedor:
        raise NotImplementedError

    @abstractmethod
    def recuperar_intencion(self, payment_intent_id: str) -> IntentoPagoProveedor:
        raise NotImplementedError

    @abstractmethod
    def cancelar_intencion(self, payment_intent_id: str) -> IntentoPagoProveedor:
        raise NotImplementedError

    @abstractmethod
    def verificar_webhook(
        self,
        payload: bytes,
        firma: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError
