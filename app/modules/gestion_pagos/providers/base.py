from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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


class ErrorProveedorPagoDesconocido(ErrorProveedorPago):
    """La operacion externa pudo ejecutarse pero su resultado es desconocido."""


class ErrorFirmaWebhook(ValueError):
    pass


@dataclass(frozen=True)
class IntentoPagoProveedor:
    id: str
    client_secret: str | None
    amount: int
    currency: str
    status: str
    # Metadata confiable generada por el servidor al crear el PaymentIntent.
    # Permite enrutar un refund creado en el dashboard de Stripe (cuyo objeto
    # refund no lleva metadata) al tenant correcto consultando el proveedor.
    metadata: dict[str, str] = field(default_factory=dict, hash=False)


@dataclass(frozen=True)
class ReembolsoProveedor:
    id: str
    payment_intent_id: str | None
    amount: int
    currency: str
    status: str
    failure_reason: str | None = None
    # Metadata con la que se creo el reembolso. Permite atribuir un refund a
    # un ajuste concreto sin depender del orden en que el proveedor lo liste.
    metadata: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def confirmed(self) -> bool:
        return self.status == "succeeded"


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

    # CU36 - El proveedor debe confirmar el reembolso; nunca se simula.
    # Las implementaciones sin soporte heredan un fallo explicito y seguro.
    def crear_reembolso(
        self,
        *,
        payment_intent_id: str,
        amount: int,
        metadata: dict[str, str],
        idempotency_key: str,
    ) -> ReembolsoProveedor:
        raise ErrorProveedorPago(
            "El proveedor de pagos no soporta reembolsos"
        )

    def recuperar_reembolso(self, refund_id: str) -> ReembolsoProveedor:
        raise ErrorProveedorPago(
            "El proveedor de pagos no soporta reembolsos"
        )

    def listar_reembolsos(
        self, payment_intent_id: str,
    ) -> list[ReembolsoProveedor]:
        raise ErrorProveedorPago(
            "El proveedor de pagos no soporta reembolsos"
        )
