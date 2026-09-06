from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import requerir_permiso
from app.database.session import get_db
from app.modules.gestion_historial_clinico.schemas.schemas import (
    HistorialClinicoRespuesta,
)
from app.modules.gestion_historial_clinico.services import service

router = APIRouter(
    prefix="/historial-clinico",
    tags=["Historial Clínico"]
)

permiso_historial_clinico = requerir_permiso("Consultar historial clínico")


@router.get(
    "/{paciente_id}",
    response_model=HistorialClinicoRespuesta,
)
def consultar_historial_clinico(
    paciente_id: int,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.consultar_historial_clinico(db, paciente_id)