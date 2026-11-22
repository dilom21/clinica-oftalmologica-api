"""Servicio de bandeja de notificaciones del paciente autenticado.

Solo se consultan y modifican filas cuyo ``usuario_id`` coincide con el
usuario de la sesion tenant. El JSON ``datos`` se filtra por allowlist antes
de salir del backend. La bandeja no depende de dispositivos ni de preferencias.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import Notificacion
from app.modules.notificaciones_push.repositories import (
    notificaciones as repo_notificaciones,
)
from app.modules.notificaciones_push.schemas.http import (
    BandejaRespuesta,
    NotificacionBandejaItem,
    NotificacionLeidaRespuesta,
    filtrar_datos_navegacion,
)

MENSAJE_RECURSO_NO_ENCONTRADO = "Recurso no encontrado"
MENSAJE_LECTURA_NO_DISPONIBLE = "No se pudo actualizar la notificación"

LIMITE_MAXIMO_PAGINA = 50


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _item(notificacion: Notificacion) -> NotificacionBandejaItem:
    return NotificacionBandejaItem(
        id=notificacion.id,
        tipo=notificacion.tipo,
        titulo=notificacion.titulo,
        cuerpo=notificacion.cuerpo,
        datos=filtrar_datos_navegacion(notificacion.datos),
        cita_id=notificacion.cita_id,
        leida=bool(notificacion.leida),
        fecha_lectura=notificacion.fecha_lectura,
        fecha_creacion=notificacion.fecha_creacion,
    )


def listar_bandeja(
    db: Session,
    *,
    usuario_id: int,
    page: int,
    page_size: int,
) -> BandejaRespuesta:
    if page < 1:
        raise HTTPException(
            status_code=422,
            detail="page debe ser mayor o igual a 1",
        )
    if page_size < 1 or page_size > LIMITE_MAXIMO_PAGINA:
        raise HTTPException(
            status_code=422,
            detail=f"page_size debe estar entre 1 y {LIMITE_MAXIMO_PAGINA}",
        )

    items = repo_notificaciones.listar_paginado_por_usuario(
        db, usuario_id, page=page, page_size=page_size,
    )
    total = repo_notificaciones.contar_por_usuario(db, usuario_id)
    total_pages = (total + page_size - 1) // page_size if total else 0

    return BandejaRespuesta(
        items=[_item(notificacion) for notificacion in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def marcar_notificacion_leida(
    db: Session,
    *,
    usuario_id: int,
    notificacion_id: int,
) -> NotificacionLeidaRespuesta:
    notificacion = repo_notificaciones.obtener_por_id_y_usuario(
        db, notificacion_id, usuario_id,
    )
    if notificacion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=MENSAJE_RECURSO_NO_ENCONTRADO,
        )

    if not notificacion.leida:
        try:
            repo_notificaciones.marcar_leida(db, notificacion, _ahora())
            db.commit()
            db.refresh(notificacion)
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MENSAJE_LECTURA_NO_DISPONIBLE,
            ) from None

    return NotificacionLeidaRespuesta(
        id=notificacion.id,
        leida=bool(notificacion.leida),
        fecha_lectura=notificacion.fecha_lectura,
    )
