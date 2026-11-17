"""Guarded CU31 migration runner for existing tenant databases.

``--plan`` is read-only. ``--execute`` requires both the exact plan hash and
``CU31_TENANT_MIGRATIONS_ALLOW=1``. No URL, host, username or password is
included in output or diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.modules.administracion_saas.tenant_migrator import (  # noqa: E402
    LedgerState,
    MigrationArtifact,
    MigrationError,
    TenantTarget,
    build_plan,
    execute_plan,
    load_bundle,
)


EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_FAILED = 3
EXIT_REFUSED = 4
ALLOW_ENV = "CU31_TENANT_MIGRATIONS_ALLOW"
MIGRATION_ROOT = ROOT / "database" / "tenant_migrations"
_TEST_DATABASE = re.compile(r"^tenant_[a-z0-9_]+_(?:test|testing)$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_ERROR = re.compile(
    r"(?i)(postgres(?:ql)?://[^\s]+|database_url\s*=\s*[^\s]+|"
    r"password\s*=\s*[^\s]+|host\s*=\s*[^\s]+)"
)


def _sanitize(message: object) -> str:
    text = str(message).replace("\r", " ").replace("\n", " ")
    return _SAFE_ERROR.sub("[REDACTED]", text)[:500]


class SqlAlchemyControlPlane:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def list_active_targets(self, database_name: str | None = None):
        from sqlalchemy import text

        session = self._session_factory()
        try:
            rows = session.execute(text("""
                SELECT td.id AS tenant_database_id,
                       e.codigo AS empresa_codigo,
                       td.database_name,
                       td.version_schema
                  FROM saas_control.tenant_database td
                  JOIN saas_control.empresa e ON e.id = td.empresa_id
                 WHERE td.estado = 'ACTIVA'
                   AND (CAST(:database_name AS VARCHAR) IS NULL
                        OR td.database_name = :database_name)
                 ORDER BY td.database_name
            """), {"database_name": database_name}).mappings().all()
            return tuple(TenantTarget(
                tenant_database_id=row["tenant_database_id"],
                empresa_codigo=row["empresa_codigo"],
                database_name=row["database_name"],
                version_schema=row["version_schema"],
                managed=True,
            ) for row in rows)
        finally:
            session.close()

    def known_database_names(self) -> frozenset[str]:
        from sqlalchemy import text

        session = self._session_factory()
        try:
            return frozenset(session.execute(text(
                "SELECT database_name FROM saas_control.tenant_database"
            )).scalars().all())
        finally:
            session.close()

    def assert_target_current(self, target: TenantTarget) -> None:
        from sqlalchemy import text

        if target.tenant_database_id is None:
            raise MigrationError("Managed tenant has no control-plane identifier")
        session = self._session_factory()
        try:
            row = session.execute(text("""
                SELECT database_name, estado, version_schema
                  FROM saas_control.tenant_database
                 WHERE id = :tenant_id
            """), {"tenant_id": target.tenant_database_id}).mappings().first()
            if row is None or (
                row["database_name"] != target.database_name
                or row["estado"] != "ACTIVA"
                or row["version_schema"] != target.version_schema
            ):
                raise MigrationError("Control-plane tenant changed after planning")
        finally:
            session.close()

    def update_schema_version(
        self, target: TenantTarget, schema_version: str,
    ) -> None:
        from sqlalchemy import text

        if target.tenant_database_id is None:
            raise MigrationError("Managed tenant has no control-plane identifier")
        session = self._session_factory()
        try:
            result = session.execute(text("""
                UPDATE saas_control.tenant_database
                   SET version_schema = :schema_version,
                       ultima_verificacion = CURRENT_TIMESTAMP
                 WHERE id = :tenant_id
                   AND database_name = :database_name
                   AND estado = 'ACTIVA'
                   AND version_schema IS NOT DISTINCT FROM :previous_version
            """), {
                "schema_version": schema_version,
                "tenant_id": target.tenant_database_id,
                "database_name": target.database_name,
                "previous_version": target.version_schema,
            })
            if result.rowcount != 1:
                raise MigrationError(
                    "Control-plane tenant changed before its version update"
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def _execute_script(connection, sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        while cursor.nextset():
            pass


class PsycopgTenantDatabase:
    LOCK_TIMEOUT = "5s"
    STATEMENT_TIMEOUT = "120s"

    def __init__(self, connection, database_name: str, runner_version: str):
        self._connection = connection
        self._database_name = database_name
        self._runner_version = runner_version
        digest = hashlib.sha256(
            f"cu31:{database_name}:{runner_version}".encode("utf-8")
        ).digest()[:8]
        self._lock_id = int.from_bytes(digest, "big", signed=True)

    def ledger_state(self) -> LedgerState:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass('app_migrations.schema_migration') IS NOT NULL"
            )
            exists = bool(cursor.fetchone()[0])
            if not exists:
                return LedgerState(False)
            cursor.execute("""
                SELECT version, checksum_sha256
                  FROM app_migrations.schema_migration
                 ORDER BY version
            """)
            return LedgerState(True, tuple(
                (str(row[0]), str(row[1]).strip()) for row in cursor.fetchall()
            ))

    def run_preflight(self, sql: str) -> None:
        if not re.search(
            r"(?im)^\s*BEGIN\s+TRANSACTION\s+READ\s+ONLY\s*;", sql,
        ):
            raise MigrationError("Preflight is not read-only")
        _execute_script(self._connection, sql)

    def acquire_lock(self) -> bool:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (self._lock_id,))
            return bool(cursor.fetchone()[0])

    def release_lock(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (self._lock_id,))
            if not cursor.fetchone()[0]:
                raise MigrationError("Tenant advisory lock was unexpectedly lost")

    def fingerprint_legacy_table(self) -> str:
        payload: list[object] = []
        with self._connection.cursor() as cursor:
            cursor.execute("""
                SELECT column_name, data_type, udt_name, is_nullable,
                       coalesce(column_default, '')
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = 'servicios_oftalmologicos'
                 ORDER BY ordinal_position
            """)
            columns = cursor.fetchall()
            if not columns:
                raise MigrationError("Historical table is missing")
            payload.append(columns)
            cursor.execute("""
                SELECT c.conname, c.contype, pg_get_constraintdef(c.oid)
                  FROM pg_constraint c
                 WHERE c.conrelid = 'public.servicios_oftalmologicos'::regclass
                 ORDER BY c.contype, c.conname
            """)
            payload.append(cursor.fetchall())
            cursor.execute("""
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND tablename = 'servicios_oftalmologicos'
                 ORDER BY indexname
            """)
            payload.append(cursor.fetchall())
            cursor.execute("""
                SELECT count(*), md5(coalesce(string_agg(
                    row_to_json(t)::text, E'\\n' ORDER BY t.id
                ), ''))
                  FROM public.servicios_oftalmologicos t
            """)
            payload.append(cursor.fetchone())
        canonical = json.dumps(payload, default=str, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _set_local_timeouts(self, cursor) -> None:
        # PostgreSQL SET does not accept extended-protocol bind parameters.
        # These values are class constants, never user-controlled input.
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        cursor.execute("SET LOCAL statement_timeout = '120s'")

    def _record(self, cursor, migration: MigrationArtifact, duration_ms: int) -> None:
        cursor.execute("""
            INSERT INTO app_migrations.schema_migration
                (version, nombre, checksum_sha256, duracion_ms, runner_version)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            migration.version,
            migration.name,
            migration.checksum,
            max(0, duration_ms),
            self._runner_version,
        ))

    def bootstrap_ledger(self, sql: str) -> None:
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                self._set_local_timeouts(cursor)
                cursor.execute(sql)
                cursor.execute("""
                    WITH esperada(nombre, tipo, not_null) AS (
                        VALUES
                            ('version', 'character varying(20)', TRUE),
                            ('nombre', 'character varying(150)', TRUE),
                            ('checksum_sha256', 'character(64)', TRUE),
                            ('aplicada_en', 'timestamp with time zone', TRUE),
                            ('duracion_ms', 'bigint', TRUE),
                            ('runner_version', 'character varying(50)', TRUE)
                    )
                    SELECT count(*)
                      FROM esperada e
                      JOIN pg_attribute a
                        ON a.attrelid = 'app_migrations.schema_migration'::regclass
                       AND a.attname = e.nombre
                       AND a.attnum > 0 AND NOT a.attisdropped
                     WHERE format_type(a.atttypid, a.atttypmod) = e.tipo
                       AND a.attnotnull = e.not_null
                """)
                if cursor.fetchone()[0] != 6:
                    raise MigrationError("Tenant ledger has an incompatible shape")
                cursor.execute("""
                    SELECT
                        (SELECT count(*) FROM pg_attribute
                          WHERE attrelid = 'app_migrations.schema_migration'::regclass
                            AND attnum > 0 AND NOT attisdropped),
                        (SELECT count(*) FROM pg_constraint
                          WHERE conrelid = 'app_migrations.schema_migration'::regclass
                            AND contype = 'p'),
                        (SELECT count(*) FROM pg_constraint
                          WHERE conrelid = 'app_migrations.schema_migration'::regclass
                            AND conname IN (
                                'ck_schema_migration_checksum',
                                'ck_schema_migration_duracion'
                            ) AND contype = 'c')
                """)
                column_count, primary_keys, checks = cursor.fetchone()
                if column_count != 6 or primary_keys != 1 or checks != 2:
                    raise MigrationError("Tenant ledger constraints are incomplete")
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_roles r
                         WHERE r.rolname IN ('anon', 'authenticated')
                           AND (
                               has_schema_privilege(r.rolname, 'app_migrations', 'USAGE')
                               OR has_table_privilege(
                                   r.rolname,
                                   'app_migrations.schema_migration',
                                   'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                               )
                           )
                    )
                """)
                if cursor.fetchone()[0]:
                    raise MigrationError("Tenant ledger is exposed to Data API roles")

    def apply_transactional(
        self, migration: MigrationArtifact, runner_version: str,
    ) -> None:
        if runner_version != self._runner_version:
            raise MigrationError("Runner version changed during execution")
        started = time.monotonic()
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                self._set_local_timeouts(cursor)
                cursor.execute(migration.sql)
                duration = int((time.monotonic() - started) * 1000)
                self._record(cursor, migration, duration)

    def apply_idempotent_script(
        self, migration: MigrationArtifact, runner_version: str,
    ) -> None:
        if runner_version != self._runner_version:
            raise MigrationError("Runner version changed during execution")
        guarded_sql, substitutions = re.subn(
            r"(?im)^\s*BEGIN\s*;",
            "BEGIN;\nSET LOCAL lock_timeout = '5s';\n"
            "SET LOCAL statement_timeout = '120s';",
            migration.sql,
            count=1,
        )
        if substitutions != 1:
            raise MigrationError("Idempotent script has no unique BEGIN boundary")
        started = time.monotonic()
        _execute_script(self._connection, guarded_sql)
        duration = int((time.monotonic() - started) * 1000)
        # The SQL script committed its own atomic transaction. If this small
        # ledger transaction fails, a retry safely re-runs the idempotent SQL.
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                self._set_local_timeouts(cursor)
                self._record(cursor, migration, duration)

    def verify(self, sql: str) -> None:
        if not re.search(
            r"(?im)^\s*BEGIN\s+TRANSACTION\s+READ\s+ONLY\s*;", sql,
        ):
            raise MigrationError("Final verification is not read-only")
        _execute_script(self._connection, sql)

    def close(self) -> None:
        self._connection.close()


class PsycopgTenantFactory:
    def __init__(
        self,
        base_url: str,
        runner_version: str,
        test_url: str | None = None,
    ):
        self._base_url = base_url
        self._runner_version = runner_version
        self._test_url = test_url

    @staticmethod
    def database_name(url_value: str) -> str:
        from sqlalchemy.engine import make_url

        name = make_url(url_value).database
        if not name:
            raise MigrationError("Database URL has no database name")
        return name

    def _connect(self, url_value: str, database_name: str):
        import psycopg
        from sqlalchemy.engine import make_url

        url = make_url(url_value).set(database=database_name)
        if url.port == 6543:
            raise MigrationError(
                "Transaction pooler port 6543 is not allowed for advisory locks"
            )
        kwargs = {
            "dbname": url.database,
            "user": url.username,
            "password": url.password,
            "host": url.host,
            "port": url.port,
            "connect_timeout": 15,
            "application_name": "cu31-tenant-migrator",
            "autocommit": True,
        }
        for key in ("sslmode", "sslrootcert"):
            if key in url.query:
                kwargs[key] = url.query[key]
        return psycopg.connect(**kwargs)

    def open(self, target: TenantTarget) -> PsycopgTenantDatabase:
        source = self._test_url if not target.managed else self._base_url
        if not source:
            raise MigrationError("Disposable test database URL is unavailable")
        connection = self._connect(source, target.database_name)
        return PsycopgTenantDatabase(
            connection, target.database_name, self._runner_version,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_active_tenants",
        description="Plan or execute the versioned CU31 tenant migrations.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="Read-only plan.")
    mode.add_argument("--execute", action="store_true", help="Apply the confirmed plan.")
    parser.add_argument("--confirm-plan", help="Exact SHA-256 printed by --plan.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--database", help="One active control-plane tenant only.")
    scope.add_argument(
        "--test-url-env",
        help="Environment variable containing a disposable *_test database URL.",
    )
    return parser


def _runtime(args):
    from app.core.config import DATABASE_URL
    from app.database.session import SessionLocal

    bundle = load_bundle(MIGRATION_ROOT)
    control = SqlAlchemyControlPlane(SessionLocal)
    test_url = None
    if args.test_url_env:
        if not _ENV_NAME.fullmatch(args.test_url_env):
            raise MigrationError("Disposable URL environment variable name is invalid")
        test_url = os.getenv(args.test_url_env)
        if not test_url:
            raise MigrationError("Disposable database URL is unavailable")
        database_name = PsycopgTenantFactory.database_name(test_url)
        if not _TEST_DATABASE.fullmatch(database_name):
            raise MigrationError("Disposable database name must end in _test or _testing")
        if database_name in control.known_database_names():
            raise MigrationError("Disposable database collides with a registered tenant")
        targets = (TenantTarget(
            tenant_database_id=None,
            empresa_codigo="DISPOSABLE-TEST",
            database_name=database_name,
            version_schema=None,
            managed=False,
        ),)
    else:
        targets = control.list_active_targets(args.database)
    factory = PsycopgTenantFactory(
        DATABASE_URL, bundle.runner_version, test_url=test_url,
    )
    return bundle, control, factory, targets


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_CONFIG

    if args.plan and args.confirm_plan:
        print("CU31 migration configuration is invalid", file=sys.stderr)
        return EXIT_CONFIG
    if args.execute and not args.confirm_plan:
        print("CU31 migration refused: --confirm-plan is required", file=sys.stderr)
        return EXIT_REFUSED
    if args.execute and (os.getenv(ALLOW_ENV) or "").strip() != "1":
        print(f"CU31 migration refused: {ALLOW_ENV}=1 is required", file=sys.stderr)
        return EXIT_REFUSED

    try:
        bundle, control, factory, targets = _runtime(args)
        plan = build_plan(targets, bundle, factory)
        if args.plan:
            print(json.dumps(plan.to_public_dict(), sort_keys=True))
            return EXIT_OK
        results = execute_plan(
            plan, args.confirm_plan, bundle, factory, control,
        )
        print(json.dumps({
            "mode": "execute",
            "plan_hash": plan.plan_hash,
            "schema_version": bundle.schema_version,
            "tenants": [
                {
                    "database_name": item.database_name,
                    "applied": list(item.applied),
                    "skipped": list(item.skipped),
                    "version_updated": item.version_updated,
                }
                for item in results
            ],
        }, sort_keys=True))
        return EXIT_OK
    except MigrationError as exc:
        print("CU31 tenant migration failed", file=sys.stderr)
        print(json.dumps({
            "error": type(exc).__name__,
            "message": _sanitize(exc),
        }, sort_keys=True), file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:
        print("CU31 tenant migration failed", file=sys.stderr)
        print(json.dumps({
            "error": type(exc).__name__,
            "message": _sanitize(exc),
        }, sort_keys=True), file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
