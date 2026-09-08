from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.gestion_pacientes.schemas.schemas import PacienteRespuesta


# Valores de la restricción chk_antecedente_tipo de PostgreSQL.
TipoAntecedente = Literal[
    "ALERGIA", "ENFERMEDAD", "CIRUGIA", "MEDICAMENTO", "ANTECEDENTE_FAMILIAR", "OTRO",
]


class AntecedenteClinicoBase(BaseModel):
    tipo: TipoAntecedente
    descripcion: str = Field(min_length=1)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("tipo", mode="before")
    @classmethod
    def normalizar_tipo(cls, valor):
        return valor.strip().upper() if isinstance(valor, str) else valor


class AntecedenteClinicoCrear(AntecedenteClinicoBase):
    historial_clinico_id: int = Field(gt=0, le=2**63 - 1)


class AntecedenteClinicoActualizar(AntecedenteClinicoBase):
    pass


class AntecedenteClinicoRespuesta(BaseModel):
    id: int
    tipo: str
    descripcion: str
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)


class HistorialClinicoDetalleRespuesta(BaseModel):
    id: int
    fecha_apertura: datetime
    observaciones_generales: str | None
    antecedentes: list[AntecedenteClinicoRespuesta]

    model_config = ConfigDict(from_attributes=True)


class HistorialClinicoRespuesta(BaseModel):
    paciente: PacienteRespuesta
    historial: HistorialClinicoDetalleRespuesta | None
