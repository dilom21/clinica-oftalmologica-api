from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MensajeEntrada(Entrada):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, json_schema_extra={"examples":[{"mensaje":"¿Qué especialistas atienden aquí?","modalidad":"voz"}]})
    mensaje: str = Field(min_length=1, max_length=4000)
    modalidad: Literal["texto", "voz"] = "texto"

    @field_validator("mensaje")
    @classmethod
    def texto_valido(cls, value):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Texto Unicode inválido") from None
        if any(ord(c) < 32 and c not in "\n\t" for c in value):
            raise ValueError("Caracteres de control no permitidos")
        return value


class ServicioPublico(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    precio: Decimal | None
    duracion_minutos: int | None


class ProfesionalPublico(BaseModel):
    id: int
    nombre: str
    especialidad: str | None


class Intervalo(BaseModel):
    hora_inicio: time
    hora_fin: time


class ClinicaTarjeta(BaseModel):
    tipo: Literal["clinica"] = "clinica"
    privacidad: Literal["publica"] = "publica"
    nombre: str | None
    telefono: str | None
    correo: str | None
    direccion: str | None
    horario_general: None = None


class ServiciosTarjeta(BaseModel):
    tipo: Literal["servicios"] = "servicios"
    privacidad: Literal["publica"] = "publica"
    servicios: list[ServicioPublico] = Field(max_length=10)
    busqueda: str | None = None
    offset: int | None = None
    hay_mas: bool | None = None


class ProfesionalesTarjeta(BaseModel):
    tipo: Literal["profesionales"] = "profesionales"
    privacidad: Literal["publica"] = "publica"
    profesionales: list[ProfesionalPublico] = Field(max_length=10)
    hay_mas: bool | None = None
    offset: int | None = None


class DisponibilidadTarjeta(BaseModel):
    tipo: Literal["disponibilidad"] = "disponibilidad"
    privacidad: Literal["publica"] = "publica"
    oftalmologo_id: int
    fecha: date
    intervalos: list[Intervalo] = Field(max_length=48)
    profesional: ProfesionalPublico | None = None
    franja: Literal["manana", "tarde"] | None = None


class PropuestaTarjeta(BaseModel):
    tipo: Literal["propuesta_cita"] = "propuesta_cita"
    privacidad: Literal["publica"] = "publica"
    oftalmologo_id: int
    fecha: date
    hora_inicio: time
    hora_fin: time
    reservada: Literal[False] = False
    requiere_confirmacion: Literal[True] = True
    profesional: ProfesionalPublico | None = None


class CitaPropia(BaseModel):
    id: int
    oftalmologo_id: int
    fecha: date
    hora_inicio: time
    hora_fin: time
    estado: str


class CitasTarjeta(BaseModel):
    tipo: Literal["citas_propias"] = "citas_propias"
    privacidad: Literal["privada"] = "privada"
    citas: list[CitaPropia] = Field(max_length=10)
    hay_mas: bool | None = None


class ErrorChatbot(BaseModel):
    detail: str
    model_config = ConfigDict(json_schema_extra={"examples":[{"detail":"Conversación no disponible"}]})


class AccionTarjeta(BaseModel):
    tipo: Literal["navegacion"] = "navegacion"
    privacidad: Literal["privada"] = "privada"
    destino: Literal["catalogo", "agendar_cita", "mis_citas", "detalle_cita", "reprogramar_cita", "cancelar_cita"]
    cita_id: int | None = None


Tarjeta = Annotated[ClinicaTarjeta | ServiciosTarjeta | ProfesionalesTarjeta |
                    DisponibilidadTarjeta | PropuestaTarjeta | CitasTarjeta |
                    AccionTarjeta, Field(discriminator="tipo")]


class ConversacionRespuesta(BaseModel):
    conversacion_id: UUID
    expira_en_segundos: int


class MensajeRespuesta(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples":[{
        "conversacion_id":"123e4567-e89b-42d3-a456-426614174000",
        "respuesta":"Encontré a Profesional Ficticio, especialista en Oftalmología General.",
        "texto_voz":"Encontré a Profesional Ficticio, especialista en Oftalmología General.",
        "tipo":"datos","tarjetas":[{"tipo":"profesionales","privacidad":"publica","profesionales":[{"id":1,"nombre":"Profesional Ficticio","especialidad":"Oftalmología General"}],"hay_mas":False,"offset":0}]
    }]})
    conversacion_id: UUID
    respuesta: str
    texto_voz: str
    tipo: Literal["texto", "datos", "orientacion", "privacidad"] = "texto"
    tarjetas: list[Tarjeta] = Field(default_factory=list, max_length=16)
