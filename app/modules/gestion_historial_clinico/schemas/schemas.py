from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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


# =========================================================
# CU17 - REGISTRAR TRATAMIENTOS, INDICACIONES Y RECETAS
#
# Ninguno de estos schemas acepta campos controlados por el servidor
# (`id`, `consulta_clinica_id` del cuerpo, `estado`, `fecha_*` generadas,
# `oftalmologo_id`, `usuario_id`): el actor y la consulta provienen del token
# y de la URL. `extra="forbid"` los rechaza con 422.
# =========================================================


def _texto_opcional_o_none(valor):
    """Cadena vacía o solo espacios -> `None`."""
    if isinstance(valor, str):
        limpio = valor.strip()
        return limpio or None
    return valor


def _texto_obligatorio(valor, mensaje: str):
    """Texto obligatorio: se recorta y no puede quedar vacío."""
    if isinstance(valor, str):
        limpio = valor.strip()
        if not limpio:
            raise ValueError(mensaje)
        return limpio
    return valor


class TratamientoCrear(BaseModel):
    descripcion: str = Field(min_length=1)
    observaciones: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("descripcion", mode="before")
    @classmethod
    def normalizar_descripcion(cls, valor):
        return _texto_obligatorio(
            valor, "La descripción del tratamiento no puede estar vacía",
        )

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        return _texto_opcional_o_none(valor)

    @model_validator(mode="after")
    def validar_rango_de_fechas(self):
        if (
            self.fecha_inicio is not None
            and self.fecha_fin is not None
            and self.fecha_fin < self.fecha_inicio
        ):
            raise ValueError(
                "fecha_fin no puede ser anterior a fecha_inicio"
            )
        return self


class TratamientoRespuesta(BaseModel):
    id: int
    consulta_clinica_id: int
    descripcion: str
    observaciones: str | None
    fecha_inicio: date | None
    fecha_fin: date | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class IndicacionCrear(BaseModel):
    descripcion: str = Field(min_length=1)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("descripcion", mode="before")
    @classmethod
    def normalizar_descripcion(cls, valor):
        return _texto_obligatorio(
            valor, "La descripción de la indicación no puede estar vacía",
        )


class IndicacionRespuesta(BaseModel):
    id: int
    consulta_clinica_id: int
    descripcion: str
    fecha_registro: datetime

    model_config = ConfigDict(from_attributes=True)


class DetalleRecetaCrear(BaseModel):
    medicamento: str = Field(min_length=1, max_length=150)
    presentacion: str | None = Field(default=None, max_length=100)
    dosis: str | None = Field(default=None, max_length=100)
    frecuencia: str | None = Field(default=None, max_length=100)
    duracion: str | None = Field(default=None, max_length=100)
    indicaciones: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("medicamento", mode="before")
    @classmethod
    def normalizar_medicamento(cls, valor):
        return _texto_obligatorio(
            valor, "El medicamento no puede estar vacío",
        )

    @field_validator(
        "presentacion", "dosis", "frecuencia", "duracion", "indicaciones",
        mode="before",
    )
    @classmethod
    def normalizar_texto_opcional(cls, valor):
        return _texto_opcional_o_none(valor)


class DetalleRecetaRespuesta(BaseModel):
    id: int
    receta_id: int
    medicamento: str
    presentacion: str | None
    dosis: str | None
    frecuencia: str | None
    duracion: str | None
    indicaciones: str | None

    model_config = ConfigDict(from_attributes=True)


class RecetaCrear(BaseModel):
    observaciones: str | None = None
    # Una receta debe contener al menos un medicamento: la lista vacía es 422.
    detalles: list[DetalleRecetaCrear] = Field(min_length=1)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        return _texto_opcional_o_none(valor)


class RecetaRespuesta(BaseModel):
    id: int
    consulta_clinica_id: int
    fecha_emision: datetime
    observaciones: str | None
    estado: bool
    detalles: list[DetalleRecetaRespuesta]

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# CU18 - REGISTRAR RESULTADOS DE EXAMENES OFTALMOLOGICOS
# =========================================================


class ExamenOftalmologicoCrear(BaseModel):
    nombre_examen: str = Field(min_length=1, max_length=150)
    observaciones: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("nombre_examen", mode="before")
    @classmethod
    def normalizar_nombre_examen(cls, valor):
        return _texto_obligatorio(
            valor, "El nombre del examen no puede estar vacio",
        )

    @field_validator("observaciones", mode="before")
    @classmethod
    def normalizar_observaciones(cls, valor):
        return _texto_opcional_o_none(valor)


class ExamenOftalmologicoRespuesta(BaseModel):
    id: int
    consulta_clinica_id: int
    nombre_examen: str
    fecha_solicitud: datetime
    observaciones: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class ResultadoExamenCrear(BaseModel):
    resultado: str = Field(min_length=1)
    archivo_url: str | None = None

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("resultado", mode="before")
    @classmethod
    def normalizar_resultado(cls, valor):
        return _texto_obligatorio(
            valor, "El resultado del examen no puede estar vacio",
        )

    @field_validator("archivo_url", mode="before")
    @classmethod
    def normalizar_archivo_url(cls, valor):
        return _texto_opcional_o_none(valor)


class ResultadoExamenRespuesta(BaseModel):
    id: int
    examen_id: int
    fecha_resultado: datetime
    resultado: str
    archivo_url: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


class ExamenConResultadosRespuesta(ExamenOftalmologicoRespuesta):
    resultados: list[ResultadoExamenRespuesta]
