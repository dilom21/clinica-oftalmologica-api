from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EstadoPlan = Literal["ACTIVO", "COMPLETADO", "CANCELADO"]
EstadoCuota = Literal["PENDIENTE", "VENCIDA", "PAGADA", "CANCELADA"]
MetodoPagoCuota = Literal["EFECTIVO", "TRANSFERENCIA", "QR"]


class CuotaPlanCrear(BaseModel):
    numero_cuota: int = Field(gt=0, le=10_000)
    monto_programado: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    fecha_vencimiento: date

    model_config = ConfigDict(extra="forbid")


class PlanPagoCrear(BaseModel):
    paciente_id: int = Field(gt=0, le=2**63 - 1)
    consulta_clinica_id: int = Field(gt=0, le=2**63 - 1)
    servicio_realizado_ids: list[int] = Field(min_length=1, max_length=50)
    cuotas: list[CuotaPlanCrear] = Field(min_length=1, max_length=120)
    observaciones: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("servicio_realizado_ids")
    @classmethod
    def validar_servicios(cls, ids: list[int]):
        if any(item <= 0 for item in ids):
            raise ValueError("Todos los identificadores deben ser positivos")
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten servicios duplicados")
        return ids

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        return (valor.strip() or None) if isinstance(valor, str) else valor

    @model_validator(mode="after")
    def validar_cronograma(self):
        numeros = [item.numero_cuota for item in self.cuotas]
        if len(numeros) != len(set(numeros)):
            raise ValueError("Los numeros de cuota no pueden repetirse")
        if sorted(numeros) != list(range(1, len(numeros) + 1)):
            raise ValueError("Las cuotas deben numerarse consecutivamente desde 1")
        fechas = [item.fecha_vencimiento for item in sorted(
            self.cuotas, key=lambda item: item.numero_cuota,
        )]
        if fechas != sorted(fechas):
            raise ValueError("Las fechas de vencimiento deben ser cronologicas")
        return self


class ServicioDisponiblePlan(BaseModel):
    servicio_realizado_id: int
    nombre_servicio: str
    precio_aplicado: Decimal
    saldo_cobrado: Decimal
    saldo_disponible: Decimal
    disponible: bool
    conflicto: str | None = None


class ServiciosDisponiblesPlanRespuesta(BaseModel):
    paciente_id: int
    consulta_clinica_id: int
    servicios: list[ServicioDisponiblePlan]
    total_disponible: Decimal


class ServicioPlanRespuesta(BaseModel):
    servicio_realizado_id: int
    nombre_servicio: str
    monto_financiado: Decimal
    activo: bool


class PagoCuotaResumen(BaseModel):
    pago_id: int
    metodo_pago: str
    estado: str
    monto: Decimal
    fecha_creacion: datetime
    aplicado_a_cuota: bool


class CuotaRespuesta(BaseModel):
    id: int
    numero_cuota: int
    monto_programado: Decimal
    fecha_vencimiento: date
    estado: EstadoCuota
    pagos: list[PagoCuotaResumen] = Field(default_factory=list)


class PlanPagoRespuesta(BaseModel):
    id: int
    paciente_id: int
    consulta_clinica_id: int
    monto_total: Decimal
    moneda: str
    cantidad_cuotas: int
    fecha_creacion: datetime
    estado: EstadoPlan
    observaciones: str | None
    creado_por: int
    saldo_pendiente: Decimal
    monto_pagado: Decimal
    servicios: list[ServicioPlanRespuesta]
    cuotas: list[CuotaRespuesta]


class PlanPagoListadoItem(BaseModel):
    id: int
    paciente_id: int
    consulta_clinica_id: int
    monto_total: Decimal
    monto_pagado: Decimal
    saldo_pendiente: Decimal
    moneda: str
    cantidad_cuotas: int
    cuotas_pagadas: int
    fecha_creacion: datetime
    estado: EstadoPlan
    tiene_cuotas_vencidas: bool


class PlanesPagoPagina(BaseModel):
    items: list[PlanPagoListadoItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class CancelarPlanPago(BaseModel):
    motivo: str = Field(min_length=3, max_length=1000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class PagoCuotaCrear(BaseModel):
    metodo_pago: MetodoPagoCuota
    referencia_bancaria: str | None = Field(default=None, min_length=3, max_length=255)
    observaciones: str | None = Field(default=None, max_length=1000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("referencia_bancaria", "observaciones", mode="before")
    @classmethod
    def normalizar_opcional(cls, valor):
        return (valor.strip() or None) if isinstance(valor, str) else valor

    @model_validator(mode="after")
    def validar_metodo(self):
        bancaria = self.metodo_pago in {"TRANSFERENCIA", "QR"}
        if bancaria and self.referencia_bancaria is None:
            raise ValueError("La referencia bancaria es obligatoria")
        if self.metodo_pago == "EFECTIVO" and self.referencia_bancaria is not None:
            raise ValueError("El efectivo no admite referencia bancaria")
        return self


class RechazarPagoCuota(BaseModel):
    motivo: str = Field(min_length=3, max_length=1000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class PagoCuotaRespuesta(BaseModel):
    pago_id: int
    cuota_id: int
    plan_pago_id: int
    monto: Decimal
    moneda: str
    metodo_pago: str
    estado_pago: str
    estado_cuota: EstadoCuota
    aplicado_a_cuota: bool
    referencia_bancaria: str | None = None


class IntencionCuotaStripeRespuesta(BaseModel):
    pago_id: int
    cuota_id: int
    plan_pago_id: int
    payment_intent_id: str
    client_secret: str
    monto: Decimal
    moneda: str
    estado_pago: str


class IntentosCuotaRespuesta(BaseModel):
    cuota_id: int
    estado_cuota: EstadoCuota
    intentos: list[PagoCuotaResumen]
