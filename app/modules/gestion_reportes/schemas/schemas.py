from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class FiltroReporte(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campo: str
    operador: str
    valor: Any


class OrdenReporte(BaseModel):
    model_config = ConfigDict(extra="forbid")
    campo: str
    direccion: Literal["asc", "desc"] = "asc"


class ReporteDinamico(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str
    columnas: list[str] = Field(min_length=1)
    filtros: list[FiltroReporte] = Field(default_factory=list)
    orden: list[OrdenReporte] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=50, ge=1, le=200)


class ReporteEstatico(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filtros: list[FiltroReporte] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)


class DatosEnvioReporte(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destinatario: EmailStr
    formato: Literal["xlsx", "pdf", "csv", "html"]
    asunto: str = Field(min_length=1, max_length=150)
    mensaje: str = Field(default="", max_length=1000)


class ReporteDinamicoEmail(DatosEnvioReporte, ReporteDinamico):
    pass


class ReporteEstaticoEmail(DatosEnvioReporte, ReporteEstatico):
    pass


def serializar_valor(value: Any) -> Any:
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value
