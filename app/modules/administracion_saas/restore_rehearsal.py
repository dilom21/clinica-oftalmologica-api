"""Guarded restore rehearsal against a disposable probe database.

The rehearsal proves that a tenant backup can actually be restored, mutated and
rolled back WITHOUT ever touching a real tenant database. A throwaway probe
database is created, restored, mutated, rolled back from a PRE_RESTORE snapshot
and then deliberately preserved for inspection. The probe is only ever dropped
through an explicit, exactly confirmed :func:`cleanup_probe` call.

No tenant credentials ever travel in argv, and ``shell=True`` is never used. The
``psql`` password is only ever injected into the child environment.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Container
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scripts.saas.provision_tenant import PostgresConnection, SubprocessRunner

from .backup_runner import _client_path, _minimal_client_environment
from .restore_service import _read_storage_archive
from .service import sanitize_error

PROBE_PREFIX = "restore_probe_"
TENANT_PREFIX = "tenant_"
PROBE_SUFFIX_HEX = 8
RESERVED_DATABASES = frozenset({"postgres", "template0", "template1", "saas_control"})
IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
CRITICAL_TENANT_TABLES = ("usuario", "rol", "paciente", "cita", "historial_clinico")

_TENANT_DATABASE_PATTERN = re.compile(r"^tenant_[a-z0-9_]+$")
_TABLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MAX_IDENTIFIER_LENGTH = 63


class RehearsalError(RuntimeError):
    """Base class for restore rehearsal failures."""


class RehearsalRefused(RehearsalError):
    """A guard or prohibition stopped the rehearsal before any mutation."""


class RehearsalExecutionError(RehearsalError):
    """The rehearsal ran but a step failed; the probe may be preserved.

    ``stage`` names the last plan step reached (see :data:`PLAN_STEPS`) and
    ``diagnostic`` carries a sanitized, machine-readable failure report so the
    next run can identify the exact stage without leaking secrets.
    """

    def __init__(self, message: str, *, stage: "str | None" = None,
                 diagnostic: "RehearsalDiagnostic | None" = None):
        super().__init__(message)
        self.stage = stage
        self.diagnostic = diagnostic


# --------------------------------------------------------------- diagnostics
# Extra redaction on top of ``sanitize_error``: JWTs, long hex hashes and
# absolute filesystem paths must never reach operator output.
_SQLSTATE_PATTERN = re.compile(r"SQLSTATE[:\s]+([0-9A-Z]{5})", re.IGNORECASE)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*")
_HASH_PATTERN = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_LOCAL_PATH_PATTERN = re.compile(r"""(?:[A-Za-z]:\\[^\s"']+|/(?:home|Users|tmp|var|private)/[^\s"']+)""")


def sanitize_diagnostic(message: str | None) -> str:
    """Sanitize a failure message for operator output; never a secret."""
    text = sanitize_error(message) or ""
    text = _JWT_PATTERN.sub("[REDACTED_JWT]", text)
    text = _HASH_PATTERN.sub("[REDACTED_HASH]", text)
    text = _LOCAL_PATH_PATTERN.sub("[REDACTED_PATH]", text)
    return text


@dataclass(frozen=True)
class RehearsalDiagnostic:
    """Sanitized, machine-readable report of a rehearsal failure."""

    stage: str
    exception_class: str
    sqlstate: str | None
    returncode: int | None
    message: str

    def to_dict(self) -> dict:
        """Return a JSON-serializable diagnostic that never contains secrets."""
        return {
            "stage": self.stage,
            "exception_class": self.exception_class,
            "sqlstate": self.sqlstate,
            "returncode": self.returncode,
            "message": self.message,
        }


def _exception_chain(exc: BaseException):
    """Yield an exception and its cause/context chain, without cycles."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def build_rehearsal_diagnostic(exc: BaseException, stage: str,
                               message: str | None = None) -> RehearsalDiagnostic:
    """Extract stage/class/sqlstate/returncode/message without leaking secrets.

    ``message`` overrides the reported text (used when the caller already holds a
    sanitized stage-level message); class/sqlstate/returncode are still taken from
    the deepest exception in ``exc``'s cause/context chain.
    """
    chain = list(_exception_chain(exc))
    root = chain[-1] if chain else exc
    sqlstate: str | None = None
    returncode: int | None = None
    for item in chain:
        for attr in ("sqlstate", "pgcode"):
            value = getattr(item, attr, None)
            if sqlstate is None and isinstance(value, str) and value:
                sqlstate = value
        origin = getattr(item, "orig", None)
        value = getattr(origin, "sqlstate", None)
        if sqlstate is None and isinstance(value, str) and value:
            sqlstate = value
        rc = getattr(item, "returncode", None)
        if returncode is None and isinstance(rc, int):
            returncode = rc
    text = message if message is not None else sanitize_diagnostic(str(exc))
    if sqlstate is None:
        found = _SQLSTATE_PATTERN.search(sanitize_diagnostic(str(root)))
        if found:
            sqlstate = found.group(1)
    return RehearsalDiagnostic(
        stage=stage,
        exception_class=type(root).__name__,
        sqlstate=sqlstate,
        returncode=returncode,
        message=text,
    )


# --------------------------------------------------------------- name helpers
def build_probe_database_name(tenant_database_name: str) -> str:
    """Derive a unique, disposable probe name from a real tenant database name."""
    if not isinstance(tenant_database_name, str) or not _TENANT_DATABASE_PATTERN.fullmatch(
        tenant_database_name
    ):
        raise RehearsalRefused("tenant database name is not a safe tenant identifier")
    slug = tenant_database_name[len(TENANT_PREFIX):]
    name = f"{PROBE_PREFIX}{slug}_{uuid.uuid4().hex[:PROBE_SUFFIX_HEX]}"
    if not IDENTIFIER_PATTERN.fullmatch(name) or len(name) > _MAX_IDENTIFIER_LENGTH:
        raise RehearsalRefused("probe database name is not a safe identifier")
    return name


def build_prepared_probe_name(tenant_database_name: str, backup_id: int) -> str:
    """Derive the deterministic probe name shared by --prepare and --execute."""
    if not isinstance(tenant_database_name, str) or not _TENANT_DATABASE_PATTERN.fullmatch(
        tenant_database_name
    ):
        raise RehearsalRefused("tenant database name is not a safe tenant identifier")
    if not isinstance(backup_id, int) or backup_id <= 0:
        raise RehearsalRefused("backup identifier must be a positive integer")
    slug = tenant_database_name[len(TENANT_PREFIX):]
    digest = hashlib.sha256(f"{tenant_database_name}:{backup_id}".encode("utf-8")).hexdigest()
    name = f"{PROBE_PREFIX}{slug}_{digest[:PROBE_SUFFIX_HEX]}"
    if not IDENTIFIER_PATTERN.fullmatch(name) or len(name) > _MAX_IDENTIFIER_LENGTH:
        raise RehearsalRefused("probe database name is not a safe identifier")
    return name


def assert_disposable_probe(name: str, *, tenant_databases: Container[str]) -> str:
    """Refuse anything that is not an explicitly disposable probe database."""
    if not isinstance(name, str) or not IDENTIFIER_PATTERN.fullmatch(name):
        raise RehearsalRefused("not a safe identifier")
    if not name.startswith(PROBE_PREFIX):
        raise RehearsalRefused("probe prefix required")
    if name.startswith(TENANT_PREFIX):
        raise RehearsalRefused("tenant databases are never rehearsal targets")
    if name in RESERVED_DATABASES:
        raise RehearsalRefused("reserved database")
    if name in tenant_databases:
        raise RehearsalRefused("tenant database")
    return name


def validate_probe_creation(
    name: str, *, tenant_databases: Container[str], exists
) -> str:
    """Assert the name is disposable and that the probe does not already exist."""
    assert_disposable_probe(name, tenant_databases=tenant_databases)
    if exists(name):
        raise RehearsalRefused("probe database already exists")
    return name


def require_probe_confirmation(name: str, confirmation: str | None) -> None:
    """Require an exact, non-empty confirmation token equal to the probe name."""
    if (confirmation or "").strip() != name:
        raise RehearsalRefused("confirmation does not match the probe database")


# ------------------------------------------------------------ database access
class RehearsalDatabase(Protocol):
    def public_tables(self, database: str) -> list[str]: ...
    def count_rows(self, database: str, table: str) -> int: ...
    def sequence_count(self, database: str) -> int: ...
    def constraint_count(self, database: str) -> int: ...
    def index_count(self, database: str) -> int: ...
    def execute(self, database: str, sql: str) -> None: ...


class PostgresRehearsalDatabase:
    """Read-only introspection plus tightly scoped mutation, via the psql client."""

    def __init__(self, connection: PostgresConnection, pg_bin_dir: str | None = None,
                 timeout_seconds: int = 900, runner=None):
        if timeout_seconds <= 0:
            raise ValueError("Rehearsal timeout must be positive")
        self.psql = _client_path("psql", pg_bin_dir)
        if self.psql is None:
            raise RuntimeError("PostgreSQL rehearsal client is unavailable")
        self.connection = connection
        self.timeout_seconds = timeout_seconds
        self.runner = runner or SubprocessRunner()

    def _env(self) -> dict[str, str]:
        env = _minimal_client_environment()
        env["PGPASSWORD"] = self.connection.password
        env["PGSSLMODE"] = self.connection.sslmode
        return env

    def _host_args(self) -> list[str]:
        return ["-h", self.connection.host, "-p", str(self.connection.port),
                "-U", self.connection.user]

    def _query(self, database: str, sql: str) -> str:
        return self.runner.run(
            [str(self.psql), *self._host_args(), "-d", database, "-tAc", sql],
            self._env(), self.timeout_seconds,
        )

    def execute(self, database: str, sql: str) -> None:
        self.runner.run(
            [str(self.psql), *self._host_args(), "-d", database,
             "-v", "ON_ERROR_STOP=1", "-c", sql],
            self._env(), self.timeout_seconds,
        )

    def _scalar(self, database: str, sql: str) -> int:
        output = self._query(database, sql).strip()
        if not output.isdigit():
            raise RehearsalError("rehearsal introspection returned an unexpected value")
        return int(output)

    def public_tables(self, database: str) -> list[str]:
        output = self._query(
            database,
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname;",
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def count_rows(self, database: str, table: str) -> int:
        if not isinstance(table, str) or not _TABLE_PATTERN.fullmatch(table):
            raise RehearsalError("table name is not a safe identifier")
        return self._scalar(database, f'SELECT count(*) FROM public."{table}";')

    def sequence_count(self, database: str) -> int:
        return self._scalar(
            database,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='S';",
        )

    def constraint_count(self, database: str) -> int:
        return self._scalar(
            database,
            "SELECT count(*) FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND con.contype IN ('p','u','f','c');",
        )

    def index_count(self, database: str) -> int:
        return self._scalar(
            database,
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public';",
        )


# ----------------------------------------------------------------- fingerprint
@dataclass(frozen=True)
class RehearsalFingerprint:
    tables: tuple[str, ...]
    sequences: int
    constraints: int
    indexes: int
    row_counts: tuple[tuple[str, int], ...]
    total_rows: int
    critical_tables: tuple[str, ...]


def fingerprint(database: RehearsalDatabase, name: str) -> RehearsalFingerprint:
    """Capture the structural and row-count state of a probe database."""
    tables = tuple(database.public_tables(name))
    row_counts = tuple(sorted((table, int(database.count_rows(name, table))) for table in tables))
    return RehearsalFingerprint(
        tables=tables,
        sequences=int(database.sequence_count(name)),
        constraints=int(database.constraint_count(name)),
        indexes=int(database.index_count(name)),
        row_counts=row_counts,
        total_rows=sum(count for _, count in row_counts),
        critical_tables=tuple(table for table in CRITICAL_TENANT_TABLES if table in tables),
    )


# ---------------------------------------------------------------------- report
@dataclass(frozen=True)
class RehearsalReport:
    backup_id: int
    probe_database: str
    archive_name: str
    size_bytes: int
    sha256: str
    tables: int
    sequences: int
    constraints: int
    indexes: int
    rows_before: int
    rows_mutated: int
    rows_after_rollback: int
    critical_tables: tuple[str, ...]
    steps: tuple[str, ...]
    dropped: bool = False

    def to_dict(self) -> dict:
        """Return a JSON-serializable summary that never contains credentials."""
        return {
            "backup_id": self.backup_id,
            "probe_database": self.probe_database,
            "archive_name": self.archive_name,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "tables": self.tables,
            "sequences": self.sequences,
            "constraints": self.constraints,
            "indexes": self.indexes,
            "rows_before": self.rows_before,
            "rows_mutated": self.rows_mutated,
            "rows_after_rollback": self.rows_after_rollback,
            "critical_tables": list(self.critical_tables),
            "steps": list(self.steps),
            "dropped": self.dropped,
        }


# ---------------------------------------------------------------- orchestration
@dataclass(frozen=True)
class RehearsalDependencies:
    backend: object
    backup_runner: object
    database: RehearsalDatabase
    storage: object


def _simulate_mutation(database: RehearsalDatabase, probe_database: str,
                       before: RehearsalFingerprint) -> int:
    """Mutate only the probe and return the number of removed rows."""
    database.execute(
        probe_database,
        'CREATE TABLE IF NOT EXISTS public."rehearsal_probe_mutation" (id integer);',
    )
    database.execute(
        probe_database,
        'INSERT INTO public."rehearsal_probe_mutation" (id) VALUES (1);',
    )
    rows_mutated = 0
    if before.row_counts:
        table, count = max(before.row_counts, key=lambda item: item[1])
        if count > 0:
            if not _TABLE_PATTERN.fullmatch(table):
                raise RehearsalExecutionError("inspected table name is not a safe identifier")
            database.execute(probe_database, f'DELETE FROM public."{table}";')
            rows_mutated = count
    return rows_mutated


def rehearse_restore(
    deps: RehearsalDependencies, *,
    backup_id: int,
    storage_key: str,
    size_bytes: int,
    sha256: str,
    probe_database: str,
    tenant_databases: Container[str],
    confirmation: str | None,
    workdir: Path,
) -> RehearsalReport:
    """Restore, mutate and roll back a backup against a preserved probe database."""
    steps: list[str] = []
    stage = "confirmation"

    def fail(message: str, cause: BaseException | None = None) -> RehearsalExecutionError:
        """Build a stage-aware, sanitized execution error."""
        text = sanitize_diagnostic(message) or "restore rehearsal failed"
        reference = cause if cause is not None else RehearsalExecutionError(text)
        return RehearsalExecutionError(
            text, stage=stage,
            diagnostic=build_rehearsal_diagnostic(reference, stage, message=text))

    try:
        require_probe_confirmation(probe_database, confirmation)
        steps.append("confirmation")
        stage = "guard"
        validate_probe_creation(probe_database, tenant_databases=tenant_databases,
                                exists=deps.backend.database_exists)
        steps.append("guard")

        stage = "archive"
        archive = _read_storage_archive(deps.storage, storage_key, size_bytes, sha256, workdir)
        steps.append("archive")
        stage = "list_archive"
        deps.backend.list_archive(archive)
        steps.append("list_archive")
        stage = "create_probe_database"
        deps.backend.create_database(probe_database)
        steps.append("create_probe_database")

        stage = "restore_target"
        try:
            # Mirror the selected in-place strategy exactly: restore_service
            # performs an exact public-schema rebuild (reset + clean restore),
            # so the probe must exercise the same restore_exact path.
            deps.backend.restore_exact(probe_database, archive)
            healthy = deps.backend.health_check(probe_database)
            structured = deps.backend.verify_structure(probe_database)
        except Exception as exc:
            raise fail(
                f"probe restore failed: {exc}; probe {probe_database} preserved", exc) from None
        if not healthy or not structured:
            raise fail(f"probe verification failed; probe {probe_database} preserved")
        steps.append("restore_target")

        stage = "fingerprint_before"
        before = fingerprint(deps.database, probe_database)
        steps.append("fingerprint_before")
        if before.total_rows <= 0:
            raise fail("restored probe has no data rows")
        if before.critical_tables != CRITICAL_TENANT_TABLES:
            raise fail("restored probe is missing critical tenant tables")

        stage = "pre_restore"
        pre_path = workdir / "probe_pre_restore.dump"
        deps.backup_runner.dump(probe_database, pre_path)
        deps.backup_runner.verify(pre_path)
        steps.append("pre_restore")

        stage = "simulate_mutation"
        rows_mutated = _simulate_mutation(deps.database, probe_database, before)
        steps.append("simulate_mutation")
        stage = "fingerprint_after_mutation"
        after_mutation = fingerprint(deps.database, probe_database)
        steps.append("fingerprint_after_mutation")
        if after_mutation == before:
            raise fail("simulated mutation was not observable")

        stage = "rollback"
        try:
            deps.backend.restore_exact(probe_database, pre_path)
            healthy = deps.backend.health_check(probe_database)
            structured = deps.backend.verify_structure(probe_database)
        except Exception as exc:
            raise fail(
                f"rollback failed: {exc}; probe {probe_database} preserved", exc) from None
        if not healthy or not structured:
            raise fail(f"rollback verification failed; probe {probe_database} preserved")
        steps.append("rollback")

        stage = "fingerprint_after_rollback"
        after_rollback = fingerprint(deps.database, probe_database)
        steps.append("fingerprint_after_rollback")
        if after_rollback != before:
            raise fail("rollback did not restore the original state")

        return RehearsalReport(
            backup_id=backup_id,
            probe_database=probe_database,
            archive_name=archive.name,
            size_bytes=size_bytes,
            sha256=sha256,
            tables=len(before.tables),
            sequences=before.sequences,
            constraints=before.constraints,
            indexes=before.indexes,
            rows_before=before.total_rows,
            rows_mutated=rows_mutated,
            rows_after_rollback=after_rollback.total_rows,
            critical_tables=before.critical_tables,
            steps=tuple(steps),
            dropped=False,
        )
    except RehearsalRefused:
        raise
    except RehearsalExecutionError as exc:
        # Steps raised directly (e.g. mutation guard) still report their stage.
        if exc.diagnostic is None:
            exc.diagnostic = build_rehearsal_diagnostic(exc, stage)
        if exc.stage is None:
            exc.stage = stage
        raise
    except Exception as exc:
        raise fail(f"restore rehearsal failed: {exc}", exc) from None


def cleanup_probe(
    deps: RehearsalDependencies, *,
    probe_database: str,
    tenant_databases: Container[str],
    confirmation: str | None,
) -> bool:
    """Drop a preserved probe database after an exact confirmation."""
    require_probe_confirmation(probe_database, confirmation)
    assert_disposable_probe(probe_database, tenant_databases=tenant_databases)
    try:
        deps.backend.terminate_sessions(probe_database)
        deps.backend.drop_database(probe_database)
    except Exception as exc:
        raise RehearsalExecutionError(
            sanitize_error(f"probe cleanup failed: {exc}") or "probe cleanup failed"
        ) from None
    return True
