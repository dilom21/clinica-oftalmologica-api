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


class SwapExecutionError(RuntimeError):
    """A database-swap failure whose physical state must not be overwritten."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        original_available: bool,
        temporary_name: str | None = None,
        backup_name: str | None = None,
    ):
        super().__init__(message)
        self.stage = stage
        self.original_available = original_available
        # Physical names are retained for the operator only when the failed
        # cutover may have left one or both databases on disk.
        self.temporary_name = temporary_name
        self.backup_name = backup_name


@dataclass(frozen=True)
class RestoreDependencies:
    backend: TenantRestoreRunner
    backup_runner: TenantBackupRunner
    storage: object


@dataclass(frozen=True)
class DatabaseSwap:
    """Names retained until the canonical destination and metadata are valid."""

    temporary_name: str
    backup_name: str


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
           action: str, result: str, ip: str | None, *, detalle: str | None = None) -> None:
    descripcion = f"empresa_id={empresa_id}; restore_id={restore_id}; result={result}"
    if detalle:
        descripcion = f"{descripcion}; {detalle}"
    SaasRepository(db).log(
        user_id, action, "restore_tenant", restore_id,
        descripcion, ip, result=result,
    )


def _audit_new(session_factory: sessionmaker, user_id, empresa_id, restore_id, action, result, ip,
               *, detalle: str | None = None):
    with session_factory() as db:
        _audit(db, user_id, empresa_id, restore_id, action, result, ip, detalle=detalle)
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
    if not backup.version_schema or not backup.version_schema.strip():
        raise HTTPException(409, "Backup schema version is unavailable")


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
        current_version = tenant.version_schema
        storage_key, size_bytes, sha256 = backup.storage_key, backup.size_bytes, backup.sha256
        db.rollback()
    with tempfile.TemporaryDirectory(prefix="saas-restore-validate-") as temporary:
        try:
            archive = _read_storage_archive(storage, storage_key, size_bytes, sha256, Path(temporary))
            backend.list_archive(archive)
            archive_has_ledger = backend.archive_contains_schema(
                archive,
                "app_migrations",
            )
            if (
                not backend.is_known_schema_version(current_version)
                or not backend.supports_archive_version(
                    payload["version_schema"],
                    has_ledger=archive_has_ledger,
                )
                or (
                    payload["version_schema"] != current_version
                    and not archive_has_ledger
                )
            ):
                raise RuntimeError("Backup schema version is unsupported")
        except Exception as exc:
            _audit_new(session_factory, user_id, payload["empresa_id"], None,
                       "VALIDAR_RESTORE", "ERROR", ip)
            raise HTTPException(409, sanitize_error(str(exc)) or "Backup validation failed") from exc
    _audit_new(session_factory, user_id, payload["empresa_id"], None, "VALIDAR_RESTORE", "EXITO", ip)
    return payload


def _retention_note(
    *,
    restore_id: int,
    tenant_database_id: int,
    database_name: str,
    etapa: str,
    motivo: str,
    temporary_name: str | None = None,
    backup_name: str | None = None,
    temporary_label: str = "base_temporal",
) -> str:
    """Auditable, secret-free correlation for retained restore databases.

    It intentionally records physical database names, which are Control Plane
    metadata. This text is only stored in ``restore_tenant.mensaje_error`` and
    ``saas_bitacora.descripcion`` (SaaS admin surfaces); it never includes
    credentials, connection strings or archive contents.
    """
    parts = [
        f"restore_id={restore_id}",
        f"tenant_database_id={tenant_database_id}",
        f"base_canonica={database_name}",
    ]
    if temporary_name:
        parts.append(f"{temporary_label}={temporary_name}")
    if backup_name:
        parts.append(f"base_respaldo={backup_name}")
    parts.append(f"etapa={etapa}")
    parts.append(f"motivo={motivo}")
    return "; ".join(parts)


def _finalize_error(session_factory: sessionmaker, restore_id: int, etapa: str, user_id, empresa_id,
                     ip, message: str, *, revert_tenant_id: int | None = None,
                     revert_state: str | None = None,
                     revert_version: str | None = None,
                     retencion: str | None = None) -> None:
    if retencion:
        # Names come first so they survive the 500-character sanitization cap.
        message = f"{retencion}; {message}"
    with session_factory() as db:
        row = db.get(RestoreTenant, restore_id)
        if row is not None:
            row.estado = "ERROR"
            row.etapa = etapa
            row.fecha_fin = _now()
            row.mensaje_error = sanitize_error(message)
            _audit(db, user_id, empresa_id, restore_id, "ERROR_RESTORE", "ERROR", ip,
                   detalle=retencion)
        if revert_tenant_id is not None and revert_state is not None:
            tenant = db.get(TenantDatabase, revert_tenant_id)
            if tenant is not None:
                tenant.estado = revert_state
                if revert_version is not None:
                    tenant.version_schema = revert_version
        db.commit()


def _allow_database_swap() -> bool:
    """Swap is never assumed: it needs an explicit opt-in AND a proven capability."""
    value = (os.getenv("SAAS_RESTORE_ALLOW_DATABASE_SWAP", "") or "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _probe_swap_support(backend, allow_swap: bool) -> bool:
    """Probe the admin capability before any destructive window opens.

    A failed probe is a preparation failure with ``original_available=True``:
    the canonical database has not been touched, so it must never trigger an
    in-place restore/rollback over a healthy database.
    """
    if not allow_swap:
        return False
    try:
        return bool(backend.capabilities().supports_validation_database)
    except Exception as exc:
        raise SwapExecutionError(
            str(exc), stage="PREPARACION", original_available=True,
        ) from exc


def _unused_database_name(backend, prefix: str) -> str:
    """Generate a bounded PostgreSQL identifier and explicitly reject collisions."""
    for _ in range(5):
        candidate = prefix + uuid.uuid4().hex
        if not backend.database_exists(candidate):
            return candidate
    raise RuntimeError("Could not reserve a unique restore database name")


def _drop_untrusted_temporary(backend, temporary_name: str) -> None:
    """Best-effort cleanup before cutover; the active database is still untouched."""
    try:
        backend.terminate_sessions(temporary_name)
        backend.drop_database(temporary_name)
    except Exception:
        # An orphan is safer than broadening the failure into data loss.
        pass


def _recover_database_swap(backend, database_name: str, swap: DatabaseSwap) -> bool:
    """Put the old database back without deleting either valid copy.

    PostgreSQL cannot atomically rename two databases.  If either rename below
    fails, every remaining database is deliberately preserved for manual
    recovery.
    """
    try:
        backend.terminate_sessions(database_name)
        backend.rename_database(database_name, swap.temporary_name)
    except Exception:
        return False
    try:
        backend.terminate_sessions(swap.backup_name)
        backend.rename_database(swap.backup_name, database_name)
    except Exception:
        return False
    return True


def _perform_restore(
    backend,
    database_name: str,
    archive: Path,
    swap_selected: bool,
    *,
    expected_schema_version: str,
) -> DatabaseSwap | None:
    """Prefer validation-database + cutover when the runtime proves the capability."""
    if swap_selected:
        try:
            temporary_name = _unused_database_name(backend, "restore_")
            backend.create_database(temporary_name)
        except Exception as exc:
            raise SwapExecutionError(
                str(exc), stage="CREACION_TEMPORAL", original_available=True,
            ) from exc
        try:
            # A new PostgreSQL database already owns an empty ``public``
            # schema.  Use the same exact restore path as in-place recovery so
            # the archive can create both ``public`` and ``app_migrations``.
            backend.restore_exact(
                temporary_name,
                archive,
                expected_schema_version=expected_schema_version,
            )
            if not backend.verify_structure(temporary_name) or not backend.health_check(temporary_name):
                raise RuntimeError("Validation database verification failed")
        except Exception as exc:
            _drop_untrusted_temporary(backend, temporary_name)
            raise SwapExecutionError(
                str(exc), stage="VALIDACION_TEMPORAL", original_available=True,
                temporary_name=temporary_name,
            ) from exc

        try:
            backup_name = _unused_database_name(backend, "restore_backup_")
        except Exception as exc:
            raise SwapExecutionError(
                str(exc), stage="RESERVA_RESPALDO", original_available=True,
                temporary_name=temporary_name,
            ) from exc
        try:
            backend.terminate_sessions(database_name)
            backend.rename_database(database_name, backup_name)
        except Exception as exc:
            # The canonical database was not renamed.  Keep the validated
            # temporary database too; it may be useful for a safe retry.
            raise SwapExecutionError(
                str(exc), stage="RENOMBRE_ACTIVA", original_available=True,
                temporary_name=temporary_name,
            ) from exc

        try:
            backend.terminate_sessions(temporary_name)
            backend.rename_database(temporary_name, database_name)
        except Exception as exc:
            try:
                backend.terminate_sessions(backup_name)
                backend.rename_database(backup_name, database_name)
            except Exception as recovery_exc:
                raise SwapExecutionError(
                    f"{exc}; original name recovery failed: {recovery_exc}",
                    stage="RECUPERACION_RENOMBRE",
                    original_available=False,
                    temporary_name=temporary_name, backup_name=backup_name,
                ) from exc
            raise SwapExecutionError(
                str(exc), stage="RENOMBRE_TEMPORAL", original_available=True,
                temporary_name=temporary_name,
            ) from exc
        return DatabaseSwap(temporary_name=temporary_name, backup_name=backup_name)
    else:
        backend.restore_exact(
            database_name,
            archive,
            expected_schema_version=expected_schema_version,
        )
        return None


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
            pre_version = pre.version_schema
            db.rollback()
        pre_archive = _read_storage_archive(storage, pre_key, pre_size, pre_sha, workdir,
                                            name="pre_restore.dump")
        engine_disposer(tenant_id)
        backend.terminate_sessions(database_name)
        backend.restore_exact(
            database_name,
            pre_archive,
            expected_schema_version=pre_version,
        )
        if not backend.verify_structure(database_name) or not backend.health_check(database_name):
            raise RuntimeError("Rollback verification failed")
        if not backend.verify_database_schema_version(database_name, pre_version):
            raise RuntimeError("Rollback schema version verification failed")
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
                if pre_version:
                    tenant.version_schema = pre_version
                tenant.ultima_verificacion = _now()
        _audit(db, user_id, empresa_id, restore_id, "ERROR_RESTORE", "ERROR", ip)
        if failure is None:
            _audit(db, user_id, empresa_id, restore_id, "COMPLETAR_ROLLBACK_RESTORE", "EXITO", ip)
        db.commit()
        return row


def _prepare_restore_window(
    session_factory: sessionmaker,
    *,
    restore_id: int,
    empresa_id: int,
    tenant_id: int,
    database_name: str,
    current_version: str,
    restored_version: str,
    storage_key: str,
    size_bytes: int,
    sha256: str,
    user_id: int,
    ip: str | None,
    backend,
    backup_runner,
    storage,
    workdir: Path,
) -> tuple[Path, bool, bool, int]:
    """Validate the restore target without mutating the tenant or its database.

    This runs between the ``PENDIENTE`` reservation and the ``RESTAURANDO``
    window. ``PENDIENTE`` belongs to the active restore states, so every
    failure here must end as ``ERROR``; otherwise the orphan reservation would
    block future restores and manual/automatic backups of the same tenant.
    Nothing in this function may modify the canonical database, so no
    ``_rollback``/``restore_exact`` is allowed on any exit path.
    """
    # 3. Validate the target archive; a failure never touches the DB.
    try:
        target_archive = _read_storage_archive(storage, storage_key, size_bytes, sha256, workdir)
        backend.list_archive(target_archive)
        archive_has_ledger = backend.archive_contains_schema(
            target_archive,
            "app_migrations",
        )
    except Exception as exc:
        _finalize_error(session_factory, restore_id, "VALIDACION", user_id, empresa_id, ip,
                        str(exc))
        reason = sanitize_error(str(exc)) or "Backup validation failed"
        raise RestoreExecutionError(f"Backup validation failed; tenant unchanged ({reason})") from None

    # 3b. The capability probe must fail before any destructive step: the
    # canonical database is intact, so no in-place rollback may run.
    swap_selected = _probe_swap_support(backend, _allow_database_swap())
    cross_version = restored_version != current_version
    if (
        not backend.is_known_schema_version(current_version)
        or not backend.supports_archive_version(
            restored_version,
            has_ledger=archive_has_ledger,
        )
    ):
        _finalize_error(
            session_factory,
            restore_id,
            "VALIDACION_VERSION",
            user_id,
            empresa_id,
            ip,
            "Restore schema version is unknown or unsupported by the archive",
        )
        raise RestoreExecutionError(
            "Restore schema version refused; tenant unchanged"
        )
    try:
        ledger_matches = bool(
            backend.verify_database_schema_version(database_name, current_version)
        )
    except Exception as exc:
        # A psql timeout/exit code is a preparation failure, not an active
        # restore: close the reservation and leave the tenant ACTIVA.
        _finalize_error(
            session_factory,
            restore_id,
            "VALIDACION_VERSION",
            user_id,
            empresa_id,
            ip,
            f"Tenant schema version verification failed: {exc}",
        )
        raise RestoreExecutionError(
            "Current tenant schema version could not be verified; tenant unchanged"
        ) from None
    if not ledger_matches:
        _finalize_error(
            session_factory,
            restore_id,
            "VALIDACION_VERSION",
            user_id,
            empresa_id,
            ip,
            "Current tenant schema version does not match its migration ledger",
        )
        raise RestoreExecutionError(
            "Current tenant schema version is inconsistent; tenant unchanged"
        )
    if cross_version and (
        not archive_has_ledger or not swap_selected
    ):
        _finalize_error(
            session_factory,
            restore_id,
            "VALIDACION_VERSION",
            user_id,
            empresa_id,
            ip,
            "Cross-version restore requires a complete ledger and validation database",
        )
        raise RestoreExecutionError(
            "Cross-version restore refused; tenant unchanged"
        )

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

    # 4b. Read-only preflight for the in-place strategy: the public schema
    # must be resettable without destroying shared objects. The tenant is
    # still ACTIVA and no session has been terminated yet.
    if not swap_selected:
        try:
            state = backend.inspect_public_schema(database_name)
            backend.assert_reset_safe(state)
        except SchemaResetRefused as exc:
            _finalize_error(session_factory, restore_id, "PREFLIGHT", user_id, empresa_id, ip, str(exc))
            raise RestoreExecutionError("Restore preflight refused; tenant unchanged") from None
        except Exception as exc:
            _finalize_error(session_factory, restore_id, "PREFLIGHT", user_id, empresa_id, ip, str(exc))
            raise RestoreExecutionError("Restore preflight failed; tenant unchanged") from None

    return target_archive, archive_has_ledger, swap_selected, pre.id


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
        current_version = tenant.version_schema
        restored_version = backup.version_schema
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
        # 3-4b. Reservation is already committed (PENDIENTE). Everything up to
        # the destructive window must be side-effect free on the tenant; any
        # unexpected failure closes the reservation as ERROR instead of leaving
        # an orphan PENDIENTE that would block restores and backups.
        try:
            (
                target_archive,
                _archive_has_ledger,
                swap_selected,
                pre_restore_backup_id,
            ) = _prepare_restore_window(
                session_factory,
                restore_id=restore_id,
                empresa_id=empresa_id,
                tenant_id=tenant_id,
                database_name=database_name,
                current_version=current_version,
                restored_version=restored_version,
                storage_key=storage_key,
                size_bytes=size_bytes,
                sha256=sha256,
                user_id=user_id,
                ip=ip,
                backend=backend,
                backup_runner=backup_runner,
                storage=storage,
                workdir=workdir,
            )
        except SwapExecutionError as exc:
            # The capability probe failed before any destructive step. The
            # canonical database is intact and no in-place rollback may run.
            _finalize_error(
                session_factory,
                restore_id,
                exc.stage,
                user_id,
                empresa_id,
                ip,
                str(exc),
            )
            raise RestoreExecutionError(
                "Restore capability probe failed; tenant unchanged"
            ) from None
        except RestoreExecutionError:
            # Already finalized inside the preparation step.
            raise
        except Exception as exc:
            # Last-resort net (e.g. an unexpected verification exception). The
            # canonical database was never modified and the tenant is ACTIVA.
            _finalize_error(session_factory, restore_id, "PREPARACION", user_id, empresa_id, ip,
                            str(exc) or "Restore preparation failed")
            raise RestoreExecutionError(
                "Restore preparation failed before any database change; tenant unchanged"
            ) from None

        # 5. Take the tenant out of service only now that the snapshot exists.
        try:
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
        except RestoreExecutionError:
            raise
        except Exception as exc:
            # Nothing was mutated: a failed transition must not keep the
            # reservation active nor leave the tenant out of service.
            _finalize_error(session_factory, restore_id, "PREPARACION", user_id, empresa_id, ip,
                            str(exc) or "Restore preparation failed",
                            revert_tenant_id=tenant_id, revert_state="ACTIVA")
            raise RestoreExecutionError(
                "Restore preparation failed; tenant restored to ACTIVA"
            ) from None

        # 6. Close tenant connections before cutover. A failure here did not mutate data.
        try:
            engine_disposer(tenant_id)
            backend.terminate_sessions(database_name)
        except Exception as exc:
            _finalize_error(session_factory, restore_id, "PREPARACION", user_id, empresa_id, ip,
                            str(exc), revert_tenant_id=tenant_id, revert_state="ACTIVA")
            raise RestoreExecutionError("Restore preparation failed; tenant restored to ACTIVA") from None

        # 7. Restore + verify. Any failure from here needs an automatic rollback.
        swap: DatabaseSwap | None = None
        try:
            swap = _perform_restore(
                backend,
                database_name,
                target_archive,
                swap_selected,
                expected_schema_version=restored_version,
            )
            if not backend.verify_structure(database_name) or not backend.health_check(database_name):
                raise RuntimeError("Final tenant verification failed")
            if not backend.verify_database_schema_version(
                database_name,
                restored_version,
            ):
                raise RuntimeError("Final schema version verification failed")
        except SwapExecutionError as exc:
            retencion = None
            if exc.temporary_name or exc.backup_name:
                motivos = {
                    "VALIDACION_TEMPORAL": "validacion_temporal_fallida_temporal_huerfana_o_eliminada",
                    "RESERVA_RESPALDO": "reserva_respaldo_fallida_temporal_retenida",
                    "RENOMBRE_ACTIVA": "renombre_activa_fallido_temporal_retenida",
                    "RENOMBRE_TEMPORAL": "renombre_temporal_fallido_original_recuperado_temporal_retenida",
                    "RECUPERACION_RENOMBRE": "recuperacion_renombre_fallida_temporal_y_respaldo_retenidos",
                }
                retencion = _retention_note(
                    restore_id=restore_id,
                    tenant_database_id=tenant_id,
                    database_name=database_name,
                    etapa=exc.stage,
                    motivo=motivos.get(exc.stage, "cutover_fallido"),
                    temporary_name=exc.temporary_name,
                    backup_name=exc.backup_name,
                )
            _finalize_error(
                session_factory,
                restore_id,
                exc.stage,
                user_id,
                empresa_id,
                ip,
                str(exc),
                revert_tenant_id=tenant_id,
                revert_state="ACTIVA" if exc.original_available else "ERROR",
                revert_version=current_version if exc.original_available else None,
                retencion=retencion,
            )
            if exc.original_available:
                raise RestoreExecutionError(
                    "Restore cutover failed; original database and validated temporary database retained"
                ) from None
            raise RestoreExecutionError(
                "Restore cutover and name recovery failed; databases retained for manual intervention"
            ) from None
        except Exception as exc:
            if swap is not None:
                recovered = _recover_database_swap(backend, database_name, swap)
                _finalize_error(
                    session_factory,
                    restore_id,
                    "VALIDACION_DESTINO" if recovered else "RECUPERACION_DESTINO",
                    user_id,
                    empresa_id,
                    ip,
                    str(exc),
                    revert_tenant_id=tenant_id,
                    revert_state="ACTIVA" if recovered else "ERROR",
                    revert_version=current_version if recovered else None,
                    retencion=_retention_note(
                        restore_id=restore_id,
                        tenant_database_id=tenant_id,
                        database_name=database_name,
                        etapa="VALIDACION_DESTINO" if recovered else "RECUPERACION_DESTINO",
                        motivo=(
                            "validacion_destino_fallida_original_recuperado_nueva_base_retenida"
                            if recovered
                            else "validacion_destino_y_recuperacion_fallidas_bases_retenidas"
                        ),
                        temporary_name=swap.temporary_name,
                        backup_name=None if recovered else swap.backup_name,
                    ),
                )
                if recovered:
                    raise RestoreExecutionError(
                        "Restored destination validation failed; original database recovered and new database retained"
                    ) from None
                raise RestoreExecutionError(
                    "Restored destination validation and recovery failed; databases retained for manual intervention"
                ) from None
            row = _rollback(session_factory, restore_id, empresa_id, tenant_id, database_name,
                            pre_restore_backup_id, user_id, ip, backend, storage,
                            engine_disposer, workdir, str(exc))
            if row.rollback_estado == "ERROR":
                raise RestoreExecutionError(
                    "Restore failed and rollback failed; manual intervention required") from None
            raise RestoreExecutionError(
                "Restore failed; tenant rolled back to the pre-restore state") from None

        # 8. Success: reactivate only after the final verification passed.
        try:
            with session_factory() as db:
                tenant_row = db.get(TenantDatabase, tenant_id)
                restore_row = db.get(RestoreTenant, restore_id)
                if (
                    tenant_row is None
                    or restore_row is None
                    or tenant_row.database_name != database_name
                    or tenant_row.estado != "RESTAURANDO"
                ):
                    raise RuntimeError("Tenant metadata changed during restore")
                tenant_row.estado = "ACTIVA"
                # The physical schema and its ledger were restored from the same
                # backup snapshot; the Control Plane must describe that state.
                tenant_row.version_schema = restored_version
                tenant_row.ultima_verificacion = _now()
                restore_row.estado = "COMPLETADO"
                restore_row.etapa = "COMPLETADO"
                restore_row.fecha_fin = _now()
                restore_row.mensaje_error = None
                _audit(db, user_id, empresa_id, restore_id, "COMPLETAR_RESTORE", "EXITO", ip)
                db.commit()
        except Exception as exc:
            if swap is not None:
                recovered = _recover_database_swap(backend, database_name, swap)
                _finalize_error(
                    session_factory,
                    restore_id,
                    "METADATOS" if recovered else "RECUPERACION_METADATOS",
                    user_id,
                    empresa_id,
                    ip,
                    str(exc),
                    revert_tenant_id=tenant_id,
                    revert_state="ACTIVA" if recovered else "ERROR",
                    revert_version=current_version if recovered else None,
                    retencion=_retention_note(
                        restore_id=restore_id,
                        tenant_database_id=tenant_id,
                        database_name=database_name,
                        etapa="METADATOS" if recovered else "RECUPERACION_METADATOS",
                        motivo=(
                            "metadatos_fallidos_original_recuperado_nueva_base_retenida"
                            if recovered
                            else "metadatos_y_recuperacion_fallidos_bases_retenidas"
                        ),
                        temporary_name=swap.temporary_name,
                        backup_name=None if recovered else swap.backup_name,
                    ),
                )
                raise RestoreExecutionError(
                    "Restore metadata update failed; databases retained for recovery"
                ) from None
            row = _rollback(session_factory, restore_id, empresa_id, tenant_id, database_name,
                            pre_restore_backup_id, user_id, ip, backend, storage,
                            engine_disposer, workdir, str(exc))
            if row.rollback_estado == "ERROR":
                raise RestoreExecutionError(
                    "Restore metadata update and rollback failed; manual intervention required"
                ) from None
            raise RestoreExecutionError(
                "Restore metadata update failed; tenant rolled back to the pre-restore state"
            ) from None

        # The old database is intentionally retained until both physical and
        # Control Plane validations have succeeded.  Cleanup failure is not a
        # reason to overwrite the healthy new destination.
        if swap is not None:
            try:
                with session_factory() as db:
                    tenant_row = db.get(TenantDatabase, tenant_id)
                    restore_row = db.get(RestoreTenant, restore_id)
                    metadata_valid = bool(
                        tenant_row is not None
                        and restore_row is not None
                        and tenant_row.database_name == database_name
                        and tenant_row.estado == "ACTIVA"
                        and tenant_row.version_schema == restored_version
                        and restore_row.estado == "COMPLETADO"
                    )
                    db.rollback()
                if not metadata_valid:
                    raise RuntimeError("Final tenant metadata validation failed")
                backend.terminate_sessions(swap.backup_name)
                backend.drop_database(swap.backup_name)
            except Exception:
                retencion = _retention_note(
                    restore_id=restore_id,
                    tenant_database_id=tenant_id,
                    database_name=database_name,
                    etapa="COMPLETADO_RESPALDO_RETENIDO",
                    motivo="respaldo_anterior_retenido_destino_temporal_promovido",
                    temporary_name=swap.temporary_name,
                    temporary_label="base_temporal_promovida",
                    backup_name=swap.backup_name,
                )
                with session_factory() as db:
                    restore_row = db.get(RestoreTenant, restore_id)
                    if restore_row is not None:
                        restore_row.etapa = "COMPLETADO_RESPALDO_RETENIDO"
                        restore_row.mensaje_error = sanitize_error(
                            "Restore completed; previous database retained for operator review; "
                            + retencion
                        )
                        _audit(
                            db, user_id, empresa_id, restore_id,
                            "RETENER_RESPALDO_RESTORE", "ERROR", ip,
                            detalle=retencion,
                        )
                    db.commit()

        with session_factory() as db:
            return db.get(RestoreTenant, restore_id)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def list_restores(db: Session, empresa_id: int | None, estado: str | None) -> list[RestoreTenant]:
    query = select(RestoreTenant)
    if empresa_id is not None:
        query = query.where(RestoreTenant.empresa_id == empresa_id)
    if estado is not None:
        query = query.where(RestoreTenant.estado == estado)
    return list(db.scalars(query.order_by(RestoreTenant.id.desc()).limit(200)).all())
