"""Repositorio tenant de eventos estructurados de cita.

La clave de idempotencia es unica por tenant. El registro es idempotente: si la
clave ya existe se devuelve el evento previo sin duplicarlo. No se hace
``commit`` y no se instrumentan aun los servicios de citas (3C.4).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import CitaEvento
from app.modules.notificaciones_push.schemas.schemas import (
    CitaEventoPersistencia,
)


def obtener_por_clave(
    db: Session, clave_idempotencia: str,
) -> CitaEvento | None:
    return db.scalar(
        select(CitaEvento).where(
            CitaEvento.clave_idempotencia == clave_idempotencia,
        )
    )


def listar_por_cita(db: Session, cita_id: int) -> list[CitaEvento]:
    return list(db.scalars(
        select(CitaEvento)
        .where(CitaEvento.cita_id == cita_id)
        .order_by(CitaEvento.fecha_evento, CitaEvento.id)
    ))


def registrar(db: Session, datos: CitaEventoPersistencia) -> CitaEvento:
    existente = obtener_por_clave(db, datos.clave_idempotencia)
    if existente is not None:
        return existente
    evento = CitaEvento(
        cita_id=datos.cita_id,
        tipo=datos.tipo,
        estado_anterior=datos.estado_anterior,
        estado_nuevo=datos.estado_nuevo,
        fecha_anterior=datos.fecha_anterior,
        hora_anterior=datos.hora_anterior,
        fecha_nueva=datos.fecha_nueva,
        hora_nueva=datos.hora_nueva,
        actor_usuario_id=datos.actor_usuario_id,
        clave_idempotencia=datos.clave_idempotencia,
    )
    db.add(evento)
    db.flush()
    return evento
