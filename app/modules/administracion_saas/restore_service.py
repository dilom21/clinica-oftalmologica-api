"""Safe, auditable per-tenant restore over the proven 8A/8B backup engine.

The client only ever supplies a ``backup_id`` (and a strong confirmation). The
database name, storage key and file path are always derived from the Control
Plane. A restore is only destructive after a mandatory PRE_RESTORE snapshot and
a one-restore-per-tenant reservation; any failure after the final database was
modified triggers an automatic rollback from that snapshot.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core import config
from app.core.time import fecha_local_aplicacion
from app.core.tenancy.resolver import TenantResolver
from scripts.saas.provision_tenant import PostgresConnection

from .backup_runner import TenantBackupRunner
from .backup_service import create_pre_restore_backup
from .backup_storage_factory import build_backup_storage
from .models import BackupTenant, Empresa, RestoreTenant, Suscripcion, TenantDatabase
from .repository import SaasRepository
from .restore_runner import SchemaResetRefused, TenantRestoreRunner
from .service import sanitize_error

ALLOWED_RESTORE_TIPOS = ("MANUAL", "AUTOMATICO", "PRE_RESTORE")
ACTIVE_RESTORE_STATES = ("PENDIENTE", "EN_PROCESO", "ROLLBACK_EN_PROCESO")


class RestoreReservationError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RestoreExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RestoreDependencies:
    backend: TenantRestoreRunner
    backup_runner: TenantBackupRunner
    storage: object


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_restore_dependencies() -> RestoreDependencies:
    """Read backend-only settings at execution time; fail closed without private storage."""
    root = os.getenv("SAAS_BACKUP_PRIVATE_DIR")
    if not root:
        raise RuntimeError("Private backup storage is not configured")
    url = make_url(config.DATABASE_URL)
    if not url.host or not url.username or url.password is None:
        raise RuntimeError("Administrative PostgreSQL connection is unavailable")
    timeout = int(os.getenv("SAAS_BACKUP_TIMEOUT_SECONDS", "900"))
    connection = PostgresConnection(url.host, url.port or 5432, url.username, url.password,
                                    url.query.get("sslmode", "require"))
    pg_bin_dir = os.getenv("SAAS_BACKUP_PG_BIN_DIR")
    return RestoreDependencies(
        backend=TenantRestoreRunner(connection, pg_bin_dir=pg_bin_dir, timeout_seconds=timeout),
        backup_runner=TenantBackupRunner(connection, pg_bin_dir=pg_bin_dir, timeout_seconds=timeout),
        storage=build_backup_storage(),
    )


def _default_engine_disposer(tenant_database_id: int) -> None:
    from app.core.tenancy.dependencies import get_tenant_engine_registry

    get_tenant_engine_registry().dispose_tenant(tenant_database_id)


def get_engine_disposer():
    return _default_engine_disposer


# --------------------------------------------------------------------------- audit
def _audit(db: Session, user_id: int | None, empresa_id: int, restore_id: int | None,
           action: str, result: str, ip: str | None) -> None:
    SaasRepository(db).log(
        user_id, action, "restore_tenant", restore_id,
        f"empresa_id={empresa_id}; restore_id={restore_id}; result={result}", ip, result=result,
    )


def _audit_new(session_factory: sessionmaker, user_id, empresa_id, restore_id, action, result, ip):
    with session_factory() as db:
        _audit(db, user_id, empresa_id, restore_id, action, result, ip)
        db.commit()


# ------------------------------------------------------------------ read helpers
def _load_backup(db: Session, backup_id: int) -> BackupTenant:
    if backup_id <= 0:
        raise HTTPException(404, "Backup not found")
    backup = db.get(BackupTenant, backup_id)
    if backup is None:
        raise HTTPException(404, "Backup not found")
    return backup


def _load_mapping(db: Session, backup: BackupTenant) -> tuple[Empresa, TenantDatabase]:
    empresa = db.get(Empresa, backup.empresa_id)
    tenant = db.get(TenantDatabase, backup.tenant_database_id)
    if empresa is None or tenant is None or tenant.empresa_id != empresa.id:
        raise HTTPException(409, "Backup is not bound to a valid tenant")
    if empresa.estado != "ACTIVA" or tenant.estado != "ACTIVA":
        raise HTTPException(409, "Backup tenant is not active")
    today = fecha_local_aplicacion()
    subscriptions = list(db.scalars(select(Suscripcion).where(
        Suscripcion.empresa_id == empresa.id,
        Suscripcion.estado == "ACTIVA",
        Suscripcion.fecha_inicio <= today,
        Suscripcion.fecha_fin >= today,
    )).all())
    if len(subscriptions) != 1:
        raise HTTPException(409, "Backup tenant subscription is not active or is inconsistent")
    try:
        TenantResolver._validar_database_name(tenant.database_name)
    except Exception as exc:
        raise HTTPException(409, "Tenant database mapping is invalid") from exc
    if not tenant.database_name.startswith("tenant_"):
        raise HTTPException(409, "Tenant database mapping is invalid")
    return empresa, tenant


def _require_valid_backup(backup: BackupTenant, tenant: TenantDatabase) -> None:
    if backup.estado != "COMPLETADO":
        raise HTTPException(409, "Backup is not completed")
    if backup.tipo not in ALLOWED_RESTORE_TIPOS:
        raise HTTPException(409, "Backup type is not restorable")
    if not backup.storage_key or not backup.sha256 or not backup.size_bytes or backup.size_bytes <= 0:
        raise HTTPException(409, "Backup metadata is incomplete")
    if backup.version_schema != tenant.version_schema:
        raise HTTPException(409, "Backup schema version is incompatible")


# ------------------------------------------------------------------ archive I/O
def _consume(source, destination: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with destination.open("wb") as output:
        if hasattr(source, "read"):
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                digest.update(block)
                size += len(block)
        else:
            data = source if isinstance(source, (bytes, bytearray)) else bytes(source)
            output.write(data)
            digest.update(data)
            size = len(data)
    return size, digest.hexdigest()


def _read_storage_archive(storage, storage_key: str | None, size_bytes: int | None,
                          sha256: str | None, workdir: Path, name: str = "target.dump") -> Path:
    """Materialize and verify an archive from private storage before any DB work."""
    if not storage_key or not storage.exists(storage_key):
        raise RuntimeError("Backup object is missing")
    destination = workdir / name
    stream = storage.get(storage_key)
    try:
        size, digest = _consume(stream, destination)
    finally:
        closer = getattr(stream, "close", None)
        if callable(closer):
            closer()
    if size != size_bytes:
        raise RuntimeError("Backup size does not match metadata")
    if digest != sha256:
        raise RuntimeError("Backup checksum does not match metadata")
    return destination


# --------------------------------------------------------------------- service
def validate_restore(session_factory: sessionmaker, backup_id: int, user_id: int,
                     ip: str | None, *, backend, storage) -> dict:
    """Read-only validation; never creates a restore row and never touches a DB."""
    with session_factory() as db:
        backup = _load_backup(db, backup_id)
        empresa, tenant = _load_mapping(db, backup)
        _require_valid_backup(backup, tenant)
        payload = {
            "backup_id": backup.id, "empresa_id": empresa.id, "tenant_database_id": tenant.id,
            "estado": backup.estado, "tipo": backup.tipo, "size_bytes": backup.size_bytes,
            "sha256": backup.sha256, "version_schema": backup.version_schema, "valido": True,
        }
        storage_key, size_bytes, sha256 = backup.storage_key, backup.size_bytes, backup.sha256
        db.rollback()
    with tempfile.TemporaryDirectory(prefix="saas-restore-validate-") as temporary:
        try:
            archive = _read_storage_archive(storage, storage_key, size_bytes, sha256, Path(temporary))
            backend.list_archive(archive)
        except Exception as exc:
            _audit_new(session_factory, user_id, payload["empresa_id"], None,
                       "VALIDAR_RESTORE", "ERROR", ip)
            raise HTTPException(409, sanitize_error(str(exc)) or "Backup validation failed") from exc
    _audit_new(session_factory, user_id, payload["empresa_id"], None, "VALIDAR_RESTORE", "EXITO", ip)
    return payload


def _finalize_error(session_factory: sessionmaker, restore_id: int, etapa: str, user_id, empresa_id,
                    ip, message: str, *, revert_tenant_id: int | None = None,
                    revert_state: str | None = None) -> None:
    with session_factory() as db:
        row = db.get(RestoreTenant, restore_id)
        if row is not None:
            row.estado = "ERROR"
            row.etapa = etapa
            row.fecha_fin = _now()
            row.mensaje_error = sanitize_error(message)
            _audit(db, user_id, empresa_id, restore_id, "ERROR_RESTORE", "ERROR", ip)
        if revert_tenant_id is not None and revert_state is not None:
            tenant = db.get(TenantDatabase, revert_tenant_id)
            if tenant is not None:
                tenant.estado = revert_state
        db.commit()


def _allow_database_swap() -> bool:
    """Swap is never assumed: it needs an explicit opt-in AND a proven capability."""
    value = (os.getenv("SAAS_RESTORE_ALLOW_DATABASE_SWAP", "") or "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _swap_selected(backend, allow_swap: bool) -> bool:
    return allow_swap and backend.capabilities().supports_validation_database


def _perform_restore(backend, database_name: str, archive: Path, allow_swap: bool) -> None:
    """Prefer validation-database + cutover when the runtime proves the capability."""
    capabilities = backend.capabilities()
    if allow_swap and capabilities.supports_validation_database:
        temporary_name = "restore_" + uuid.uuid4().hex
        backend.create_database(temporary_name)
        try:
            backend.restore_into(temporary_name, archive)
            if not backend.verify_structure(temporary_name) or not backend.health_check(temporary_name):
                raise RuntimeError("Validation database verification failed")
            backend.terminate_sessions(database_name)
            backend.drop_database(database_name)
            backend.rename_database(temporary_name, database_name)
        except Exception:
            try:
                backend.drop_database(temporary_name)
            except Exception:
                pass
            raise
    else:
        backend.restore_exact(database_name, archive)


def _rollback(session_factory, restore_id, empresa_id, tenant_id, database_name, pre_restore_backup_id,
              user_id, ip, backend, storage, engine_disposer, workdir: Path, cause: str) -> RestoreTenant:
    _audit_new(session_factory, user_id, empresa_id, restore_id, "INICIAR_ROLLBACK_RESTORE", "EXITO", ip)
    with session_factory() as db:
        row = db.get(RestoreTenant, restore_id)
        row.estado = "ROLLBACK_EN_PROCESO"
        row.etapa = "ROLLBACK"
        row.rollback_estado = "EN_PROCESO"
        row.rollback_mensaje = None
        db.commit()

    failure: Exception | None = None
    try:
        with session_factory() as db:
            pre = db.get(BackupTenant, pre_restore_backup_id)
            if pre is None or pre.estado != "COMPLETADO" or not pre.storage_key:
                raise RuntimeError("Pre-restore backup is unavailable")
            pre_key, pre_size, pre_sha = pre.storage_key, pre.size_bytes, pre.sha256
            db.rollback()
        pre_archive = _read_storage_archive(storage, pre_key, pre_size, pre_sha, workdir,
                                            name="pre_restore.dump")
        engine_disposer(tenant_id)
        backend.terminate_sessions(database_name)
        backend.restore_exact(database_name, pre_archive)
        if not backend.verify_structure(database_name) or not backend.health_check(database_name):
            raise RuntimeError("Rollback verification failed")
    except Exception as exc:  # noqa: BLE001 - the failure must be recorded, never hidden
        failure = exc

    with session_factory() as db:
        row = db.get(RestoreTenant, restore_id)
        row.estado = "ERROR"
        row.fecha_fin = _now()
        row.mensaje_error = sanitize_error(cause)
        tenant = db.get(TenantDatabase, tenant_id)
        if failure is not None:
            # A failed rollback must NEVER leave the tenant ACTIVA.
            row.etapa = "ROLLBACK_ERROR"
            row.rollback_estado = "ERROR"
            row.rollback_mensaje = sanitize_error(str(failure)) or "Rollback failed"
            if tenant is not None:
                tenant.estado = "ERROR"
            _audit(db, user_id, empresa_id, restore_id, "ERROR_ROLLBACK_RESTORE", "ERROR", ip)
        else:
            row.etapa = "ROLLBACK_COMPLETADO"
            row.rollback_estado = "COMPLETADO"
            if tenant is not None:
                tenant.estado = "ACTIVA"
                tenant.ultima_verificacion = _now()
        _audit(db, user_id, empresa_id, restore_id, "ERROR_RESTORE", "ERROR", ip)
        if failure is None:
            _audit(db, user_id, empresa_id, restore_id, "COMPLETAR_ROLLBACK_RESTORE", "EXITO", ip)
        db.commit()
        return row


def create_restore(session_factory: sessionmaker, backup_id: int, confirmacion: str, user_id: int,
                   ip: str | None = None, *, backend, backup_runner, storage, engine_disposer) -> RestoreTenant:
    # 1. Authorize and read everything before any mutation.
    with session_factory() as db:
        backup = _load_backup(db, backup_id)
        empresa, tenant = _load_mapping(db, backup)
        _require_valid_backup(backup, tenant)
        if (confirmacion or "").strip() != empresa.codigo:
            raise HTTPException(409, "Confirmation does not match the backup company")
        empresa_id = empresa.id
        tenant_id = tenant.id
        database_name = tenant.database_name
        storage_key, size_bytes, sha256 = backup.storage_key, backup.size_bytes, backup.sha256
        db.rollback()

    # 2. Reserve exactly one restore per tenant before the destructive window.
    with session_factory() as db:
        restore = RestoreTenant(empresa_id=empresa_id, tenant_database_id=tenant_id, backup_id=backup_id,
                                estado="PENDIENTE", etapa="VALIDACION",
                                creado_por_saas_usuario_id=user_id)
        db.add(restore)
        try:
            db.commit()
            restore_id = restore.id
        except IntegrityError as exc:
            db.rollback()
            raise RestoreReservationError("busy") from exc

    workdir = Path(tempfile.mkdtemp(prefix="saas-restore-"))
    try:
        # 3. Validate the target archive; a failure never touches the DB.
        try:
            target_archive = _read_storage_archive(storage, storage_key, size_bytes, sha256, workdir)
            backend.list_archive(target_archive)
        except Exception as exc:
            _finalize_error(session_factory, restore_id, "VALIDACION", user_id, empresa_id, ip,
                            str(exc))
            reason = sanitize_error(str(exc)) or "Backup validation failed"
            raise RestoreExecutionError(f"Backup validation failed; tenant unchanged ({reason})") from None

        _audit_new(session_factory, user_id, empresa_id, restore_id, "VALIDAR_RESTORE", "EXITO", ip)

        # 4. Mandatory PRE_RESTORE using the exact 8A/8B engine.
        try:
            pre = create_pre_restore_backup(session_factory, empresa_id, user_id, ip,
                                            runner=backup_runner, storage=storage)
        except Exception as exc:
            _finalize_error(session_factory, restore_id, "PRE_RESTORE", user_id, empresa_id, ip,
                            str(exc))
            raise RestoreExecutionError("Pre-restore backup failed; tenant unchanged") from None
        with session_factory() as db:
            db.get(RestoreTenant, restore_id).pre_restore_backup_id = pre.id
            db.commit()
        pre_restore_backup_id = pre.id

        # 4b. Read-only preflight for the in-place strategy: the public schema
        # must be resettable without destroying shared objects. The tenant is
        # still ACTIVA and no session has been terminated yet.
        allow_swap = _allow_database_swap()
        if not _swap_selected(backend, allow_swap):
            try:
                state = backend.inspect_public_schema(database_name)
                backend.assert_reset_safe(state)
            except SchemaResetRefused as exc:
                _finalize_error(session_factory, restore_id, "PREFLIGHT", user_id, empresa_id, ip, str(exc))
                raise RestoreExecutionError("Restore preflight refused; tenant unchanged") from None
            except Exception as exc:
                _finalize_error(session_factory, restore_id, "PREFLIGHT", user_id, empresa_id, ip, str(exc))
                raise RestoreExecutionError("Restore preflight failed; tenant unchanged") from None

        # 5. Take the tenant out of service only now that the snapshot exists.
        with session_factory() as db:
            tenant_row = db.get(TenantDatabase, tenant_id)
            restore_row = db.get(RestoreTenant, restore_id)
            if tenant_row is None or restore_row is None or tenant_row.estado != "ACTIVA":
                if restore_row is not None:
                    restore_row.estado = "ERROR"
                    restore_row.etapa = "ABORTADO"
                    restore_row.fecha_fin = _now()
                    _audit(db, user_id, empresa_id, restore_id, "ERROR_RESTORE", "ERROR", ip)
                db.commit()
                raise RestoreExecutionError("Tenant is not available for restore")
            tenant_row.estado = "RESTAURANDO"
            restore_row.estado = "EN_PROCESO"
            restore_row.etapa = "RESTORE"
            restore_row.fecha_inicio = _now()
            _audit(db, user_id, empresa_id, restore_id, "INICIAR_RESTORE", "EXITO", ip)
            db.commit()

        # 6. Close tenant connections before cutover. A failure here did not mutate data.
        try:
            engine_disposer(tenant_id)
            backend.terminate_sessions(database_name)
        except Exception as exc:
            _finalize_error(session_factory, restore_id, "PREPARACION", user_id, empresa_id, ip,
                            str(exc), revert_tenant_id=tenant_id, revert_state="ACTIVA")
            raise RestoreExecutionError("Restore preparation failed; tenant restored to ACTIVA") from None

        # 7. Restore + verify. Any failure from here needs an automatic rollback.
        try:
            _perform_restore(backend, database_name, target_archive, allow_swap)
            if not backend.verify_structure(database_name) or not backend.health_check(database_name):
                raise RuntimeError("Final tenant verification failed")
        except Exception as exc:
            row = _rollback(session_factory, restore_id, empresa_id, tenant_id, database_name,
                            pre_restore_backup_id, user_id, ip, backend, storage,
                            engine_disposer, workdir, str(exc))
            if row.rollback_estado == "ERROR":
                raise RestoreExecutionError(
                    "Restore failed and rollback failed; manual intervention required") from None
            raise RestoreExecutionError(
                "Restore failed; tenant rolled back to the pre-restore state") from None

        # 8. Success: reactivate only after the final verification passed.
        with session_factory() as db:
            tenant_row = db.get(TenantDatabase, tenant_id)
            tenant_row.estado = "ACTIVA"
            tenant_row.ultima_verificacion = _now()
            restore_row = db.get(RestoreTenant, restore_id)
            restore_row.estado = "COMPLETADO"
            restore_row.etapa = "COMPLETADO"
            restore_row.fecha_fin = _now()
            restore_row.mensaje_error = None
            _audit(db, user_id, empresa_id, restore_id, "COMPLETAR_RESTORE", "EXITO", ip)
            db.commit()
            return restore_row
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def list_restores(db: Session, empresa_id: int | None, estado: str | None) -> list[RestoreTenant]:
    query = select(RestoreTenant)
    if empresa_id is not None:
        query = query.where(RestoreTenant.empresa_id == empresa_id)
    if estado is not None:
        query = query.where(RestoreTenant.estado == estado)
    return list(db.scalars(query.order_by(RestoreTenant.id.desc()).limit(200)).all())
