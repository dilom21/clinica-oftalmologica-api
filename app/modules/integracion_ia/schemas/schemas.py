"""Schemas Pydantic del módulo de integración IA.

Separar el contenido que devuelve el modelo (validado y normalizado) de los
schemas de request/response HTTP permite garantizar respuestas estables y
evitar que la IA altere el nombre del diagnóstico o el formato acordado.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.gestion_reportes.schemas.schemas import FiltroReporte, OrdenReporte


ADVERTENCIA_ANALISIS = (
    "Contenido generado por IA para apoyo profesional. "
    "Debe ser validado por el oftalmólogo."
)

ADVERTENCIA_MEJORA = (
    "Borrador generado por IA. Debe ser revisado por el oftalmólogo "
    "antes de registrarse."
)

# Límites que evitan respuestas desproporcionadas del modelo.
MAX_TEXTO = 4000
MAX_ITEMS_LISTA = 20
MAX_NOMBRE_DIAGNOSTICO = 150
MAX_TEXTO_REPORTE_VOZ = 1000


class AnalisisConsultaIARespuesta(BaseModel):
    """Respuesta estructurada del análisis asistido de una consulta clínica."""

    resumen_clinico: str = Field(min_length=1, max_length=MAX_TEXTO)
    hallazgos_relevantes: list[str] = Field(
        default_factory=list, max_length=MAX_ITEMS_LISTA,
    )
    aspectos_a_evaluar: list[str] = Field(
        default_factory=list, max_length=MAX_ITEMS_LISTA,
    )
    hipotesis_orientativas: list[str] = Field(
        default_factory=list, max_length=MAX_ITEMS_LISTA,
    )
    advertencia: str = Field(
        default=ADVERTENCIA_ANALISIS, min_length=1, max_length=1000,
    )

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class MejorarRedaccionDiagnosticoIARequest(BaseModel):
    """Body del endpoint de mejora de redacción diagnóstica.

    Solo acepta `nombre` y `descripcion`. Cualquier identificador clínico o
    campo adicional se rechaza con 422 (`extra="forbid"`).
    """

    nombre: str = Field(min_length=1, max_length=MAX_NOMBRE_DIAGNOSTICO)
    descripcion: str = Field(min_length=1, max_length=MAX_TEXTO)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("nombre", "descripcion", mode="before")
    @classmethod
    def texto_no_vacio(cls, valor):
        if isinstance(valor, str):
            limpio = valor.strip()
            if not limpio:
                raise ValueError("El texto no puede estar vacío")
            return limpio
        return valor


class MejorarRedaccionDiagnosticoIAContenido(BaseModel):
    """Contenido mínimo que debe devolver el modelo para la mejora."""

    descripcion_mejorada: str = Field(min_length=1, max_length=MAX_TEXTO)

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class MejorarRedaccionDiagnosticoIARespuesta(BaseModel):
    """Respuesta HTTP de la mejora de redacción.

    El `nombre` y `descripcion_original` provienen del request (no del modelo):
    la IA solo puede mejorar la redacción, nunca renombrar el diagnóstico.
    """

    nombre: str
    descripcion_original: str
    descripcion_mejorada: str
    advertencia: str


class InterpretarReporteIARequest(BaseModel):
    """Texto ya transcrito que se convertirá en configuración de reporte."""

    texto: str = Field(min_length=1, max_length=MAX_TEXTO_REPORTE_VOZ)

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("texto", mode="before")
    @classmethod
    def texto_no_vacio(cls, valor):
        if isinstance(valor, str):
            limpio = valor.strip()
            if not limpio:
                raise ValueError("El texto no puede estar vacío")
            return limpio
        return valor


class InterpretacionReporteIARespuesta(BaseModel):
    """Configuración estable y no ejecutable propuesta por la IA."""

    dataset: str
    columnas: list[str] = Field(min_length=1)
    filtros: list[FiltroReporte] = Field(default_factory=list)
    orden: list[OrdenReporte] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=50, ge=1, le=200)
    requiere_aclaracion: bool = False
    pregunta_aclaracion: str | None = None
    accion_sugerida: Literal["previsualizar", "exportar"] = "previsualizar"
    formato_sugerido: Literal["xlsx", "pdf", "csv", "html"] | None = None

    model_config = ConfigDict(extra="forbid")
