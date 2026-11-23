"""Repositorio tenant de la bandeja de notificaciones.

La bandeja no depende de que el usuario tenga dispositivos o push habilitado.
El repositorio no hace ``commit``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import Notificacion
from app.modules.notificaciones_push.schemas.schemas import (
    NotificacionPersistencia,
)


def obtener_por_id(db: Session, notificacion_id: int) -> Notificacion | None:
    return db.scalar(
        select(Notificacion).where(Notificacion.id == notificacion_id)
    )


def obtener_por_id_y_usuario(
    db: Session, notificacion_id: int, usuario_id: int,
) -> Notificacion | None:
    """Busqueda acotada al propietario autenticado."""
    return db.scalar(
        select(Notificacion).where(
            Notificacion.id == notificacion_id,
            Notificacion.usuario_id == usuario_id,
        )
    )


def obtener_por_evento_clave(
    db: Session, evento_clave: str,
) -> Notificacion | None:
    return db.scalar(
        select(Notificacion).where(Notificacion.evento_clave == evento_clave)
    )


def crear(db: Session, datos: NotificacionPersistencia) -> Notificacion:
    notificacion = Notificacion(
        usuario_id=datos.usuario_id,
        tipo=datos.tipo,
        evento_clave=datos.evento_clave,
        titulo=datos.titulo,
        cuerpo=datos.cuerpo,
        datos=datos.datos,
        cita_id=datos.cita_id,
    )
    db.add(notificacion)
    db.flush()
    return notificacion


def listar_por_usuario(
    db: Session,
    usuario_id: int,
    *,
    limite: int = 50,
    solo_no_leidas: bool = False,
) -> list[Notificacion]:
    stmt = select(Notificacion).where(
        Notificacion.usuario_id == usuario_id,
        Notificacion.cancelada_en.is_(None),
    )
    if solo_no_leidas:
        stmt = stmt.where(Notificacion.leida.is_(False))
    return list(db.scalars(
        stmt.order_by(Notificacion.fecha_creacion.desc(), Notificacion.id.desc())
        .limit(limite)
    ))


def marcar_leida(
    db: Session, notificacion: Notificacion, cuando: datetime,
) -> None:
    notificacion.leida = True
    notificacion.fecha_lectura = cuando
    notificacion.fecha_actualizacion = cuando
    db.flush()


def listar_paginado_por_usuario(
    db: Session,
    usuario_id: int,
    *,
    page: int,
    page_size: int,
) -> list[Notificacion]:
    """Bandeja del propietario con orden estable y paginacion acotada.

    El orden es ``fecha_creacion DESC, id DESC``: el ID desempata fechas
    iguales para que la paginacion no repita ni omita filas.
    """
    return list(db.scalars(
        select(Notificacion)
        .where(
            Notificacion.usuario_id == usuario_id,
            Notificacion.cancelada_en.is_(None),
        )
        .order_by(Notificacion.fecha_creacion.desc(), Notificacion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ))


def contar_por_usuario(db: Session, usuario_id: int) -> int:
    return int(db.scalar(
        select(func.count())
        .select_from(Notificacion)
        .where(
            Notificacion.usuario_id == usuario_id,
            Notificacion.cancelada_en.is_(None),
        )
    ) or 0)


def cancelar(
    db: Session, notificacion: Notificacion, cuando: datetime,
) -> None:
    notificacion.cancelada_en = cuando
    notificacion.fecha_actualizacion = cuando
    db.flush()
