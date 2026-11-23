"""Consultas tenant para el productor de recordatorios de citas."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.notificaciones_push.models.models import Notificacion


def listar_citas_elegibles(
    db: Session, *, fecha_desde: date, fecha_hasta: date, limite: int,
) -> list[Cita]:
    return list(db.scalars(
        select(Cita)
        .where(
            Cita.fecha.between(fecha_desde, fecha_hasta),
            Cita.estado.in_(("PROGRAMADA", "CONFIRMADA")),
        )
        .order_by(Cita.fecha, Cita.hora_inicio, Cita.id)
        .limit(limite)
    ))


def listar_recordatorios_de_cita(db: Session, cita_id: int) -> list[Notificacion]:
    return list(db.scalars(
        select(Notificacion).where(
            Notificacion.cita_id == cita_id,
            Notificacion.tipo == "RECORDATORIO_CITA",
        )
    ))
