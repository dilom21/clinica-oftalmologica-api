from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.modules.gestion_pacientes.schemas.schemas import PacienteRespuesta


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