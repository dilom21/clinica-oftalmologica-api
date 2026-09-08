from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import (
    HistorialClinicoRespuesta,
)


def consultar_historial_clinico(
    db: Session,
    paciente_id: int,
) -> HistorialClinicoRespuesta:
    paciente, historial = repo.obtener_paciente_con_historial(db, paciente_id)

    if paciente is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paciente no encontrado",
        )

    return HistorialClinicoRespuesta(
        paciente=paciente,
        historial=historial,
    )