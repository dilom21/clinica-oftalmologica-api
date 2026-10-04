from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.modules.gestion_agenda_citas.schemas.schemas import OftalmologoResumidoRespuesta
from app.modules.gestion_servicios.schemas.schemas import ServicioResponse


IdPositivo = Annotated[int, Field(gt=0, le=2**63 - 1)]
PrecioAplicado = Annotated[
    Decimal, Field(ge=0, max_digits=10, decimal_places=2, allow_inf_nan=False),
]


class ServicioRealizadoLineaCrear(BaseModel):
    servicio_id: IdPositivo
    # El servidor determina el importe; el cliente puede enviar el mostrado para verificarlo.
    precio_aplicado: PrecioAplicado | None = None
    observaciones: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        return (valor.strip() or None) if isinstance(valor, str) else valor

    @field_validator("precio_aplicado", mode="before")
    @classmethod
    def rechazar_booleano(cls, valor):
        if isinstance(valor, bool):
            raise ValueError("El precio aplicado debe ser un número")
        return valor


class ServicioRealizadoCrear(ServicioRealizadoLineaCrear):
    paciente_id: IdPositivo
    consulta_clinica_id: IdPositivo | None = None
    fecha_realizacion: AwareDatetime | None = None


class ServiciosRealizadosLoteCrear(BaseModel):
    paciente_id: IdPositivo
    consulta_clinica_id: IdPositivo | None = None
    fecha_realizacion: AwareDatetime | None = None
    servicios: list[ServicioRealizadoLineaCrear] = Field(min_length=1, max_length=50)

    model_config = ConfigDict(extra="forbid")


class ServicioRealizadoActualizar(ServicioRealizadoCrear):
    """PUT conserva el importe histórico; al cambiar servicio usa su precio de catálogo."""


class PacienteServicioRespuesta(BaseModel):
    id: int
    nombres: str
    apellidos: str

    model_config = ConfigDict(from_attributes=True)


class ServicioRealizadoRespuesta(BaseModel):
    id: int
    servicio_id: int
    paciente_id: int
    consulta_clinica_id: int | None
    oftalmologo_id: int
    fecha_realizacion: datetime | None
    precio_aplicado: float | None
    observaciones: str | None
    estado: bool | None
    servicio: ServicioResponse
    paciente: PacienteServicioRespuesta
    oftalmologo: OftalmologoResumidoRespuesta

    model_config = ConfigDict(from_attributes=True)


class ServiciosRealizadosPagina(BaseModel):
    items: list[ServicioRealizadoRespuesta]
    total: int
    page: int
    page_size: int
    total_pages: int
