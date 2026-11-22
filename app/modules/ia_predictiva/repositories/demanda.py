from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Cita


def listar_fechas_registro_citas(
    db: Session,
    *,
    antes_de: datetime,
) -> list[datetime]:
    """Devuelve únicamente la variable temporal agregable del tenant actual."""
    stmt = (
        select(Cita.fecha_registro)
        .where(Cita.fecha_registro < antes_de)
        .order_by(Cita.fecha_registro)
    )
    return list(db.scalars(stmt).all())
