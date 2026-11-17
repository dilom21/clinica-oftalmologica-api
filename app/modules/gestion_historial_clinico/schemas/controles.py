import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EstadoControl = Literal["PROGRAMADO", "REALIZADO", "CANCELADO"]


def _validar_fecha_iso(valor):
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor
    if isinstance(valor, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
        try:
            return date.fromisoformat(valor)
        except ValueError:
            pass
    raise ValueError("La fecha programada debe ser una fecha válida en formato YYYY-MM-DD")


class ControlMedicoCrear(BaseModel):
    fecha_programada: date
    motivo: str = Field(min_length=1, max_length=255)
    observaciones: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("fecha_programada", mode="before")
    @classmethod
    def validar_fecha_programada(cls, valor):
        return _validar_fecha_iso(valor)

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        if isinstance(valor, str):
            return valor.strip() or None
        return valor


class ControlMedicoActualizar(BaseModel):
    fecha_programada: date | None = None
    motivo: str | None = Field(default=None, min_length=1, max_length=255)
    observaciones: str | None = None
    estado: EstadoControl | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("fecha_programada", mode="before")
    @classmethod
    def validar_fecha_programada(cls, valor):
        return _validar_fecha_iso(valor)

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        if isinstance(valor, str):
            return valor.strip() or None
        return valor

    @model_validator(mode="after")
    def validar_actualizacion(self):
        if not self.model_fields_set:
            raise ValueError("Debe indicar al menos un dato del control")
        for campo in ("fecha_programada", "motivo", "estado"):
            if campo in self.model_fields_set and getattr(self, campo) is None:
                raise ValueError(f"El campo {campo} no puede ser nulo")
        return self


class ControlMedicoRespuesta(BaseModel):
    id: int
    consulta_clinica_id: int | None
    paciente_id: int
    oftalmologo_id: int
    fecha_programada: date
    motivo: str | None
    observaciones: str | None
    estado: str | None

    model_config = ConfigDict(from_attributes=True)
