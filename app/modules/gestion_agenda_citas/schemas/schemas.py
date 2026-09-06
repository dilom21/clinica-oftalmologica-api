from datetime import date, time

from pydantic import BaseModel, ConfigDict


# =========================================================
# CU09 - CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA
# =========================================================


class OftalmologoResumidoRespuesta(BaseModel):
    id: int
    matricula: str
    nombres: str
    apellidos: str
    especialidad: str | None = None

    model_config = ConfigDict(from_attributes=True)


class IntervaloHorarioRespuesta(BaseModel):
    hora_inicio: time
    hora_fin: time


class DisponibilidadRespuesta(BaseModel):
    oftalmologo: OftalmologoResumidoRespuesta
    fecha: date
    tiene_horario: bool
    horarios_base: list[IntervaloHorarioRespuesta] = []
    intervalos_disponibles: list[IntervaloHorarioRespuesta] = []


class CitaAgendaRespuesta(BaseModel):
    id: int
    hora_inicio: time
    hora_fin: time
    estado: str
    motivo: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AgendaRespuesta(BaseModel):
    oftalmologo: OftalmologoResumidoRespuesta
    fecha: date
    tiene_horario: bool
    horarios_base: list[IntervaloHorarioRespuesta] = []
    citas: list[CitaAgendaRespuesta] = []
