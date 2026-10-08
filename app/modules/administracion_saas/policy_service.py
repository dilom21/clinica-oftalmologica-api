"""SaaS console administration of ``saas_control.backup_policy``.

Read/upsert only. This module never schedules, never dumps and never touches the
backup/restore engines: saving a policy just persists configuration and, when the
schedule changes, recomputes ``proximo_backup`` with the existing policy engine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .backup_policy import FREQUENCIES, next_occurrence
from .models import BackupPolicy, Empresa
from .repository import SaasRepository


def _valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except Exception:
        return False
    return True


def _view(empresa: Empresa, policy: BackupPolicy | None) -> dict:
    """Safe projection: no ``database_name``, storage key or secrets."""
    return {
        "empresa_id": empresa.id,
        "empresa_codigo": empresa.codigo,
        "empresa_nombre": empresa.nombre_comercial or empresa.razon_social,
        "estado_empresa": empresa.estado,
        "configurada": policy is not None,
        "habilitado": None if policy is None else policy.habilitado,
        "frecuencia": None if policy is None else policy.frecuencia,
        "hora_local": None if policy is None else policy.hora_local,
        "timezone": None if policy is None else policy.timezone,
        "retencion_cantidad": None if policy is None else policy.retencion_cantidad,
        "ultimo_backup_automatico": None if policy is None else policy.ultimo_backup_automatico,
        "proximo_backup": None if policy is None else policy.proximo_backup,
        "fecha_actualizacion": None if policy is None else policy.fecha_actualizacion,
    }


def list_backup_policies(db: Session) -> list[dict]:
    """One row per company, with its policy (or nulls when not configured)."""
    rows = db.execute(
        select(Empresa, BackupPolicy)
        .outerjoin(BackupPolicy, BackupPolicy.empresa_id == Empresa.id)
        .order_by(Empresa.id)
    ).all()
    return [_view(empresa, policy) for empresa, policy in rows]


def _apply(policy: BackupPolicy, data, frecuencia: str, timezone_name: str,
           now: datetime, recompute: bool) -> None:
    policy.habilitado = data.habilitado
    policy.frecuencia = frecuencia
    policy.hora_local = data.hora_local
    policy.timezone = timezone_name
    policy.retencion_cantidad = data.retencion_cantidad
    policy.fecha_actualizacion = now
    # Never touch ultimo_backup_automatico. A pending window is preserved unless
    # the schedule itself changed (or the policy is being created/re-enabled).
    if recompute or policy.proximo_backup is None:
        policy.proximo_backup = next_occurrence(policy, now)


def _schedule_changed(policy: BackupPolicy, data, frecuencia: str, timezone_name: str) -> bool:
    return (
        policy.frecuencia != frecuencia
        or policy.hora_local != data.hora_local
        or policy.timezone != timezone_name
        or (data.habilitado and policy.habilitado is not True)
    )


def _description(empresa_id: int, data, frecuencia: str, timezone_name: str) -> str:
    return (
        f"empresa_id={empresa_id}; habilitado={data.habilitado}; "
        f"frecuencia={frecuencia}; timezone={timezone_name}; "
        f"retencion_cantidad={data.retencion_cantidad}"
    )


def upsert_backup_policy(db: Session, empresa_id: int, data, user_id: int,
                         ip: str | None = None) -> dict:
    """Idempotent create/update. Saving never triggers a dump or the scheduler."""
    empresa = db.get(Empresa, empresa_id)
    if empresa is None:
        raise HTTPException(404, "Empresa no encontrada")

    frecuencia = (data.frecuencia or "").strip().upper()
    if frecuencia not in FREQUENCIES:
        raise HTTPException(422, "Frecuencia de política inválida")
    timezone_name = (data.timezone or "").strip()
    if not timezone_name or not _valid_timezone(timezone_name):
        raise HTTPException(422, "Zona horaria de política inválida")
    if not 1 <= data.retencion_cantidad <= 365:
        raise HTTPException(422, "Retención de política inválida")

    now = datetime.now(timezone.utc)
    policy = db.scalars(
        select(BackupPolicy).where(BackupPolicy.empresa_id == empresa_id)
    ).first()
    created = policy is None
    if created:
        policy = BackupPolicy(empresa_id=empresa_id, fecha_creacion=now)
        # SQLite (tests) cannot autoincrement a BIGINT primary key; PostgreSQL owns it.
        if db.get_bind().dialect.name == "sqlite":
            policy.id = (db.scalar(select(func.max(BackupPolicy.id))) or 0) + 1
        db.add(policy)
        _apply(policy, data, frecuencia, timezone_name, now, recompute=True)
    else:
        recompute = _schedule_changed(policy, data, frecuencia, timezone_name)
        _apply(policy, data, frecuencia, timezone_name, now, recompute=recompute)
    try:
        db.flush()
        SaasRepository(db).log(
            user_id,
            "CREAR_POLICY_BACKUP" if created else "ACTUALIZAR_POLICY_BACKUP",
            "backup_policy",
            policy.id,
            _description(empresa_id, data, frecuencia, timezone_name),
            ip,
        )
        db.commit()
    except IntegrityError:
        # A concurrent create won the unique(empresa_id) race: converge to update.
        db.rollback()
        policy = db.scalars(
            select(BackupPolicy).where(BackupPolicy.empresa_id == empresa_id)
        ).first()
        if policy is None:
            raise HTTPException(503, "No se pudo guardar la política") from None
        recompute = _schedule_changed(policy, data, frecuencia, timezone_name)
        _apply(policy, data, frecuencia, timezone_name, now, recompute=recompute)
        try:
            SaasRepository(db).log(
                user_id, "ACTUALIZAR_POLICY_BACKUP", "backup_policy", policy.id,
                _description(empresa_id, data, frecuencia, timezone_name), ip,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(503, "No se pudo guardar la política") from None
    except Exception:
        db.rollback()
        raise HTTPException(503, "No se pudo guardar la política") from None

    db.refresh(policy)
    return _view(empresa, policy)
