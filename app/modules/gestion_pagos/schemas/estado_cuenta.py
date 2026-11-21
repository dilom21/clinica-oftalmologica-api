from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


EstadoPlanCuenta = Literal["ACTIVO", "COMPLETADO", "CANCELADO"]
EstadoCuotaCuenta = Literal["PENDIENTE", "VENCIDA", "PAGADA", "CANCELADA"]
EstadoPagoCuenta = Literal["PENDIENTE", "APROBADO"]


class PacienteEstadoCuenta(BaseModel):
    id: int
    nombres: str
    apellidos: str
    ci: str | None
    telefono: str | None


class ServicioEstadoCuenta(BaseModel):
    servicio_realizado_id: int
    consulta_clinica_id: int | None
    servicio: str
    fecha_realizacion: datetime | None
    precio_aplicado: Decimal
    monto_pagado_aplicado: Decimal
    saldo_pendiente: Decimal
    financiado: bool
    plan_pago_id: int | None


class CuotaEstadoCuenta(BaseModel):
    id: int
    numero_cuota: int
    monto_programado: Decimal
    fecha_vencimiento: date
    estado: EstadoCuotaCuenta


class PlanEstadoCuenta(BaseModel):
    id: int
    monto_total: Decimal
    moneda: str
    estado: EstadoPlanCuenta
    saldo_pendiente: Decimal
    cuotas: list[CuotaEstadoCuenta]


class PagoPendienteEstadoCuenta(BaseModel):
    pago_id: int
    monto: Decimal
    moneda: str
    estado: EstadoPagoCuenta
    pasarela: str | None
    aplicado_a_obligacion: bool | None
    requiere_reconciliacion: bool


class ResumenEstadoCuenta(BaseModel):
    moneda: str
    importe_total_servicios: Decimal
    total_pagos_aprobados_aplicados: Decimal
    saldo_total_pendiente: Decimal
    saldo_servicios_no_financiados: Decimal
    saldo_planes_pago: Decimal
    importe_cuotas_vencidas: Decimal = Field(
        description="Parte del saldo pendiente de planes; no se suma nuevamente al saldo total."
    )
    cantidad_planes_activos: int
    cantidad_cuotas_pendientes: int = Field(
        description="Cuotas con estado PENDIENTE; no incluye las cuotas VENCIDA."
    )
    cantidad_cuotas_pagadas: int
    cantidad_cuotas_vencidas: int = Field(
        description="Cuotas VENCIDA; categoría independiente de PENDIENTE."
    )


class EstadoCuentaPaciente(BaseModel):
    paciente: PacienteEstadoCuenta
    resumen: ResumenEstadoCuenta
    servicios: list[ServicioEstadoCuenta]
    planes_pago: list[PlanEstadoCuenta]
    pagos_pendientes_confirmacion: list[PagoPendienteEstadoCuenta]
