from datetime import date, time

from pydantic import BaseModel, ConfigDict


class PacienteHistorialRespuesta(BaseModel):
    id: int
    nombres: str
    apellidos: str
    ci: str | None

    model_config = ConfigDict(from_attributes=True)


class CitaHistorialRespuesta(BaseModel):
    id: int
    paciente_id: int
    oftalmologo_id: int
    fecha: date
    hora_inicio: time
    hora_fin: time
    motivo: str | None
    observaciones: str | None
    estado: str
    canal: str | None

    model_config = ConfigDict(from_attributes=True)


class HistorialCitasRespuesta(BaseModel):
    paciente: PacienteHistorialRespuesta
    citas: list[CitaHistorialRespuesta]
    mensaje: str | None = None
