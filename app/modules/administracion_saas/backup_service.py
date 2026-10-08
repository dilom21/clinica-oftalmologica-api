"""Control-plane-only, durable manual and automatic backup lifecycle."""

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import config
from app.core.tenancy.resolver import TenantResolver
from scripts.saas.provision_tenant import PostgresConnection

from .backup_runner import TenantBackupRunner
from .backup_storage import BackupStorage
from .backup_storage_factory import build_backup_storage
from .models import BackupTenant, Empresa, RestoreTenant, TenantDatabase
from .repository import SaasRepository

# Backup types that a safe restore must block while it owns the tenant.
_BLOCKED_DURING_RESTORE = ("MANUAL", "AUTOMATICO")
# Restore reservations/locks that make the tenant busy for normal backups.
_ACTIVE_RESTORE_STATES = ("PENDIENTE", "EN_PROCESO", "ROLLBACK_EN_PROCESO")


class BackupReservationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class BackupExecutionError(RuntimeError):
    pass


def build_backup_dependencies() -> tuple[TenantBackupRunner, BackupStorage]:
    """Read backend-only settings at execution time; fail closed without private storage."""
    root = os.getenv("SAAS_BACKUP_PRIVATE_DIR")
    if not root:
        raise RuntimeError("Private backup storage is not configured")
    url = make_url(config.DATABASE_URL)
    if not url.host or not url.username or url.password is None:
        raise RuntimeError("Administrative PostgreSQL connection is unavailable")
    timeout = int(os.getenv("SAAS_BACKUP_TIMEOUT_SECONDS", "900"))
    runner = TenantBackupRunner(
        PostgresConnection(url.host, url.port or 5432, url.username, url.password,
                           url.query.get("sslmode", "require")),
        pg_bin_dir=os.getenv("SAAS_BACKUP_PG_BIN_DIR"), timeout_seconds=timeout,
    )
    storage = build_backup_storage()
    return runner, storage


def _mapping(db: Session, empresa_id: int):
    empresa = db.get(Empresa, empresa_id)
    if empresa is None:
        raise HTTPException(404, "Company not found")
    if empresa.estado != "ACTIVA":
        raise HTTPException(409, "Company is not active")
    tenant = db.scalars(select(TenantDatabase).where(TenantDatabase.empresa_id == empresa_id)).first()
    if tenant is None or tenant.estado != "ACTIVA":
        raise HTTPException(409, "Tenant database is not active")
    try:
        TenantResolver._validar_database_name(tenant.database_name)
    except Exception as exc:
        raise HTTPException(409, "Tenant database mapping is invalid") from exc
    # Refuse control-plane and maintenance databases even if their names pass the
    # generic identifier validator. Provisioned physical tenants use tenant_*.
    if not tenant.database_name.startswith("tenant_"):
        raise HTTPException(409, "Tenant database mapping is invalid")
    try:
        if tenant.database_name == make_url(config.DATABASE_URL).database:
            raise HTTPException(409, "Tenant database mapping is invalid")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Administrative database configuration is unavailable") from exc
    return empresa, tenant


def _audit(db: Session, user_id: int | None, empresa_id: int, backup_id: int,
           action: str, result: str, ip: str | None):
    SaasRepository(db).log(user_id, action, "backup_tenant", backup_id,
                           f"empresa_id={empresa_id}; backup_id={backup_id}; result={result}",
                           ip, result=result)


def _digest(path: Path) -> tuple[int, str]:
    if not path.is_file():
        raise RuntimeError("Backup archive is missing")
    sha = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            sha.update(block)
    if size == 0:
        raise RuntimeError("Backup archive is empty")
    return size, sha.hexdigest()


