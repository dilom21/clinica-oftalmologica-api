from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.gestion_agenda_citas.schemas.schemas import (
    OftalmologoResumidoRespuesta,
)
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


# =========================================================
# CU15 - REGISTRAR CONSULTA CLÍNICA
# =========================================================


class ConsultaClinicaCrear(BaseModel):
    historial_clinico_id: int = Field(gt=0, le=2**63 - 1)
    cita_id: int | None = Field(default=None, gt=0, le=2**63 - 1)
    motivo_consulta: str | None = Field(default=None, max_length=255)
    anamnesis: str | None = None
    observaciones: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator(
        "motivo_consulta", "anamnesis", "observaciones", mode="before",
    )
    @classmethod
    def normalizar_texto_opcional(cls, valor):
        if isinstance(valor, str):
            limpio = valor.strip()
            return limpio or None
        return valor


class ConsultaClinicaRespuesta(BaseModel):
    id: int
    historial_clinico_id: int
    cita_id: int | None
    oftalmologo: OftalmologoResumidoRespuesta
    fecha_consulta: datetime
    motivo_consulta: str | None
    anamnesis: str | None
    observaciones: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# CU16 - REGISTRAR DIAGNÓSTICO
# =========================================================


class DiagnosticoCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=150)
    descripcion: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("nombre", mode="before")
    @classmethod
    def normalizar_nombre(cls, valor):
        if isinstance(valor, str):
            limpio = valor.strip()
            if not limpio:
                raise ValueError("El nombre del diagnóstico no puede estar vacío")
            return limpio
        return valor


class DiagnosticoRespuesta(BaseModel):
    id: int
    consulta_clinica_id: int
    nombre: str
    descripcion: str | None
    fecha_diagnostico: datetime
    estado: bool

    model_config = ConfigDict(from_attributes=True)
