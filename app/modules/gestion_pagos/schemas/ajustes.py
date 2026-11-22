"""CU36 - Contratos de anulacion, correccion, reembolso y conciliacion."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


TipoAjuste = Literal[
    "ANULACION", "CORRECCION", "DEVOLUCION", "REEMBOLSO", "RECONCILIACION",
]
NaturalezaAjuste = Literal[
    "ERROR_DE_REGISTRO", "PAGO_DUPLICADO", "SERVICIO_CANCELADO", "SOBREPAGO",
    "PAGO_INSUFICIENTE", "REVERSION_FINANCIACION", "OTRO",
]
EstadoAjuste = Literal[
    "PENDIENTE_PROVEEDOR", "CONFIRMADO", "FALLIDO", "CANCELADO", "DESCONOCIDO",
]
EfectoCaja = Literal["SIN_EFECTO", "DEVOLUCION_MANUAL", "DEVOLUCION_STRIPE"]


class AjusteDetalleCrear(BaseModel):
    servicio_realizado_id: int = Field(gt=0, le=2**63 - 1)
    monto: Decimal = Field(ge=Decimal("-9999999999.99"),
                           le=Decimal("9999999999.99"))

    model_config = ConfigDict(extra="forbid")

    @field_validator("monto")
    @classmethod
    def validar_monto(cls, valor: Decimal) -> Decimal:
        if valor == 0:
            raise ValueError("El monto del detalle no puede ser cero")
        if abs(valor.as_tuple().exponent) > 2:
            raise ValueError("El monto admite como maximo dos decimales")
        return valor


class AnularPago(BaseModel):
    motivo: str = Field(min_length=10, max_length=1000)
    naturaleza: NaturalezaAjuste
    evidencia: str | None = Field(default=None, min_length=5, max_length=2000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("evidencia", mode="before")
    @classmethod
    def normalizar_evidencia(cls, valor):
        return (valor.strip() or None) if isinstance(valor, str) else valor


class CorregirPago(BaseModel):
    motivo: str = Field(min_length=10, max_length=1000)
    naturaleza: NaturalezaAjuste
    importe: Decimal
    moneda: str | None = Field(default=None, min_length=3, max_length=3)
    evidencia: str | None = Field(default=None, min_length=5, max_length=2000)
    servicios: list[AjusteDetalleCrear] = Field(min_length=1, max_length=50)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("servicios")
    @classmethod
    def servicios_unicos(cls, items: list[AjusteDetalleCrear]):
        ids = [item.servicio_realizado_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten servicios duplicados")
        return items

    @model_validator(mode="after")
    def validar_signos(self):
        if self.importe == 0:
            raise ValueError("Una correccion requiere un importe distinto de cero")
        if self.moneda is not None and self.moneda != self.moneda.upper():
            raise ValueError("La moneda debe estar en mayusculas")
        if any(item.monto * self.importe <= 0 for item in self.servicios):
            raise ValueError(
                "Cada servicio debe llevar el mismo signo que el importe total"
            )
        total = sum((item.monto for item in self.servicios), Decimal("0.00"))
        if total != self.importe:
            raise ValueError(
                "La distribucion por servicio debe sumar exactamente el importe"
            )
        if self.importe > 0 and self.naturaleza != "ERROR_DE_REGISTRO":
            raise ValueError(
                "Un ajuste positivo solo se admite como ERROR_DE_REGISTRO"
            )
        return self


class ReembolsarPago(BaseModel):
    motivo: str = Field(min_length=10, max_length=1000)
    naturaleza: NaturalezaAjuste
    importe: Decimal
    moneda: str | None = Field(default=None, min_length=3, max_length=3)
    evidencia: str | None = Field(default=None, min_length=5, max_length=2000)
    referencia_devolucion: str | None = Field(
        default=None, min_length=3, max_length=255,
    )
    servicios: list[AjusteDetalleCrear] = Field(min_length=1, max_length=50)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("servicios")
    @classmethod
    def servicios_unicos(cls, items: list[AjusteDetalleCrear]):
        ids = [item.servicio_realizado_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten servicios duplicados")
        return items

    @model_validator(mode="after")
    def validar_signos(self):
        if self.importe >= 0:
            raise ValueError("El importe a devolver debe informarse en negativo")
        if self.moneda is not None and self.moneda != self.moneda.upper():
            raise ValueError("La moneda debe estar en mayusculas")
        if any(item.monto >= 0 for item in self.servicios):
            raise ValueError("Cada servicio debe llevar un monto negativo")
        total = sum((item.monto for item in self.servicios), Decimal("0.00"))
        if total != self.importe:
            raise ValueError(
                "La distribucion por servicio debe sumar exactamente el importe"
            )
        return self


class ConciliarAjuste(BaseModel):
    resultado: Literal["CONFIRMADO", "FALLIDO", "CANCELADO"]
    evidencia: str | None = Field(default=None, min_length=5, max_length=2000)
    observaciones: str | None = Field(default=None, max_length=2000)
    # E-3: atribucion manual de un refund sin metadata. Solo se acepta con
    # evidencia y rol administrador; nunca relaja la atribucion automatica.
    refund_id: str | None = Field(default=None, min_length=3, max_length=255)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class AtribuirReembolsoHuerfano(BaseModel):
    """E-3: conciliacion manual de un refund huerfano con refund_id explicito."""

    motivo: str = Field(min_length=10, max_length=1000)
    naturaleza: NaturalezaAjuste
    evidencia: str = Field(min_length=5, max_length=2000)
    servicios: list[AjusteDetalleCrear] = Field(min_length=1, max_length=50)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("servicios")
    @classmethod
    def servicios_unicos(cls, items: list[AjusteDetalleCrear]):
        ids = [item.servicio_realizado_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten servicios duplicados")
        if any(item.monto >= 0 for item in items):
            raise ValueError("Cada servicio debe llevar un monto negativo")
        return items


class WebhookHuerfanoRespuesta(BaseModel):
    id: int
    stripe_event_id: str
    event_type: str
    refund_id: str
    payment_intent_id: str | None = None
    amount: int
    currency: str
    status: str
    estado: Literal["PENDIENTE", "ATRIBUIDO", "DESCARTADO"]
    ajuste_id: int | None = None
    motivo: str | None = None
    creado_en: datetime


class WebhookHuerfanoCentralRespuesta(WebhookHuerfanoRespuesta):
    """I-2: cuarentena central (Control Plane); incluye el tenant atribuido."""

    empresa_id: int | None = None
    tenant_database_id: int | None = None
    atribuido_por: int | None = None
    actualizado_en: datetime | None = None


class AtribuirHuerfanoCentral(BaseModel):
    """I-2: atribucion manual explicita de un refund central a una empresa."""

    empresa_codigo: str = Field(min_length=1, max_length=30)
    motivo: str = Field(min_length=10, max_length=1000)
    evidencia: str = Field(min_length=5, max_length=2000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")



class AjusteDetalleRespuesta(BaseModel):
    servicio_realizado_id: int
    monto: Decimal


class PagoAjusteRespuesta(BaseModel):
    id: int
    pago_id: int
    tipo: TipoAjuste
    naturaleza: NaturalezaAjuste
    efecto_caja: EfectoCaja
    estado: EstadoAjuste
    importe: Decimal
    moneda: str
    motivo: str
    evidencia: str | None = None
    referencia_externa: str | None = None
    revertir_aplicacion_cuota: bool = False
    # Los ajustes automaticos creados por webhook (RECONCILIACION) no tienen
    # actor humano: la columna es nullable en modelo y base de datos.
    creado_por: int | None = None
    confirmado_por: int | None = None
    conciliado_por: int | None = None
    fecha_creacion: datetime
    fecha_confirmacion: datetime | None = None
    fecha_conciliacion: datetime | None = None
    observaciones: str | None = None
    detalles: list[AjusteDetalleRespuesta] = Field(default_factory=list)


class HistorialAjustesRespuesta(BaseModel):
    pago_id: int
    importe_base: Decimal
    importe_vigente: Decimal
    importe_reembolsado: Decimal
    ajuste_abierto: bool
    ajustes: list[PagoAjusteRespuesta]


__all__ = [
    "AjusteDetalleCrear",
    "AjusteDetalleRespuesta",
    "AnularPago",
    "AtribuirReembolsoHuerfano",
    "ConciliarAjuste",
    "CorregirPago",
    "EfectoCaja",
    "EstadoAjuste",
    "HistorialAjustesRespuesta",
    "NaturalezaAjuste",
    "PagoAjusteRespuesta",
    "ReembolsarPago",
    "TipoAjuste",
    "WebhookHuerfanoRespuesta",
]