def _run_backup(session_factory: sessionmaker, empresa_id: int, *, tipo: str,
                user_id: int | None, ip: str | None = None, runner=None, storage=None,
                ventana: str | None = None) -> BackupTenant:
    """Commit reservation before dump so independent workers see the unique index."""
    label = {"AUTOMATICO": "Automatic", "PRE_RESTORE": "Pre-restore"}.get(tipo, "Manual")
    action = {"AUTOMATICO": "CREAR_BACKUP_AUTOMATICO",
              "PRE_RESTORE": "CREAR_BACKUP_PRE_RESTORE"}.get(tipo, "CREAR_BACKUP_MANUAL")
    with session_factory() as db:
        empresa, tenant = _mapping(db, empresa_id)
        database_name = tenant.database_name
        tenant_id = tenant.id
        version = tenant.version_schema
        db.rollback()

    # Resolve configuration before reserving to avoid a stranded operation.
    if runner is None or storage is None:
        configured_runner, configured_storage = build_backup_dependencies()
        runner = runner or configured_runner
        storage = storage or configured_storage

    key = f"{uuid.uuid4().hex}.dump"
    with session_factory() as db:
        _, current = _mapping(db, empresa_id)  # Recheck state immediately before reservation.
        if current.id != tenant_id or current.database_name != database_name:
            raise HTTPException(409, "Tenant mapping changed during backup preparation")
        # A safe restore owns the tenant: ordinary backups must not race it.
        # PRE_RESTORE belongs to the restore itself and is intentionally allowed.
        if tipo in _BLOCKED_DURING_RESTORE:
            active_restore = db.scalar(select(RestoreTenant.id).where(
                RestoreTenant.tenant_database_id == tenant_id,
                RestoreTenant.estado.in_(_ACTIVE_RESTORE_STATES)))
            if active_restore is not None:
                raise BackupReservationError("restore_in_progress")
        backup = BackupTenant(empresa_id=empresa_id, tenant_database_id=tenant_id,
                              creado_por_saas_usuario_id=user_id, version_schema=version,
                              storage_key=key, tipo=tipo, ventana=ventana)
        db.add(backup)
        try:
            db.commit()
            backup_id = backup.id
        except IntegrityError as exc:
            db.rollback()
            busy = db.scalar(select(BackupTenant.id).where(
                BackupTenant.tenant_database_id == tenant_id,
                BackupTenant.estado == "EN_PROCESO"))
            if busy is not None:
                raise BackupReservationError("busy") from exc
            duplicate = None
            if ventana is not None:
                duplicate = db.scalar(select(BackupTenant.id).where(
                    BackupTenant.empresa_id == empresa_id,
                    BackupTenant.tipo == "AUTOMATICO",
                    BackupTenant.ventana == ventana,
                    BackupTenant.estado != "ERROR"))
            if duplicate is not None:
                raise BackupReservationError("already_done") from exc
            raise BackupReservationError("failed") from exc

    filename = f"tenant_{empresa_id}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{backup_id}.dump"
    try:
        with storage.staging() as temporary:
            path = temporary / "tenant.dump"
            runner.dump(database_name, path)
            storage.verify_private_archive(path)
            size, digest = _digest(path)
            runner.verify(path)
            storage.put(key, path)
            if not storage.exists(key):
                raise RuntimeError("Backup storage did not retain the archive")
        with session_factory() as db:
            record = db.get(BackupTenant, backup_id)
            if record is None or record.estado != "EN_PROCESO":
                raise RuntimeError("Backup reservation was lost")
            record.storage_key = key
            record.nombre_archivo = filename
            record.formato = "CUSTOM"
            record.size_bytes = size
            record.sha256 = digest
            record.estado = "COMPLETADO"
            record.fecha_fin = datetime.now(timezone.utc)
            _audit(db, user_id, empresa_id, backup_id, action, "EXITO", ip)
            db.commit()
            return record
    except Exception:
        cleanup_needed = False
        try:
            # A failed put can leave a partial object; verify absence after delete.
            storage.delete(key)
            cleanup_needed = storage.exists(key)
        except Exception:
            cleanup_needed = True
        try:
            with session_factory() as db:
                record = db.get(BackupTenant, backup_id)
                if record is not None and record.estado == "EN_PROCESO":
                    record.estado = "ERROR"
                    record.fecha_fin = datetime.now(timezone.utc)
                    record.storage_key = key if cleanup_needed else None
                    record.mensaje_error = (
                        f"{label} backup failed; private storage cleanup requires operator review"
                        if cleanup_needed else f"{label} backup failed"
                    )
                    _audit(db, user_id, empresa_id, backup_id, action, "ERROR", ip)
                    db.commit()
        except Exception:
            # A failed control-plane connection cannot safely be described as ERROR.
            raise BackupExecutionError(
                f"{label} backup failed; private storage cleanup requires operator review"
            ) from None
        raise BackupExecutionError(f"{label} backup failed") from None


def create_manual_backup(session_factory: sessionmaker, empresa_id: int, user_id: int,
                         ip: str | None = None, *, runner=None, storage=None) -> BackupTenant:
    try:
        return _run_backup(session_factory, empresa_id, tipo="MANUAL", user_id=user_id,
                           ip=ip, runner=runner, storage=storage)
    except BackupReservationError as exc:
        if exc.reason == "busy":
            raise HTTPException(409, "A backup is already in progress for this tenant") from exc
        if exc.reason == "restore_in_progress":
            raise HTTPException(409, "A restore is in progress for this tenant") from exc
        raise HTTPException(503, "Backup reservation failed") from exc
    except BackupExecutionError as exc:
        raise HTTPException(503, str(exc)) from exc


def create_pre_restore_backup(session_factory: sessionmaker, empresa_id: int, user_id: int,
                              ip: str | None = None, *, runner, storage) -> BackupTenant:
    """Mandatory PRE_RESTORE archive built by the exact 8A/8B engine.

    It is never blocked by the restore lock because it IS part of the restore.
    """
    return _run_backup(session_factory, empresa_id, tipo="PRE_RESTORE", user_id=user_id,
                       ip=ip, runner=runner, storage=storage)


def create_automatic_backup(session_factory: sessionmaker, empresa_id: int, ventana: str,
                            *, runner, storage) -> BackupTenant:
    return _run_backup(session_factory, empresa_id, tipo="AUTOMATICO", user_id=None,
                       ip=None, runner=runner, storage=storage, ventana=ventana)


def list_backups(db: Session, empresa_id: int | None, estado: str | None, tipo: str | None):
    query = select(BackupTenant)
    if empresa_id is not None:
        query = query.where(BackupTenant.empresa_id == empresa_id)
    if estado is not None:
        query = query.where(BackupTenant.estado == estado)
    if tipo is not None:
        query = query.where(BackupTenant.tipo == tipo)
    return list(db.scalars(query.order_by(BackupTenant.id.desc()).limit(200)).all())
