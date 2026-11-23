from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EstadoPronostico = Literal[
    "READY", "INSUFFICIENT_HISTORY", "INSUFFICIENT_DATA", "FORECAST_UNAVAILABLE"
]


class MetricasBacktesting(BaseModel):
    mae: float
    rmse: float
    wape: float | None
    sesgo: float
    observaciones: int


class ResultadoMetodo(BaseModel):
    metodo: str
    metricas: MetricasBacktesting


class CalidadHistorica(BaseModel):
    semanas_ok: int = 0
    semanas_zero: int = 0
    semanas_suppressed: int = 0
    semanas_partial: int = 0
    semanas_not_covered: int = 0


class PronosticoSemana(BaseModel):
    inicio_semana: date
    fin_semana: date
    valor_pronosticado: float = Field(ge=0)
    limite_inferior: float = Field(ge=0)
    limite_superior: float = Field(ge=0)
    calidad_datos: Literal["FORECAST"] = "FORECAST"


class PronosticoDemandaRespuesta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoPronostico
    objetivo: Literal["CITAS_CREADAS"] = "CITAS_CREADAS"
    zona_horaria: str
    fecha_corte: datetime
    horizonte_semanas: int = Field(ge=4, le=12)
    metodo: str | None = None
    version_modelo: str = "demanda-semanal-v1"
    rango_historico_inicio: date | None = None
    rango_historico_fin: date | None = None
    calidad_historica: CalidadHistorica
    metricas: MetricasBacktesting | None = None
    metricas_baseline: MetricasBacktesting | None = None
    comparacion_metodos: list[ResultadoMetodo] = Field(default_factory=list)
    pronosticos: list[PronosticoSemana] = Field(default_factory=list)
    intervalo: str | None = None
    advertencias: list[str] = Field(default_factory=list)
