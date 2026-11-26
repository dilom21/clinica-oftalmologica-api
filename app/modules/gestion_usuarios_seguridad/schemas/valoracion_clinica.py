from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =========================================================
# CU40 - ESCALA Y REGLAS DE VALORACIÓN DE LA CLÍNICA
# =========================================================

PUNTUACION_MINIMA = 1
PUNTUACION_MAXIMA = 5
COMENTARIO_MAX_LONGITUD = 1000

ESCALA_VALORACION: dict[int, str] = {
    1: "Muy mala",
    2: "Mala",
    3: "Regular",
    4: "Buena",
    5: "Excelente",
}

MOTIVO_CONSULTA_FINALIZADA = "CONSULTA_FINALIZADA"
MOTIVO_RECETA_EMITIDA = "RECETA_EMITIDA"
MOTIVO_ATENCION_COMPLETADA = "ATENCION_COMPLETADA"


def etiqueta_para_puntuacion(puntuacion: int) -> str:
    return ESCALA_VALORACION.get(puntuacion, str(puntuacion))


def _validar_puntuacion(valor: int) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError("La puntuación debe ser un número entero entre 1 y 5")
    if valor < PUNTUACION_MINIMA or valor > PUNTUACION_MAXIMA:
        raise ValueError(
            f"La puntuación debe estar entre {PUNTUACION_MINIMA} y "
            f"{PUNTUACION_MAXIMA}"
        )
    return valor


def _limpiar_comentario(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = valor.strip()
    if not texto:
        return None
    if len(texto) > COMENTARIO_MAX_LONGITUD:
        raise ValueError(
            f"El comentario no puede superar los {COMENTARIO_MAX_LONGITUD} "
            f"caracteres"
        )
    return texto


# =========================================================
# DTOs DE ENTRADA
# =========================================================

class ValoracionClinicaCrear(BaseModel):
    """Datos para registrar una nueva valoración sobre una atención médica."""

    consulta_clinica_id: int = Field(gt=0)
    puntuacion: int
    comentario: str | None = None

    # Nunca se acepta paciente_id, usuario_id ni empresa_id desde el cliente.
    model_config = ConfigDict(extra="forbid")

    @field_validator("puntuacion", mode="before")
    @classmethod
    def validar_puntuacion(cls, valor) -> int:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError(
                "La puntuación debe ser un número entero entre 1 y 5"
            )
        return _validar_puntuacion(valor)

    @field_validator("comentario")
    @classmethod
    def validar_comentario(cls, valor: str | None) -> str | None:
        return _limpiar_comentario(valor)


class ValoracionClinicaActualizar(BaseModel):
    """Datos para actualizar una valoración existente sin duplicarla."""

    puntuacion: int
    comentario: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("puntuacion", mode="before")
    @classmethod
    def validar_puntuacion(cls, valor) -> int:
        if isinstance(valor, bool) or not isinstance(valor, int):
            raise ValueError(
                "La puntuación debe ser un número entero entre 1 y 5"
            )
        return _validar_puntuacion(valor)

    @field_validator("comentario")
    @classmethod
    def validar_comentario(cls, valor: str | None) -> str | None:
        return _limpiar_comentario(valor)


# =========================================================
# DTOs DE SALIDA
# =========================================================

class OftalmologoValoracionResumen(BaseModel):
    id: int
    nombres: str
    apellidos: str
    especialidad: str | None = None
    nombre_completo: str

    model_config = ConfigDict(from_attributes=True)


class ValoracionClinicaRespuesta(BaseModel):
    id: int
    paciente_id: int
    consulta_clinica_id: int
    oftalmologo_id: int
    puntuacion: int
    etiqueta_puntuacion: str
    comentario: str | None = None
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    fecha_consulta: datetime | None = None
    motivo_consulta: str | None = None
    oftalmologo: OftalmologoValoracionResumen | None = None
    empresa_codigo: str | None = None
    empresa_nombre: str | None = None
    mensaje: str | None = None


class NotificacionValoracionRespuesta(BaseModel):
    id: int | None = None
    consulta_clinica_id: int
    fecha_consulta: datetime
    motivo_consulta: str | None = None
    tiene_receta: bool = False
    oftalmologo: OftalmologoValoracionResumen
    motivo_disparo: str
    titulo: str
    mensaje: str
    leida: bool = False
    atendida: bool = False
    fecha_creacion: datetime
    empresa_codigo: str | None = None
    empresa_nombre: str | None = None


class AtencionValorableRespuesta(BaseModel):
    consulta_clinica_id: int
    fecha_consulta: datetime
    motivo_consulta: str | None = None
    tiene_receta: bool = False
    oftalmologo: OftalmologoValoracionResumen
    notificacion: NotificacionValoracionRespuesta | None = None
    valoracion: ValoracionClinicaRespuesta | None = None
    puede_actualizar: bool = False


class ResumenValoracionesPacienteRespuesta(BaseModel):
    empresa_codigo: str | None = None
    empresa_nombre: str | None = None
    escala_minima: int = PUNTUACION_MINIMA
    escala_maxima: int = PUNTUACION_MAXIMA
    escala_etiquetas: dict[int, str] = ESCALA_VALORACION
    pendientes: list[NotificacionValoracionRespuesta] = []
    atenciones: list[AtencionValorableRespuesta] = []
    valoraciones: list[ValoracionClinicaRespuesta] = []
