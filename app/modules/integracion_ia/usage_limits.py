"""Cuotas IA compartidas mediante la bitácora persistente del tenant."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


ACCION_CONSUMO_IA = "CONSUMO_IA_RESERVADO"
LIMITE_USUARIO_MINUTO = 10
LIMITE_TENANT_MINUTO = 60
_ADVISORY_LOCK_KEY = 1_904_202_602


def reservar_consumo(db: Session, usuario_id: int) -> None:
    """Reserva una solicitud antes de llamar al proveedor, incluso si este falla.

    La sesión ya está enrutada a la base del tenant. PostgreSQL usa un advisory
    lock transaccional para que varios workers no superen la cuota por carrera.
    """
    ahora = datetime.now(timezone.utc)
    desde = ahora - timedelta(minutes=1)
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(select(func.pg_advisory_xact_lock(_ADVISORY_LOCK_KEY)))

        base = (
            select(func.count(Bitacora.id))
            .where(Bitacora.accion == ACCION_CONSUMO_IA)
            .where(Bitacora.fecha_hora >= desde)
        )
        total_tenant = int(db.scalar(base) or 0)
        total_usuario = int(
            db.scalar(base.where(Bitacora.usuario_id == usuario_id)) or 0
        )
        if total_usuario >= LIMITE_USUARIO_MINUTO or total_tenant >= LIMITE_TENANT_MINUTO:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Límite temporal de solicitudes de IA alcanzado",
                headers={"Retry-After": "60"},
            )

        registrar_bitacora(
            db,
            usuario_id,
            ACCION_CONSUMO_IA,
            entidad_afectada="integracion_ia",
            descripcion="Reserva de cuota de solicitud IA",
        )
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
