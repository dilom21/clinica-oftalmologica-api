"""Repositorio tenant de preferencias de notificacion (una fila por usuario)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import PreferenciaNotificacion


def obtener_por_usuario(
    db: Session, usuario_id: int,
) -> PreferenciaNotificacion | None:
    return db.scalar(
        select(PreferenciaNotificacion).where(
            PreferenciaNotificacion.usuario_id == usuario_id,
        )
    )


def crear_por_defecto(
    db: Session, usuario_id: int,
) -> PreferenciaNotificacion:
    """Crea la fila con todos los canales activos (valores por defecto)."""
    preferencia = PreferenciaNotificacion(usuario_id=usuario_id)
    db.add(preferencia)
    db.flush()
    return preferencia


def aplicar_cambios(
    db: Session,
    preferencia: PreferenciaNotificacion,
    cambios: dict[str, bool],
    cuando: datetime,
) -> PreferenciaNotificacion:
    for campo, valor in cambios.items():
        setattr(preferencia, campo, valor)
    preferencia.fecha_actualizacion = cuando
    db.flush()
    return preferencia
