from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OftalmologoPagoResumen(BaseModel):
    id: int
    nombres: str
    apellidos: str

    model_config = ConfigDict(from_attributes=True)


class ConsultaPagoResumen(BaseModel):
    consulta_id: int
    fecha_consulta: datetime
    oftalmologo: OftalmologoPagoResumen | None = None
    cantidad_servicios: int
    cantidad_pendientes: int
    total_pendiente: Decimal


class ServicioPagoRespuesta(BaseModel):
    servicio_realizado_id: int
    servicio_id: int
    nombre_servicio: str
    fecha_realizacion: datetime | None
    precio_aplicado: Decimal | None
    estado_pago: Literal["PENDIENTE", "PAGADO"]


class SeleccionServiciosPago(BaseModel):
    servicio_realizado_ids: list[int] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("servicio_realizado_ids")
    @classmethod
    def validar_ids(cls, ids: list[int]):
        if any(servicio_id <= 0 for servicio_id in ids):
            raise ValueError("Todos los identificadores deben ser positivos")
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten servicios duplicados")
        return ids


class SeleccionServiciosValidada(BaseModel):
    consulta_id: int
    paciente_id: int
    servicio_realizado_ids: list[int]
    total: Decimal


class IntencionPagoStripeRespuesta(BaseModel):
    pago_id: int
    consulta_clinica_id: int
    payment_intent_id: str
    client_secret: str
    monto: Decimal
    moneda: str
    estado_pago: str


class EstadoPagoStripeRespuesta(BaseModel):
    pago_id: int
    consulta_clinica_id: int
    estado_pago: str
    monto: Decimal
    moneda: str
    payment_intent_id: str | None


class WebhookStripeRespuesta(BaseModel):
    recibido: bool = True
    procesado: bool
    pago_id: int | None = None
    estado_pago: str | None = None


class ServicioPagoHistorial(BaseModel):
    """Servicio aplicado a un pago, con el importe real de pago_detalle."""

    servicio_realizado_id: int
    nombre_servicio: str
    monto_aplicado: Decimal


class PagoHistorialItem(BaseModel):
    """Elemento del historial de pagos del paciente autenticado."""

    pago_id: int
    consulta_clinica_id: int | None = None
    fecha_creacion: datetime
    fecha_hora_pago: datetime | None = None
    monto: Decimal
    moneda: str
    metodo_pago: str
    estado_pago: Literal[
        "PENDIENTE",
        "APROBADO",
        "RECHAZADO",
        "ANULADO",
        "REEMBOLSADO",
    ]
    pasarela: str | None = None
    servicios: list[ServicioPagoHistorial]
