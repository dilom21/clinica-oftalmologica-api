from sqlalchemy import select

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_historial_clinico.models.models import ConsultaClinica


def obtener_citas_agregables(db, desde_utc, hasta_utc, fecha_desde, fecha_hasta):
    return db.execute(
        select(Cita.fecha_registro, Cita.fecha, Cita.estado, Cita.fecha_actualizacion)
        .where(
            (Cita.fecha_registro >= desde_utc) & (Cita.fecha_registro < hasta_utc)
            | (Cita.fecha >= fecha_desde) & (Cita.fecha <= fecha_hasta)
        )
    ).all()


def obtener_fechas_consulta(db, desde_utc, hasta_utc):
    return db.execute(
        select(ConsultaClinica.fecha_consulta).where(
            ConsultaClinica.fecha_consulta >= desde_utc,
            ConsultaClinica.fecha_consulta < hasta_utc,
        )
    ).scalars().all()
