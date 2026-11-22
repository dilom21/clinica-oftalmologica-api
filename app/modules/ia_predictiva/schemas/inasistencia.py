from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


EstadoModeloInasistencia = Literal[
    "MODEL_READY", "MODEL_NOT_READY", "LABELS_INSUFFICIENT",
    "LABELS_AMBIGUOUS", "TRAINING_DISABLED", "PREDICTION_UNAVAILABLE",
]


class FuenteEtiquetasRespuesta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estado: EstadoModeloInasistencia
    fuente: Literal["CITA_ESTADO_Y_BITACORA"] = "CITA_ESTADO_Y_BITACORA"
    calidad: Literal["BLOQUEADO_POR_DATOS"] = "BLOQUEADO_POR_DATOS"
    version_regla: str = "inasistencia-label-v1"
    q09_entrenamiento: Literal["PROHIBIDO"] = "PROHIBIDO"
    modelo_habilitado: bool = False
    prediccion_habilitada: bool = False
    algoritmos_disponibles: list[str] = Field(default_factory=list)
    variables_permitidas: list[str] = Field(default_factory=list)
    variables_excluidas: list[str] = Field(default_factory=list)
    razones_bloqueo: list[str] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)
