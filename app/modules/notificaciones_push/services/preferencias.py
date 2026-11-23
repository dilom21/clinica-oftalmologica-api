"""Servicio de preferencias de notificacion del paciente autenticado.

``GET`` no escribe: si no existe fila devuelve los valores por defecto del
modelo. ``PUT`` crea la fila solo al guardar. Desactivar el push no borra ni
oculta las notificaciones historicas de la bandeja.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import PreferenciaNotificacion
from app.modules.notificaciones_push.repositories import (
    preferencias as repo_preferencias,
)
from app.modules.notificaciones_push.schemas.http import (
    PreferenciasActualizacionRequest,
    PreferenciasRespuesta,
)

MENSAJE_PREFERENCIAS_NO_DISPONIBLE = "No se pudieron actualizar las preferencias"

# Valores por defecto del modelo (server_default = TRUE para cada canal).
CAMPOS_PREFERENCIA = (
    "push_habilitado",
    "citas_creadas",
    "citas_confirmadas",
    "citas_reprogramadas",
    "citas_canceladas",
    "recordatorios",
)

# Fase 3C.5: preferencia especifica por tipo de notificacion. El modelo real
# solo tiene estos campos; ``ACTUALIZACION_OPERATIVA`` y ``RECORDATORIO_CITA``
# se comportan segun ``push_habilitado`` (recordatorios tiene ademas su propio
# campo, que se respeta).
PREFERENCIA_POR_TIPO = {
    "CITA_CREADA": "citas_creadas",
    "CITA_CONFIRMADA": "citas_confirmadas",
    "CITA_REPROGRAMADA": "citas_reprogramadas",
    "CITA_CANCELADA": "citas_canceladas",
    "RECORDATORIO_CITA": "recordatorios",
}


def push_permitido(
    preferencia: PreferenciaNotificacion | None,
    tipo: str | None,
) -> bool:
    """Decide si corresponde encolar/enviar push para un tipo de notificacion.

    Sin fila de preferencia se aplican los valores por defecto del modelo
    (todos los canales ``TRUE``). La bandeja in-app no depende de esto.
    """
    if preferencia is None:
        return True
    if not bool(getattr(preferencia, "push_habilitado", True)):
        return False
    campo = PREFERENCIA_POR_TIPO.get(str(tipo or "").strip().upper())
    if campo is None:
        return True
    return bool(getattr(preferencia, campo, True))



def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _respuesta(
    preferencia: PreferenciaNotificacion | None,
) -> PreferenciasRespuesta:
    if preferencia is None:
        return PreferenciasRespuesta(
            **{campo: True for campo in CAMPOS_PREFERENCIA},
            fecha_actualizacion=None,
        )
    return PreferenciasRespuesta(
        **{campo: bool(getattr(preferencia, campo)) for campo in CAMPOS_PREFERENCIA},
        fecha_actualizacion=preferencia.fecha_actualizacion,
    )


def obtener_preferencias(
    db: Session, *, usuario_id: int,
) -> PreferenciasRespuesta:
    preferencia = repo_preferencias.obtener_por_usuario(db, usuario_id)
    return _respuesta(preferencia)


def actualizar_preferencias(
    db: Session,
    *,
    usuario_id: int,
    cambios: PreferenciasActualizacionRequest,
) -> PreferenciasRespuesta:
    ahora = _ahora()
    try:
        preferencia = repo_preferencias.obtener_por_usuario(db, usuario_id)
        if preferencia is None:
            preferencia = repo_preferencias.crear_por_defecto(db, usuario_id)
        aplicados = cambios.cambios()
        if aplicados:
            repo_preferencias.aplicar_cambios(
                db, preferencia, aplicados, ahora,
            )
        db.commit()
        db.refresh(preferencia)
    except IntegrityError:
        # Carrera al crear la fila unica por usuario: releer y aplicar.
        db.rollback()
        try:
            preferencia = repo_preferencias.obtener_por_usuario(db, usuario_id)
            if preferencia is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=MENSAJE_PREFERENCIAS_NO_DISPONIBLE,
                )
            aplicados = cambios.cambios()
            if aplicados:
                repo_preferencias.aplicar_cambios(
                    db, preferencia, aplicados, ahora,
                )
            db.commit()
            db.refresh(preferencia)
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=MENSAJE_PREFERENCIAS_NO_DISPONIBLE,
            ) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=MENSAJE_PREFERENCIAS_NO_DISPONIBLE,
        ) from None

    return _respuesta(preferencia)
