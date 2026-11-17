from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MetodoManual = Literal["EFECTIVO", "TRANSFERENCIA", "QR"]
EstadoPago = Literal["PENDIENTE", "APROBADO", "RECHAZADO", "ANULADO", "REEMBOLSADO"]
EstadoNotificacion = Literal[
    "PENDIENTE", "PROCESANDO", "ACEPTADO_POR_PROVEEDOR", "FALLIDO", "ENTREGADO",
]


class PagoAdministrativoCrear(BaseModel):
    paciente_id: int = Field(gt=0, le=2**63 - 1)
    servicio_realizado_ids: list[int] = Field(min_length=1, max_length=50)
    metodo_pago: MetodoManual
    referencia_bancaria: str | None = Field(default=None, min_length=3, max_length=255)
    observaciones: str | None = Field(default=None, max_length=1000)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("servicio_realizado_ids")
    @classmethod
    def validar_ids(cls, ids: list[int]):
        if any(item <= 0 for item in ids):
            raise ValueError("Todos los identificadores deben ser positivos")
        if len(ids) != len(set(ids)):
            raise ValueError("No se permiten servicios duplicados")
        return ids

    @field_validator("referencia_bancaria", "observaciones", mode="before")
    @classmethod
    def normalizar_opcional(cls, valor):
        return (valor.strip() or None) if isinstance(valor, str) else valor

    @model_validator(mode="after")
    def validar_metodo(self):
        bancaria = self.metodo_pago in {"TRANSFERENCIA", "QR"}
        if bancaria and self.referencia_bancaria is None:
            raise ValueError("La referencia bancaria es obligatoria para transferencia o QR")
        if self.metodo_pago == "EFECTIVO" and self.referencia_bancaria is not None:
            raise ValueError("El pago en efectivo no admite referencia bancaria")
        return self


class ServicioPendienteAdministrativo(BaseModel):
    servicio_realizado_id: int
    paciente_id: int
    consulta_clinica_id: int
    nombre_servicio: str
    fecha_realizacion: datetime | None
    precio_aplicado: Decimal
    cobrable: bool
    conflicto_pago: str | None = None


class ResumenServiciosPendientes(BaseModel):
    paciente_id: int
    consulta_clinica_id: int | None
    servicios: list[ServicioPendienteAdministrativo]
    total: Decimal


class NotificacionPagoRespuesta(BaseModel):
    estado: EstadoNotificacion
    intentos: int
    proveedor_mensaje_id: str | None = None
    fecha_aceptado: datetime | None = None
    fecha_entregado: datetime | None = None
    ultimo_error: str | None = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("ultimo_error", mode="before")
    @classmethod
    def normalizar_marca_legacy_redireccion(cls, valor):
        # Compatibilidad de lectura: versiones anteriores guardaron una
        # redireccion exitosa en un campo reservado para errores.
        if valor == "DESTINATARIO_REDIRECT_PRUEBAS":
            return None
        return valor


class PagoAdministrativoRespuesta(BaseModel):
    id: int
    paciente_id: int
    consulta_clinica_id: int
    fecha_creacion: datetime
    fecha_hora_pago: datetime | None
    monto: Decimal
    moneda: str
    metodo_pago: str
    estado: EstadoPago
    referencia_bancaria: str | None
    observaciones: str | None
    servicio_realizado_ids: list[int]
    notificacion: NotificacionPagoRespuesta | None = None


class PagosAdministrativosPagina(BaseModel):
    items: list[PagoAdministrativoRespuesta]
    total: int
    page: int
    page_size: int
    total_pages: int


class ResultadoWebhookResend(BaseModel):
    recibido: bool = True
    procesado: bool
    notificacion_id: int | None = None
    estado: EstadoNotificacion | None = None
