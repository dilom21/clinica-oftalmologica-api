from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_LECTURA,
    get_db,
    obtener_administrador_actual,
    requerir_permiso,
)
from app.modules.ia_predictiva.schemas import FuenteEtiquetasRespuesta, PronosticoDemandaRespuesta
from app.modules.ia_predictiva.services import generar_pronostico_semanal, real_model_status

router = APIRouter(prefix="/ia-predictiva", tags=["IA Predictiva"])
permiso_pronostico = requerir_permiso("Generar reportes", ACCION_LECTURA)
permiso_inasistencia = requerir_permiso("Generar reportes", ACCION_LECTURA)


@router.get("/demanda/semanal", response_model=PronosticoDemandaRespuesta)
def pronostico_demanda_semanal(
    horizonte: int = Query(default=4, ge=4, le=12),
    fecha_corte: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
    _administrador=Depends(obtener_administrador_actual),
    _permiso=Depends(permiso_pronostico),
):
    """Pronostica citas creadas; nunca citas programadas ni atendidas."""
    return generar_pronostico_semanal(
        db, horizonte=horizonte, fecha_corte=fecha_corte,
    )


@router.get("/inasistencia/estado", response_model=FuenteEtiquetasRespuesta)
def estado_modelo_inasistencia(
    _db: Session = Depends(get_db),
    _administrador=Depends(obtener_administrador_actual),
    _permiso=Depends(permiso_inasistencia),
):
    """Expone el bloqueo verificable; nunca fabrica predicciones reales."""
    return real_model_status()
