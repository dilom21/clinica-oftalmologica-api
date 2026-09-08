from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_ESCRITURA, ACCION_LECTURA, requerir_permiso
from app.database.session import get_db
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
    AntecedenteClinicoRespuesta,
    HistorialClinicoRespuesta,
)
from app.modules.gestion_historial_clinico.services import service

router = APIRouter(
    prefix="/historial-clinico",
    tags=["Historial Clínico"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de historial clínico funcionando"}


permiso_historial_clinico = requerir_permiso(
    "Consultar historial clínico", ACCION_LECTURA,
)
permiso_gestionar_antecedentes = requerir_permiso(
    "Gestionar antecedentes clínicos", ACCION_ESCRITURA,
)


@router.post(
    "/antecedentes", response_model=AntecedenteClinicoRespuesta, status_code=201,
)
def crear_antecedente(
    datos: AntecedenteClinicoCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_gestionar_antecedentes),
):
    return service.crear_antecedente(db, datos, usuario)


@router.put("/antecedentes/{antecedente_id}", response_model=AntecedenteClinicoRespuesta)
def actualizar_antecedente(
    datos: AntecedenteClinicoActualizar,
    antecedente_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_gestionar_antecedentes),
):
    return service.actualizar_antecedente(db, antecedente_id, datos, usuario)


@router.get("/{paciente_id}", response_model=HistorialClinicoRespuesta)
def consultar_historial_clinico(
    paciente_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.consultar_historial_clinico(db, paciente_id)
