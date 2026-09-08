from datetime import date, datetime, time

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


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


# =========================================================
# CU11 - CONFIGURAR DISPONIBILIDAD DEL OFTALMÓLOGO
# =========================================================

DIA_SEMANA_MIN = 1
DIA_SEMANA_MAX = 7


def _validar_orden_horario(inicio: time, fin: time) -> None:
    if inicio >= fin:
        raise ValueError("hora_inicio debe ser anterior a hora_fin")


class HorarioOftalmologoBase(BaseModel):
    dia_semana: int = Field(
        ge=DIA_SEMANA_MIN,
        le=DIA_SEMANA_MAX,
    )
    hora_inicio: time
    hora_fin: time

    @model_validator(mode="after")
    def _orden_horas(self):
        _validar_orden_horario(self.hora_inicio, self.hora_fin)
        return self


class HorarioOftalmologoCrear(HorarioOftalmologoBase):
    pass


class HorarioOftalmologoActualizar(HorarioOftalmologoBase):
    pass


class HorarioOftalmologoRespuesta(BaseModel):
    id: int
    oftalmologo_id: int
    dia_semana: int
    hora_inicio: time
    hora_fin: time
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class BloqueoHorarioBase(BaseModel):
    fecha: date
    hora_inicio: time
    hora_fin: time
    motivo: str | None = None

    @model_validator(mode="after")
    def _orden_horas(self):
        _validar_orden_horario(self.hora_inicio, self.hora_fin)
        return self


class BloqueoHorarioCrear(BloqueoHorarioBase):
    pass


class BloqueoHorarioActualizar(BloqueoHorarioBase):
    pass


class BloqueoHorarioRespuesta(BaseModel):
    id: int
    oftalmologo_id: int
    fecha: date
    hora_inicio: time
    hora_fin: time
    motivo: str | None = None
    estado: bool
    fecha_registro: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class EstadoDisponibilidadActualizar(BaseModel):
    estado: bool


class ConfiguracionDisponibilidadRespuesta(BaseModel):
    oftalmologo: OftalmologoResumidoRespuesta
    horarios: list[HorarioOftalmologoRespuesta] = []
    bloqueos: list[BloqueoHorarioRespuesta] = []


# =========================================================
# CU10 - GESTIONAR CITAS MÉDICAS
# =========================================================

ESTADOS_CITA_VALIDOS = (
    "PROGRAMADA",
    "CONFIRMADA",
    "EN_ESPERA",
    "ATENDIDA",
    "CANCELADA",
    "NO_ASISTIO",
)

ESTADO_CITA_INICIAL = "PROGRAMADA"
ESTADO_CITA_CANCELADA = "CANCELADA"


def _normalizar_estado_cita(estado: str) -> str:
    normalizado = (estado or "").strip().upper()
    if normalizado not in ESTADOS_CITA_VALIDOS:
        raise ValueError(
            f"Estado de cita inválido: {estado}. "
            f"Valores válidos: {', '.join(ESTADOS_CITA_VALIDOS)}"
        )
    return normalizado


class CitaCreate(BaseModel):
    paciente_id: int
    oftalmologo_id: int
    fecha: date
    hora_inicio: time
    motivo: str | None = None
    observaciones: str | None = None


class CitaUpdate(BaseModel):
    fecha: date | None = None
    hora_inicio: time | None = None
    motivo: str | None = None
    observaciones: str | None = None

    @model_validator(mode="after")
    def _exigir_al_menos_un_campo(self):
        if all(
            campo is None
            for campo in (
                self.fecha,
                self.hora_inicio,
                self.motivo,
                self.observaciones,
            )
        ):
            raise ValueError(
                "Debe indicar al menos un campo a modificar "
                "(fecha, hora_inicio, motivo u observaciones)"
            )
        return self


class CitaEstadoUpdate(BaseModel):
    estado: str

    @field_validator("estado")
    @classmethod
    def _estado_valido(cls, valor: str) -> str:
        return _normalizar_estado_cita(valor)


class CitaResponse(BaseModel):
    id: int
    paciente_id: int
    oftalmologo_id: int
    fecha: date
    hora_inicio: time
    hora_fin: time
    motivo: str | None = None
    observaciones: str | None = None
    estado: str
    fecha_registro: datetime | None = None
    fecha_actualizacion: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# CU12 - CONSULTAR HISTORIAL DE CITAS
# =========================================================

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
    motivo: str | None = None
    observaciones: str | None = None
    estado: str
    canal: str | None = None

    model_config = ConfigDict(from_attributes=True)


class HistorialCitasRespuesta(BaseModel):
    paciente: PacienteHistorialRespuesta
    citas: list[CitaHistorialRespuesta]
    mensaje: str | None = None

