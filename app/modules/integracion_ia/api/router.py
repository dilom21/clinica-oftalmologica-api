"""Router del módulo de integración IA.

Expone dos funciones de asistencia clínica protegidas con JWT, permiso
"Usar asistencia clínica IA" (LECTURA) y ownership de la consulta:

    POST /ia/consultas/{consulta_id}/analizar
    POST /ia/consultas/{consulta_id}/mejorar-redaccion-diagnostico
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_LECTURA,
    obtener_administrador_actual,
    requerir_permiso,
)
from app.core.dependencies import get_db
from app.modules.integracion_ia.schemas.schemas import (
    AnalisisConsultaIARespuesta,
    MejorarRedaccionDiagnosticoIARequest,
    MejorarRedaccionDiagnosticoIARespuesta,
    InterpretarReporteIARequest,
    InterpretacionReporteIARespuesta,
)
from app.modules.integracion_ia.services import service

router = APIRouter(
    prefix="/ia",
    tags=["Inteligencia Artificial"],
)


permiso_asistencia_ia = requerir_permiso(
    "Usar asistencia clínica IA", ACCION_LECTURA,
)
permiso_generar_reportes = requerir_permiso("Generar reportes", ACCION_LECTURA)


@router.post(
    "/consultas/{consulta_id}/analizar",
    response_model=AnalisisConsultaIARespuesta,
    status_code=200,
)
def analizar_consulta(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_asistencia_ia),
):
    return service.analizar_consulta(db, consulta_id, usuario)


@router.post(
    "/consultas/{consulta_id}/mejorar-redaccion-diagnostico",
    response_model=MejorarRedaccionDiagnosticoIARespuesta,
    status_code=200,
)
def mejorar_redaccion_diagnostico(
    datos: MejorarRedaccionDiagnosticoIARequest,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_asistencia_ia),
):
    return service.mejorar_redaccion_diagnostico(
        db, consulta_id, datos, usuario,
    )


@router.post(
    "/reportes/interpretar",
    response_model=InterpretacionReporteIARespuesta,
    status_code=200,
)
def interpretar_reporte(
    datos: InterpretarReporteIARequest,
    db: Session = Depends(get_db),
    usuario=Depends(obtener_administrador_actual),
    _permiso=Depends(permiso_generar_reportes),
):
    return service.interpretar_reporte(db, datos, usuario)
