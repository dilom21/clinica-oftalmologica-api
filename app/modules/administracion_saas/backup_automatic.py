"""Automatic per-tenant backup orchestration over the proven 8A engine.

Eligibility, due detection, per-window idempotency, fail-and-continue,
automatic-only retention, and audit live here. No pg_dump logic is duplicated:
every execution goes through ``backup_service.create_automatic_backup``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.time import fecha_local_aplicacion

from .backup_policy import as_utc, next_occurrence, window_key
from .backup_service import BackupReservationError, _audit, create_automatic_backup
from .models import BackupPolicy, BackupTenant, Empresa, Suscripcion, TenantDatabase


def eligible_policies(db):
    """Return ``(BackupPolicy, Empresa, TenantDatabase)`` rows eligible for automation."""
    today = fecha_local_aplicacion()
    active_subscription = (
        select(Suscripcion.id)
        .where(
            Suscripcion.empresa_id == Empresa.id,
            Suscripcion.estado == "ACTIVA",
            Suscripcion.fecha_inicio <= today,
            Suscripcion.fecha_fin >= today,
        )
        .exists()
    )
    query = (
        select(BackupPolicy, Empresa, TenantDatabase)
        .join(Empresa, Empresa.id == BackupPolicy.empresa_id)
        .join(TenantDatabase, TenantDatabase.empresa_id == Empresa.id)
        .where(BackupPolicy.habilitado.is_(True))
        .where(Empresa.estado == "ACTIVA")
        .where(TenantDatabase.estado == "ACTIVA")
        .where(active_subscription)
        .order_by(BackupPolicy.empresa_id)
    )
    return [tuple(row) for row in db.execute(query).all()]


def window_already_recorded(db, empresa_id: int, ventana: str) -> bool:
    """True when a non-error AUTOMATICO row already owns this company/window."""
    record = db.scalar(select(BackupTenant.id).where(
        BackupTenant.empresa_id == empresa_id,
        BackupTenant.tipo == "AUTOMATICO",
        BackupTenant.ventana == ventana,
        BackupTenant.estado != "ERROR",
    ))
    return record is not None


def plan_automatic_backups(session_factory, now=None) -> dict:
    """Read-only plan. Never writes and never touches pg_dump or storage."""
    moment = as_utc(now) or datetime.now(timezone.utc)
    with session_factory() as db:
        policies = eligible_policies(db)
        detalles = []
        debidas = ya_ejecutadas = no_debidas = 0
        for policy, empresa, _tenant in policies:
            due = as_utc(policy.proximo_backup)
            if due is None or due > moment:
                no_debidas += 1
                detalles.append({"empresa_id": empresa.id, "estado": "NO_DEBIDO"})
                continue
            ventana = window_key(due)
            if window_already_recorded(db, empresa.id, ventana):
                ya_ejecutadas += 1
                detalles.append({"empresa_id": empresa.id, "estado": "YA_EJECUTADO",
                                 "ventana": ventana})
            else:
                debidas += 1
                detalles.append({"empresa_id": empresa.id, "estado": "DEBIDO",
                                 "ventana": ventana})
        return {
            "politicas": len(policies),
            "debidas": debidas,
            "ya_ejecutadas": ya_ejecutadas,
            "no_debidas": no_debidas,
            "detalles": detalles,
        }


def run_automatic_backups(session_factory, *, runner, storage, now=None,
                          purge_retention: bool = False) -> dict:
    """Plan once, then run due backups sequentially; one failure never stops the rest."""
    moment = as_utc(now) or datetime.now(timezone.utc)
    plan = plan_automatic_backups(session_factory, now=moment)
    due = [detalle for detalle in plan["detalles"] if detalle["estado"] == "DEBIDO"]
    completadas = omitidas = fallidas = purgados = 0
    detalles = []
    for item in due:
        empresa_id = item["empresa_id"]
        ventana = item["ventana"]
        try:
            create_automatic_backup(session_factory, empresa_id, ventana,
                                    runner=runner, storage=storage)
        except BackupReservationError as exc:
            if exc.reason in {"busy", "already_done", "restore_in_progress"}:
                omitidas += 1
                detalles.append({"empresa_id": empresa_id, "estado": "OMITIDO",
                                 "ventana": ventana})
            else:
                fallidas += 1
                detalles.append({"empresa_id": empresa_id, "estado": "FALLIDO",
                                 "ventana": ventana})
            continue
        except Exception:
            fallidas += 1
            detalles.append({"empresa_id": empresa_id, "estado": "FALLIDO", "ventana": ventana})
            continue
        completadas += 1
        detalles.append({"empresa_id": empresa_id, "estado": "COMPLETADO", "ventana": ventana})
        _advance_policy(session_factory, empresa_id, moment)
        if purge_retention:
            keep = _retention_count(session_factory, empresa_id)
            if keep is not None:
                try:
                    purgados += apply_retention(session_factory, empresa_id, keep, storage)["purgados"]
                except Exception:
                    # Retention is best-effort and never stops the remaining tenants.
                    pass
    return {
        "politicas": plan["politicas"],
        "debidas": plan["debidas"],
        "completadas": completadas,
        "omitidas": omitidas,
        "fallidas": fallidas,
        "purgados": purgados,
        "detalles": detalles,
    }


def _advance_policy(session_factory, empresa_id: int, now: datetime) -> None:
    """Advance the policy after a completed backup; never advance on failure."""
    try:
        with session_factory() as db:
            policy = db.scalars(
                select(BackupPolicy).where(BackupPolicy.empresa_id == empresa_id)
            ).first()
            if policy is None:
                return
            policy.ultimo_backup_automatico = now
            policy.proximo_backup = next_occurrence(policy, now)
            policy.fecha_actualizacion = now
            db.commit()
    except Exception:
        # The completed backup plus per-window idempotency remain authoritative.
        pass


def _retention_count(session_factory, empresa_id: int) -> int | None:
    with session_factory() as db:
        return db.scalar(
            select(BackupPolicy.retencion_cantidad).where(BackupPolicy.empresa_id == empresa_id)
        )


def select_retention_candidates(db, empresa_id: int, keep: int) -> list[BackupTenant]:
    """Automatic COMPLETADO rows beyond ``keep``; MANUAL/PRE_RESTORE are never returned."""
    if keep < 0:
        raise ValueError("Retention count must not be negative")
    rows = list(db.scalars(
        select(BackupTenant)
        .where(
            BackupTenant.empresa_id == empresa_id,
            BackupTenant.tipo == "AUTOMATICO",
            BackupTenant.estado == "COMPLETADO",
            BackupTenant.storage_key.is_not(None),
        )
        .order_by(BackupTenant.id.desc())
    ).all())
    return rows[keep:]


def apply_retention(session_factory, empresa_id: int, keep: int, storage) -> dict:
    """Delete selected automatic archives and rows; never touches MANUAL/PRE_RESTORE."""
    with session_factory() as db:
        candidates = select_retention_candidates(db, empresa_id, keep)
        pairs = [(row.id, row.storage_key) for row in candidates]
    purgados = 0
    for backup_id, key in pairs:
        try:
            storage.delete(key)
            if storage.exists(key):
                continue
        except Exception:
            continue
        with session_factory() as db:
            record = db.get(BackupTenant, backup_id)
            if record is None:
                continue
            if (record.tipo != "AUTOMATICO" or record.estado != "COMPLETADO"
                    or record.storage_key != key):
                continue
            db.delete(record)
            _audit(db, None, empresa_id, backup_id, "PURGAR_BACKUP_AUTOMATICO", "EXITO", None)
            db.commit()
        purgados += 1
    return {"seleccionados": len(pairs), "purgados": purgados}
