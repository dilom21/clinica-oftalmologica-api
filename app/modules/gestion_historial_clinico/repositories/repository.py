from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import (
    AntecedenteClinico,
    HistorialClinico,
)
from app.modules.gestion_pacientes.models.models import Paciente


def obtener_paciente_con_historial(
    db: Session,
    paciente_id: int,
):
    paciente = db.scalar(
        select(Paciente).where(Paciente.id == paciente_id)
    )

    if paciente is None:
        return None, None

    historial = db.scalar(
        select(HistorialClinico)
        .options(
            selectinload(
                HistorialClinico.antecedentes.and_(
                    AntecedenteClinico.estado.is_(True)
                )
            )
        )
        .where(
            HistorialClinico.paciente_id == paciente_id,
            HistorialClinico.estado.is_(True),
        )
    )

    return paciente, historial