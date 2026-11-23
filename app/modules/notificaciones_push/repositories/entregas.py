"""Repositorio tenant del outbox de entregas por dispositivo.

Persistencia y primitivas de cola de la Fase 3C.5: reclamacion atomica,
fencing por ``lease_token`` y actualizacion condicionada del resultado. El
repositorio no hace ``commit``; la transaccion pertenece al worker.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import NotificacionEntrega
from app.modules.notificaciones_push.schemas.schemas import (
    NotificacionEntregaPersistencia,
)


def obtener_por_id(db: Session, entrega_id: int) -> NotificacionEntrega | None:
    return db.scalar(
        select(NotificacionEntrega).where(NotificacionEntrega.id == entrega_id)
    )


def obtener_por_notificacion_dispositivo(
    db: Session, notificacion_id: int, dispositivo_id: int,
) -> NotificacionEntrega | None:
    return db.scalar(
        select(NotificacionEntrega).where(
            NotificacionEntrega.notificacion_id == notificacion_id,
            NotificacionEntrega.dispositivo_id == dispositivo_id,
        )
    )


def crear(
    db: Session, datos: NotificacionEntregaPersistencia,
) -> NotificacionEntrega:
    entrega = NotificacionEntrega(
        notificacion_id=datos.notificacion_id,
        dispositivo_id=datos.dispositivo_id,
    )
    db.add(entrega)
    db.flush()
    return entrega


def crear_pendientes(
    db: Session,
    notificacion_id: int,
    dispositivos: list[int],
) -> list[NotificacionEntrega]:
    entregas = [
        NotificacionEntrega(
            notificacion_id=notificacion_id,
            dispositivo_id=dispositivo_id,
        )
        for dispositivo_id in dispositivos
    ]
    db.add_all(entregas)
    db.flush()
    return entregas


def listar_por_notificacion(
    db: Session, notificacion_id: int,
) -> list[NotificacionEntrega]:
    return list(db.scalars(
        select(NotificacionEntrega)
        .where(NotificacionEntrega.notificacion_id == notificacion_id)
        .order_by(NotificacionEntrega.id)
    ))


def listar_pendientes(
    db: Session, *, limite: int = 100,
) -> list[NotificacionEntrega]:
    """Consulta de cola de solo lectura para observabilidad temprana."""
    return list(db.scalars(
        select(NotificacionEntrega)
        .where(NotificacionEntrega.estado == "PENDIENTE")
        .order_by(NotificacionEntrega.fecha_creacion, NotificacionEntrega.id)
        .limit(limite)
    ))


# --------------------------------------------------------------------------- #
# Cola del worker (Fase 3C.5)
# --------------------------------------------------------------------------- #
def _condicion_reclamable(ahora: datetime):
    """PENDIENTE vencida o PROCESANDO con lease expirado."""
    return or_(
        and_(
            NotificacionEntrega.estado == "PENDIENTE",
            or_(
                NotificacionEntrega.proximo_intento.is_(None),
                NotificacionEntrega.proximo_intento <= ahora,
            ),
        ),
        and_(
            NotificacionEntrega.estado == "PROCESANDO",
            NotificacionEntrega.bloqueado_hasta.is_not(None),
            NotificacionEntrega.bloqueado_hasta <= ahora,
        ),
    )


def construir_consulta_reclamo(
    ahora: datetime,
    limite: int,
    *,
    dialecto: str,
):
    """Sentencia de seleccion de IDs reclamables (compilable por dialecto).

    En PostgreSQL agrega ``FOR UPDATE SKIP LOCKED``; en otros dialectos (SQLite
    de pruebas) no lo emite porque no existe.
    """
    stmt = (
        select(NotificacionEntrega.id)
        .where(_condicion_reclamable(ahora))
        .order_by(NotificacionEntrega.fecha_creacion, NotificacionEntrega.id)
        .limit(limite)
    )
    if dialecto == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    return stmt


def reclamar_lote(
    db: Session,
    *,
    ahora: datetime,
    lease_token: str,
    lease_hasta: datetime,
    limite: int,
) -> list[NotificacionEntrega]:
    """Reclama de forma atomica un lote acotado y devuelve solo lo propio.

    En PostgreSQL los IDs se seleccionan con ``FOR UPDATE SKIP LOCKED`` dentro
    de la transaccion corta del worker; la actualizacion vuelve a exigir la
    condicion de reclamo y el ``lease_token`` para no apropiarse de una fila
    que otro worker confirmo. En SQLite (pruebas) no existe ``SKIP LOCKED``:
    la atomicidad real de PostgreSQL queda pendiente de validacion aislada.
    """
    if limite <= 0:
        return []

    condicion = _condicion_reclamable(ahora)
    stmt = construir_consulta_reclamo(
        ahora, limite, dialecto=db.get_bind().dialect.name,
    )

    ids = list(db.scalars(stmt))
    if not ids:
        return []

    db.execute(
        update(NotificacionEntrega)
        .where(NotificacionEntrega.id.in_(ids), condicion)
        .values(
            estado="PROCESANDO",
            lease_token=lease_token,
            bloqueado_hasta=lease_hasta,
            fecha_actualizacion=ahora,
        )
    )
    db.flush()

    return list(db.scalars(
        select(NotificacionEntrega)
        .where(
            NotificacionEntrega.id.in_(ids),
            NotificacionEntrega.estado == "PROCESANDO",
            NotificacionEntrega.lease_token == lease_token,
        )
        .order_by(NotificacionEntrega.id)
    ))


def actualizar_resultado(
    db: Session,
    entrega_id: int,
    *,
    lease_token: str,
    valores: dict,
) -> bool:
    """Persiste el resultado solo si el lease del worker sigue vigente.

    Devuelve ``False`` cuando otro worker reclamo la entrega: el resultado
    viejo se descarta y nunca sobrescribe el nuevo claim.
    """
    resultado = db.execute(
        update(NotificacionEntrega)
        .where(
            NotificacionEntrega.id == entrega_id,
            NotificacionEntrega.estado == "PROCESANDO",
            NotificacionEntrega.lease_token == lease_token,
        )
        .values(**valores)
    )
    db.flush()
    return resultado.rowcount == 1
