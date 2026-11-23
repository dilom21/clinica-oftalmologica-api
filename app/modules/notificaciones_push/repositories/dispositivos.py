"""Repositorio tenant de dispositivos push.

El token FCM se recibe como secreto operativo: no se imprime, no se registra en
logs y no se devuelve en ninguna consulta publica. El repositorio no hace
``commit``; la transaccion pertenece a la capa de servicio.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import DispositivoPush
from app.modules.notificaciones_push.schemas.schemas import (
    DispositivoPushPersistencia,
)


def obtener_por_id(db: Session, dispositivo_id: int) -> DispositivoPush | None:
    return db.scalar(
        select(DispositivoPush).where(DispositivoPush.id == dispositivo_id)
    )


def obtener_por_id_y_usuario(
    db: Session, dispositivo_id: int, usuario_id: int,
) -> DispositivoPush | None:
    """Busqueda acotada al propietario autenticado (aislamiento por usuario)."""
    return db.scalar(
        select(DispositivoPush).where(
            DispositivoPush.id == dispositivo_id,
            DispositivoPush.usuario_id == usuario_id,
        )
    )


def obtener_inactivo_por_usuario_y_token(
    db: Session, usuario_id: int, token_fcm: str,
) -> DispositivoPush | None:
    return db.scalar(
        select(DispositivoPush)
        .where(
            DispositivoPush.usuario_id == usuario_id,
            DispositivoPush.token_fcm == token_fcm,
            DispositivoPush.activo.is_(False),
        )
        .order_by(DispositivoPush.id.desc())
    )


def obtener_por_token(db: Session, token_fcm: str) -> DispositivoPush | None:
    return db.scalar(
        select(DispositivoPush).where(DispositivoPush.token_fcm == token_fcm)
    )


def obtener_activo_por_token(
    db: Session, token_fcm: str,
) -> DispositivoPush | None:
    return db.scalar(
        select(DispositivoPush).where(
            DispositivoPush.token_fcm == token_fcm,
            DispositivoPush.activo.is_(True),
        )
    )


def listar_activos_por_usuario(
    db: Session, usuario_id: int,
) -> list[DispositivoPush]:
    return list(db.scalars(
        select(DispositivoPush)
        .where(
            DispositivoPush.usuario_id == usuario_id,
            DispositivoPush.activo.is_(True),
        )
        .order_by(DispositivoPush.id)
    ))


def registrar(
    db: Session, datos: DispositivoPushPersistencia,
) -> DispositivoPush:
    dispositivo = DispositivoPush(
        usuario_id=datos.usuario_id,
        token_fcm=datos.token_fcm,
        plataforma=datos.plataforma,
        instalacion_id=datos.instalacion_id,
        app_version=datos.app_version,
        nombre_dispositivo=datos.nombre_dispositivo,
        activo=True,
    )
    db.add(dispositivo)
    db.flush()
    return dispositivo


def revocar(db: Session, dispositivo: DispositivoPush, cuando: datetime) -> None:
    """Revocacion logica: conserva el historial de entregas."""
    dispositivo.activo = False
    dispositivo.revocado_en = cuando
    dispositivo.fecha_actualizacion = cuando
    db.flush()


def marcar_uso(db: Session, dispositivo: DispositivoPush, cuando: datetime) -> None:
    dispositivo.fecha_ultimo_uso = cuando
    dispositivo.fecha_actualizacion = cuando
    db.flush()


def _aplicar_metadatos(
    dispositivo: DispositivoPush,
    datos: DispositivoPushPersistencia,
    cuando: datetime,
) -> None:
    dispositivo.token_fcm = datos.token_fcm
    dispositivo.plataforma = datos.plataforma
    dispositivo.instalacion_id = datos.instalacion_id
    dispositivo.app_version = datos.app_version
    dispositivo.nombre_dispositivo = datos.nombre_dispositivo
    dispositivo.fecha_actualizacion = cuando
    dispositivo.fecha_ultimo_uso = cuando


def actualizar_metadatos(
    db: Session,
    dispositivo: DispositivoPush,
    datos: DispositivoPushPersistencia,
    cuando: datetime,
) -> DispositivoPush:
    """Actualiza un dispositivo activo ya registrado (reintento idempotente)."""
    _aplicar_metadatos(dispositivo, datos, cuando)
    db.flush()
    return dispositivo


def reactivar(
    db: Session,
    dispositivo: DispositivoPush,
    datos: DispositivoPushPersistencia,
    cuando: datetime,
) -> DispositivoPush:
    """Reactiva el mismo dispositivo tras una revocacion previa del propietario."""
    _aplicar_metadatos(dispositivo, datos, cuando)
    dispositivo.activo = True
    dispositivo.revocado_en = None
    db.flush()
    return dispositivo
