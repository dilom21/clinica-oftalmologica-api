from datetime import datetime

from pydantic import BaseModel

from app.modules.gestion_pacientes.schemas.schemas import PacienteRespuesta


class AntecedenteClinicoRespuesta(BaseModel):
    id: int
    tipo: str
    descripcion: str
    fecha_registro: datetime


class HistorialClinicoDetalleRespuesta(BaseModel):
    id: int
    fecha_apertura: datetime
    observaciones_generales: str | None
    antecedentes: list[AntecedenteClinicoRespuesta]


class HistorialClinicoRespuesta(BaseModel):
    paciente: PacienteRespuesta
    historial: HistorialClinicoDetalleRespuesta | None