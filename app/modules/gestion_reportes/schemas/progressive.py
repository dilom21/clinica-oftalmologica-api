from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.gestion_agenda_citas.schemas.schemas import ESTADOS_CITA_VALIDOS


class Granularidad(str, Enum):
    DIA = "dia"
    SEMANA = "semana"
    MES = "mes"


class FiltrosProgresivos(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fecha_desde: date
    fecha_hasta: date
    granularidad: Granularidad = Granularidad.DIA
    estado: str | None = None

    @model_validator(mode="after")
    def validar_rango(self):
        if self.fecha_desde > self.fecha_hasta:
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        if (self.fecha_hasta - self.fecha_desde).days > 366:
            raise ValueError("El rango máximo es de 366 días")
        if self.estado is not None:
            self.estado = self.estado.strip().upper()
            if self.estado not in ESTADOS_CITA_VALIDOS:
                raise ValueError("Estado de cita inválido")
        return self


class EstadoDato(str, Enum):
    OK = "OK"
    ZERO = "ZERO"
    SUPPRESSED = "SUPPRESSED"
    PARTIAL = "PARTIAL"
    NOT_COVERED = "NOT_COVERED"


class ValorAgregado(BaseModel):
    valor: int | None
    estado_dato: EstadoDato


class PuntoSerie(ValorAgregado):
    periodo_inicio: date
    periodo_fin: date


class IndicadorKPI(ValorAgregado):
    indicador: str
    etiqueta: str
    calificador: str | None = None
    advertencias: list[str] = Field(default_factory=list)


class RespuestaKPIs(BaseModel):
    fecha_desde: date
    fecha_hasta: date
    zona_horaria: str
    indicadores: list[IndicadorKPI]
    distribucion_estados: list[IndicadorKPI]
    advertencias: list[str]


class RespuestaSerie(BaseModel):
    indicador: str
    etiqueta: str
    granularidad: Granularidad
    fecha_desde: date
    fecha_hasta: date
    zona_horaria: str
    puntos: list[PuntoSerie]
    advertencias: list[str]


class RespuestaComparacion(BaseModel):
    indicador: str
    actual: ValorAgregado
    anterior: ValorAgregado
    variacion_absoluta: int | None
    variacion_porcentual: float | None
    periodo_actual: tuple[date, date]
    periodo_anterior: tuple[date, date]
    advertencias: list[str]


class SolicitudExportacion(FiltrosProgresivos):
    indicador: Literal[
        "citas_creadas", "citas_programadas", "citas_atendidas",
        "cancelaciones", "reprogramaciones_proxy", "inasistencias_descriptivas",
        "consultas_clinicas",
    ] = "citas_creadas"
