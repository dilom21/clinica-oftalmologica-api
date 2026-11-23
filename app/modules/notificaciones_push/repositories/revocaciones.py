"""Repositorio tenant del outbox de revocaciones globales (Fase 3C.5.2).

Persistencia durable e idempotente de las solicitudes que el worker FCM crea
cuando el proveedor informa inequivocamente que un token dejo de ser valido.

Reglas:

* vive en la base **tenant**; el worker no abre el Control Plane;
* solo guarda la huella SHA-256 calculada en backend, nunca el token;
* ``(dispositivo_id, token_fingerprint)`` es la clave de idempotencia;
* reclamacion atomica con ``lease_token`` y fencing, igual que el outbox de
  entregas de 3C.5 (``FOR UPDATE SKIP LOCKED`` solo en PostgreSQL);
* el repositorio **no** hace ``commit``: la transaccion pertenece al worker o
  al procesador de revocaciones.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import RevocacionPushGlobal


def obtener_por_id(
    db: Session, revocacion_id: int,
) -> RevocacionPushGlobal | None:
    return db.scalar(
        select(RevocacionPushGlobal).where(
            RevocacionPushGlobal.id == revocacion_id,
        )
    )


def obtener_por_dispositivo_huella(
    db: Session, dispositivo_id: int, token_fingerprint: str,
) -> RevocacionPushGlobal | None:
    return db.scalar(
        select(RevocacionPushGlobal).where(
            RevocacionPushGlobal.dispositivo_id == dispositivo_id,
            RevocacionPushGlobal.token_fingerprint == token_fingerprint,
        )
    )


def crear_si_no_existe(
    db: Session,
    *,
    dispositivo_id: int,
    usuario_id: int,
    token_fingerprint: str,
    cuando: datetime,
) -> tuple[RevocacionPushGlobal, bool]:
    """Crea la solicitud una sola vez. Devuelve ``(fila, creada)``.

    Un duplicado concurrente se resuelve con un savepoint: se relee la fila ya
    persistida por la otra transaccion. Nunca hace ``commit``.
    """
    existente = obtener_por_dispositivo_huella(
        db, dispositivo_id, token_fingerprint,
    )
    if existente is not None:
        return existente, False

    try:
        with db.begin_nested():
            solicitud = RevocacionPushGlobal(
                dispositivo_id=dispositivo_id,
                usuario_id=usuario_id,
                token_fingerprint=token_fingerprint,
                estado="PENDIENTE",
                intentos=0,
                fecha_creacion=cuando,
                fecha_actualizacion=cuando,
            )
            db.add(solicitud)
            db.flush()
    except IntegrityError:
        existente = obtener_por_dispositivo_huella(
            db, dispositivo_id, token_fingerprint,
        )
        if existente is None:
            raise
        return existente, False
    return solicitud, True


def listar_pendientes(
    db: Session, *, limite: int = 100,
) -> list[RevocacionPushGlobal]:
    """Consulta de solo lectura para observabilidad."""
    return list(db.scalars(
        select(RevocacionPushGlobal)
        .where(RevocacionPushGlobal.estado.in_(("PENDIENTE", "PROCESANDO")))
        .order_by(
            RevocacionPushGlobal.fecha_creacion, RevocacionPushGlobal.id,
        )
        .limit(limite)
    ))


# --------------------------------------------------------------------------- #
# Cola del procesador de revocaciones
# --------------------------------------------------------------------------- #
def _condicion_reclamable(ahora: datetime):
    return or_(
        and_(
            RevocacionPushGlobal.estado == "PENDIENTE",
            or_(
                RevocacionPushGlobal.proximo_intento.is_(None),
                RevocacionPushGlobal.proximo_intento <= ahora,
            ),
        ),
        and_(
            RevocacionPushGlobal.estado == "PROCESANDO",
            RevocacionPushGlobal.bloqueado_hasta.is_not(None),
            RevocacionPushGlobal.bloqueado_hasta <= ahora,
        ),
    )


def construir_consulta_reclamo(
    ahora: datetime,
    limite: int,
    *,
    dialecto: str,
):
    """Sentencia de IDs reclamables; ``FOR UPDATE SKIP LOCKED`` en PostgreSQL."""
    stmt = (
        select(RevocacionPushGlobal.id)
        .where(_condicion_reclamable(ahora))
        .order_by(RevocacionPushGlobal.fecha_creacion, RevocacionPushGlobal.id)
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
) -> list[RevocacionPushGlobal]:
    """Reclama un lote acotado y devuelve solo las filas propias."""
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
        update(RevocacionPushGlobal)
        .where(RevocacionPushGlobal.id.in_(ids), condicion)
        .values(
            estado="PROCESANDO",
            lease_token=lease_token,
            bloqueado_hasta=lease_hasta,
            fecha_actualizacion=ahora,
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()

    return list(db.scalars(
        select(RevocacionPushGlobal)
        .where(
            RevocacionPushGlobal.id.in_(ids),
            RevocacionPushGlobal.estado == "PROCESANDO",
            RevocacionPushGlobal.lease_token == lease_token,
        )
        .order_by(RevocacionPushGlobal.id)
    ))


def actualizar_resultado(
    db: Session,
    revocacion_id: int,
    *,
    lease_token: str,
    valores: dict,
) -> bool:
    """Persiste el resultado solo si el lease sigue vigente (fencing)."""
    resultado = db.execute(
        update(RevocacionPushGlobal)
        .where(
            RevocacionPushGlobal.id == revocacion_id,
            RevocacionPushGlobal.estado == "PROCESANDO",
            RevocacionPushGlobal.lease_token == lease_token,
        )
        .values(**valores)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    return resultado.rowcount == 1
