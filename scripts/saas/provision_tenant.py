"""Guarded Paso 7D.2 provisioning workflow.

The execute path is deliberately dependency-injected.  It is therefore fully
testable without a database, while production wiring still requires explicit
``--execute`` and ``--confirm tenant_vision_clara``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


EXPECTED_CODE = "VISION-CLARA"
EXPECTED_DATABASE = "tenant_vision_clara"
PENDING = "PENDIENTE"
PROVISIONING = "PROVISIONANDO"
IN_PROGRESS = "EN_PROCESO"
ACTIVE = "ACTIVA"
COMPLETED = "COMPLETADO"
ERROR = "ERROR"
EXCLUDED_SCHEMAS = (
    "auth", "storage", "realtime", "extensions", "saas_control",
    "vault", "graphql", "graphql_public", "supabase_functions",
)
REQUIRED_TOOLS = ("pg_dump", "pg_restore", "psql")
SCHEMA_VERSION = "v1"
RECONCILE_COUNT_TABLES = ("usuario", "paciente", "cita")
DEFAULT_COMMAND_TIMEOUT_SECONDS = 900
SOURCE_TABLES = (
    "accion", "antecedente_clinico", "bitacora", "bloqueo_horario", "cita",
    "consulta_clinica", "control_medico", "detalle_receta", "diagnostico",
    "examen_oftalmologico", "funcion", "historial_clinico", "horario_oftalmologo",
    "indicacion", "modulo", "oftalmologo", "paciente", "receta", "resultado_examen",
    "rol", "rol_funcion", "servicio_oftalmologico", "servicio_realizado",
    "servicios_oftalmologicos", "token_recuperacion", "tratamiento", "usuario",
)
CLEAN_EXPECTED_CODE = "OFTALMO-NORTE"
CLEAN_EXPECTED_DATABASE = "tenant_oftalmo_norte"
CLEAN_ADMIN_EMAIL = "admin@oftalmonorte.demo"
CLEAN_CATALOG_TABLES = ("accion", "modulo", "funcion", "rol", "rol_funcion")
CLEAN_CATALOG_COUNTS = {"accion": 3, "modulo": 6, "funcion": 19, "rol": 4, "rol_funcion": 39}
CLEAN_EMPTY_TABLES = (
    "antecedente_clinico", "bitacora", "bloqueo_horario", "cita", "consulta_clinica",
    "control_medico", "detalle_receta", "diagnostico", "examen_oftalmologico",
    "historial_clinico", "horario_oftalmologo", "indicacion", "oftalmologo", "paciente",
    "receta", "resultado_examen", "servicio_realizado", "servicios_oftalmologicos",
    "token_recuperacion", "tratamiento",
)
PG_BIN_DIRS = (
    Path(r"C:\Program Files\PostgreSQL\18\bin"),
    Path(r"C:\Program Files\PostgreSQL\17\bin"),
    Path(r"C:\Program Files\PostgreSQL\16\bin"),
)
PENDING_BATCH_TARGETS = (
    ("VISUAL-ORIENTAL", "tenant_visual_oriental", "admin@visualoriental.demo"),
    ("INSTITUTO-VISION", "tenant_instituto_vision", "admin@institutovision.demo"),
    ("OFTALMOCARE", "tenant_oftalmocare", "admin@oftalmocare.demo"),
    ("VISTA-SUR", "tenant_vista_sur", "admin@vistasur.demo"),
    ("MEDICO-OCULAR", "tenant_medico_ocular", "admin@medicoocular.demo"),
)
PENDING_BATCH_CODES = tuple(item[0] for item in PENDING_BATCH_TARGETS)
EXCLUDED_BATCH_CODES = frozenset((EXPECTED_CODE, CLEAN_EXPECTED_CODE))
PROHIBITED_DEPENDENCY_SCHEMAS = "('auth','storage','saas_control','realtime','extensions','vault','graphql','graphql_public','supabase_functions')"
_DEPENDENCY_AUDIT_SQL = f"""
SELECT count(*) = 0 FROM (
  SELECT 1 FROM pg_constraint con
  JOIN pg_class src ON src.oid=con.conrelid
  JOIN pg_namespace src_ns ON src_ns.oid=src.relnamespace
  JOIN pg_class ref ON ref.oid=con.confrelid
  JOIN pg_namespace ref_ns ON ref_ns.oid=ref.relnamespace
  WHERE src_ns.nspname='public' AND con.contype='f' AND ref_ns.nspname IN {PROHIBITED_DEPENDENCY_SCHEMAS}
  UNION ALL
  SELECT 1 FROM pg_depend d
  JOIN pg_class src ON src.oid=d.objid
  JOIN pg_namespace src_ns ON src_ns.oid=src.relnamespace
  WHERE src_ns.nspname='public' AND d.deptype IN ('n','a')
    AND pg_describe_object(d.refclassid,d.refobjid,d.refobjsubid) ~* '(auth|storage|saas_control|realtime|extensions|vault|graphql|graphql_public|supabase_functions)\\.'
) blockers;
""".strip()


def _ensure_repository_root_on_sys_path() -> None:
    repository_root = str(Path(__file__).resolve().parents[2])
    if repository_root not in sys.path:
        sys.path.insert(0, repository_root)


class ProvisioningError(RuntimeError):
    """Controlled failure whose message must not contain credentials."""


@dataclass(frozen=True)
class TenantMapping:
    code: str
    database_name: str
    tenant_state: str
    provisioning_state: str
    empresa_id: int | None = None
    tenant_database_id: int | None = None
    provisioning_id: int | None = None
    empresa_state: str | None = None
    subscription_id: int | None = None
    subscription_state: str | None = None
    subscription_start: date | None = None
    subscription_end: date | None = None


@dataclass(frozen=True)
class ToolPaths:
    pg_dump: Path
    pg_restore: Path
    psql: Path


@dataclass(frozen=True)
class Preflight:
    role_createdb: bool = True
    dependencies_ok: bool = True
    source_counts: dict[str, int] | None = None
    source_structure: "SchemaSnapshot | None" = None


@dataclass(frozen=True)
class SchemaSnapshot:
    """Read-only structural fingerprint captured before the dump."""

    columns: tuple[str, ...]
    constraints: tuple[tuple[str, ...], ...]
    indexes: tuple[str, ...]
    object_counts: tuple[str, ...]


@dataclass(frozen=True)
class RecoverySession:
    pid: int
    usename: str | None
    application_name: str | None
    backend_type: str | None
    state: str | None
    xact_start: str | None
    query_start: str | None
    residual_only: bool = False


@dataclass(frozen=True)
class RecoveryAudit:
    target_exists: bool
    sessions: tuple[RecoverySession, ...]
    source: str
    database_name: str
    safe_to_terminate: bool = False
    residual_only: bool = False

    @property
    def session_count(self) -> int:
        return len(self.sessions)


@dataclass(frozen=True)
class Verification:
    structure_ok: bool = True
    counts_ok: bool = True
    schemas_ok: bool = True
    sql_health_ok: bool = True
    columns_ok: bool = True
    constraints_ok: bool = True
    indexes_ok: bool = True
    catalogs_ok: bool = True
    admin_ok: bool = True
    clean_data_ok: bool = True

    @property
    def ok(self) -> bool:
        return all((self.structure_ok, self.counts_ok, self.schemas_ok, self.sql_health_ok,
                    self.columns_ok, self.constraints_ok, self.indexes_ok, self.catalogs_ok,
                    self.admin_ok, self.clean_data_ok))


@dataclass(frozen=True)
class CleanVerification:
    structure_ok: bool = True
    schemas_ok: bool = True
    catalogs_ok: bool = True
    empty_ok: bool = True
    bootstrap_ok: bool = True

    @property
    def ok(self) -> bool:
        return all((self.structure_ok, self.schemas_ok, self.catalogs_ok,
                    self.empty_ok, self.bootstrap_ok))


class ProgressReporter(Protocol):
    def report(self, step: int, label: str, duration_seconds: float | None = None) -> None: ...


class ConsoleProgressReporter:
    def report(self, step: int, label: str, duration_seconds: float | None = None) -> None:
        suffix = "" if duration_seconds is None else f" ({duration_seconds:.2f}s)"
        print(f"[{step}/10] {label}{suffix}")


class ControlPlaneReader(Protocol):
    def read_mapping(self, code: str) -> TenantMapping: ...


class ControlPlaneWriter(Protocol):
    def start(self, mapping: TenantMapping) -> None: ...
    def fail(self, mapping: TenantMapping, message: str) -> None: ...
    def complete(self, mapping: TenantMapping, version: str = "v1") -> None: ...
    def reset_after_recovery(self, mapping: TenantMapping) -> None: ...


class ToolRunner(Protocol):
    def available(self, name: str) -> bool: ...


class ProvisioningBackend(Protocol):
    def preflight(self, mapping: TenantMapping) -> Preflight: ...
    def database_exists(self, database_name: str) -> bool: ...
    def create_database(self, database_name: str) -> None: ...
    def dump_public(self, source_database: str, destination: Path, source_counts: dict[str, int] | None) -> None: ...
    def seed_catalogs(self, source_database: str, target_database: str, directory: Path) -> None: ...
    def bootstrap_admin(self, target_database: str, email: str, password_hash: str) -> None: ...
    def verify_clean(self, target_database: str, source_structure: SchemaSnapshot,
                     admin_email: str) -> CleanVerification: ...
    def restore_public(self, target_database: str, dump_path: Path) -> None: ...
    def verify(self, source_counts: dict[str, int] | None, target_database: str,
               source_structure: SchemaSnapshot | None = None) -> Verification: ...
    def health_check(self, mapping: TenantMapping) -> bool: ...
    def cleanup_dump(self, dump_path: Path) -> None: ...
    def recovery_audit(self, database_name: str) -> RecoveryAudit: ...
    def dispose_tenant_engine(self, tenant_database_id: int) -> None: ...
    def terminate_residual_sessions(
        self, database_name: str, sessions: tuple[RecoverySession, ...],
    ) -> int: ...
    def drop_database(self, database_name: str) -> None: ...
    def count_tables(self, database_name: str, tables: tuple[str, ...]) -> dict[str, int]: ...
    def runtime_health_check(self, mapping: TenantMapping) -> bool: ...
    def verify_existing(self, source_counts: dict[str, int], target_database: str,
                        source_structure: SchemaSnapshot) -> Verification: ...
    def reconciliation_source_snapshot(self, mapping: TenantMapping) -> Preflight: ...
    def snapshot_target(self, database_name: str) -> SchemaSnapshot: ...


@dataclass(frozen=True)
class PostgresConnection:
    host: str
    port: int
    user: str
    password: str
    sslmode: str = "require"


class CommandRunner(Protocol):
    def run(self, argv: list[str], env: dict[str, str], timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
            input_text: str | None = None) -> str: ...


class SubprocessRunner:
    def run(self, argv: list[str], env: dict[str, str], timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
            input_text: str | None = None) -> str:
        try:
            completed = subprocess.run(
                argv, env=env, check=True, capture_output=True, text=True,
                timeout=timeout_seconds, input=input_text,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProvisioningError("PostgreSQL client command timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr or exc.stdout or "PostgreSQL client command failed"
            raise ProvisioningError(_sanitized_error(detail)) from exc
        return completed.stdout


class SubprocessPostgresBackend:
    """PostgreSQL client implementation; secrets only enter child env."""

    def __init__(self, tools: ToolPaths, connection: PostgresConnection, runner: CommandRunner | None = None,
                 command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
                 engine_disposer: Callable[[int], None] | None = None):
        if command_timeout_seconds <= 0:
            raise ValueError("command_timeout_seconds must be positive")
        self.tools = tools
        self.connection = connection
        self.runner = runner or SubprocessRunner()
        self.command_timeout_seconds = command_timeout_seconds
        self.engine_disposer = engine_disposer

    def _run(self, argv: list[str], input_text: str | None = None) -> str:
        try:
            return self.runner.run(argv, self._env(), self.command_timeout_seconds, input_text)
        except TypeError as exc:
            # Preserve compatibility with existing injected two-argument fakes.
            if "positional" not in str(exc) and "argument" not in str(exc):
                raise
            try:
                return self.runner.run(argv, self._env(), self.command_timeout_seconds)
            except TypeError:
                return self.runner.run(argv, self._env())

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PGPASSWORD"] = self.connection.password
        env["PGSSLMODE"] = self.connection.sslmode
        return env

    def _common(self, database: str) -> list[str]:
        validate_database_identifier(database)
        return ["-h", self.connection.host, "-p", str(self.connection.port),
                "-U", self.connection.user, "-d", database]

    def count_tables(self, database_name: str, tables: tuple[str, ...]) -> dict[str, int]:
        validate_database_identifier(database_name)
        return {
            table: int(self._psql(database_name, f'SELECT count(*) FROM public."{validate_table_identifier(table)}";').strip() or "-1")
            for table in tables
        }

    def _psql(self, database: str, sql: str) -> str:
        return self._run([str(self.tools.psql), *self._common(database), "-tAc", sql])

    def preflight(self, mapping: TenantMapping) -> Preflight:
        role = self._psql("postgres", "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user;").strip()
        dependency = self._psql("postgres", _DEPENDENCY_AUDIT_SQL).strip()
        counts = {
            table: int(self._psql("postgres", f'SELECT count(*) FROM public."{table}";').strip() or "-1")
            for table in SOURCE_TABLES
        }
        return Preflight(
            role_createdb=role == "t", dependencies_ok=dependency == "t",
            source_counts=counts, source_structure=self._schema_snapshot("postgres"),
        )

    def reconciliation_source_snapshot(self, mapping: TenantMapping) -> Preflight:
        role = self._psql("postgres", "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user;").strip()
        dependency = self._psql("postgres", _DEPENDENCY_AUDIT_SQL).strip()
        return Preflight(
            role_createdb=role == "t", dependencies_ok=dependency == "t",
            source_counts=self.count_tables("postgres", SOURCE_TABLES),
            source_structure=self._schema_snapshot("postgres"),
        )

    def snapshot_target(self, database_name: str) -> SchemaSnapshot:
        return self._schema_snapshot(database_name)

    def database_exists(self, database_name: str) -> bool:
        validate_database_identifier(database_name)
        return self._psql("postgres", "SELECT 1 FROM pg_database WHERE datname = "
                           + _sql_literal(database_name) + ";").strip() == "1"

    def create_database(self, database_name: str) -> None:
        self._psql("postgres", f'CREATE DATABASE "{database_name}" TEMPLATE template0;')

    def dump_public(self, source_database: str, destination: Path, source_counts: dict[str, int] | None,
                    schema_only: bool = False) -> None:
        args = [
            str(self.tools.pg_dump), "-Fc", "--schema=public",
            *( ["--schema-only"] if schema_only else [] ), "--no-owner",
            "--no-privileges", *self._common(source_database), "-f", str(destination),
        ]
        self._run(args)

    def seed_catalogs(self, source_database: str, target_database: str, directory: Path) -> None:
        dump_path = directory / "tenant_clean_catalogs.dump"
        try:
            self._run([
                str(self.tools.pg_dump), "-Fc", "--data-only", "--no-owner", "--no-privileges",
                *[f"--table=public.{table}" for table in CLEAN_CATALOG_TABLES],
                *self._common(source_database), "-f", str(dump_path),
            ])
            self.restore_public(target_database, dump_path)
        finally:
            dump_path.unlink(missing_ok=True)

    def bootstrap_admin(self, target_database: str, email: str, password_hash: str) -> None:
        # The hash is supplied through stdin, never through argv, logs, or the Control Plane.
        escaped_email = email.replace("'", "''")
        escaped_hash = password_hash.replace("'", "''")
        sql = f"""
DO $$
DECLARE admin_role_id bigint;
BEGIN
  SELECT id INTO admin_role_id FROM public.rol
   WHERE lower(trim(nombre)) = lower('Administrador') AND estado IS TRUE;
  IF NOT FOUND OR (SELECT count(*) FROM public.rol
       WHERE lower(trim(nombre)) = lower('Administrador') AND estado IS TRUE) <> 1 THEN
    RAISE EXCEPTION 'Expected exactly one active Administrador role';
  END IF;
  IF (SELECT count(*) FROM public.usuario) <> 0 THEN
    RAISE EXCEPTION 'Bootstrap requires an empty usuario table';
  END IF;
  INSERT INTO public.usuario (correo, password_hash, estado, fecha_creacion, rol_id)
  VALUES ('{escaped_email}', '{escaped_hash}', TRUE, now(), admin_role_id);
END $$;
""".strip()
        self._run([str(self.tools.psql), *self._common(target_database), "-v", "ON_ERROR_STOP=1", "-f", "-"], sql)

    def verify_clean(self, target_database: str, source_structure: SchemaSnapshot,
                     admin_email: str) -> CleanVerification:
        validate_database_identifier(target_database)
        target = self._schema_snapshot(target_database)
        prohibited = self._psql(target_database,
            "SELECT count(*) FROM pg_namespace WHERE nspname IN "
            "('saas_control','auth','storage','realtime','extensions','vault','graphql','graphql_public','supabase_functions');").strip()
        catalog_counts = self.count_tables(target_database, CLEAN_CATALOG_TABLES)
        empty_counts = self.count_tables(target_database, CLEAN_EMPTY_TABLES)
        admin_count = int(self._psql(target_database, """SELECT count(*)
            FROM public.usuario u JOIN public.rol r ON r.id = u.rol_id
            WHERE u.correo = {email} AND lower(trim(r.nombre)) = lower('Administrador')
              AND u.estado IS TRUE AND nullif(trim(u.password_hash), '') IS NOT NULL;""".format(
                  email=_sql_literal(admin_email))).strip() or "-1")
        total_users = int(self._psql(target_database, "SELECT count(*) FROM public.usuario;").strip() or "-1")
        return CleanVerification(
            structure_ok=target.object_counts == source_structure.object_counts
                and target.columns == source_structure.columns and target.constraints == source_structure.constraints
                and target.indexes == source_structure.indexes,
            schemas_ok=prohibited == "0",
            catalogs_ok=catalog_counts == CLEAN_CATALOG_COUNTS,
            empty_ok=all(value == 0 for value in empty_counts.values()),
            bootstrap_ok=total_users == 1 and admin_count == 1,
        )

    def restore_public(self, target_database: str, dump_path: Path) -> None:
        self._run([
            str(self.tools.pg_restore), "--dbname", target_database, "--schema=public",
            "--no-owner", "--no-privileges", "--exit-on-error",
            "-h", self.connection.host, "-p", str(self.connection.port),
            "-U", self.connection.user, str(dump_path),
        ])

    def _schema_snapshot(self, database: str) -> SchemaSnapshot:
        column_rows = self._psql(
            database,
            "SELECT table_name || E'\\t' || column_name || E'\\t' || data_type || E'\\t' || "
            "coalesce(udt_schema, '') || E'\\t' || coalesce(udt_name, '') || E'\\t' || "
            "is_nullable || E'\\t' || ordinal_position::text FROM information_schema.columns "
            "WHERE table_schema='public' ORDER BY table_name, ordinal_position;",
        ).splitlines()
        raw_constraints = self._psql(
            database,
            "SELECT con.contype::text || E'\\t' || src_ns.nspname || E'\\t' || src.relname || E'\\t' || "
            "coalesce((SELECT string_agg(a.attname, ',' ORDER BY x.ord) FROM unnest(con.conkey) "
            "WITH ORDINALITY AS x(attnum, ord) JOIN pg_attribute a ON a.attrelid=src.oid "
            "AND a.attnum=x.attnum), '') || E'\\t' || coalesce(ref_ns.nspname, '') || E'\\t' || "
            "coalesce(ref.relname, '') || E'\\t' || coalesce((SELECT string_agg(a.attname, ',' ORDER BY x.ord) "
            "FROM unnest(con.confkey) WITH ORDINALITY AS x(attnum, ord) JOIN pg_attribute a "
            "ON a.attrelid=ref.oid AND a.attnum=x.attnum), '') || E'\\t' || "
            "CASE con.confmatchtype WHEN 'f' THEN 'FULL' WHEN 'p' THEN 'PARTIAL' ELSE 'SIMPLE' END || E'\\t' || "
            "CASE con.confupdtype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE' "
            "WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' ELSE '' END || E'\\t' || "
            "CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT' WHEN 'c' THEN 'CASCADE' "
            "WHEN 'n' THEN 'SET NULL' WHEN 'd' THEN 'SET DEFAULT' ELSE '' END || E'\\t' || "
            "con.condeferrable::text || E'\\t' || con.condeferred::text || E'\\t' || "
            "coalesce(pg_get_constraintdef(con.oid, true), '') FROM pg_constraint con "
            "JOIN pg_class src ON src.oid=con.conrelid JOIN pg_namespace src_ns ON src_ns.oid=src.relnamespace "
            "LEFT JOIN pg_class ref ON ref.oid=con.confrelid LEFT JOIN pg_namespace ref_ns ON ref_ns.oid=ref.relnamespace "
            "WHERE src_ns.nspname='public' AND con.contype IN ('p','u','f','c') "
            "ORDER BY con.contype, src.relname, con.conkey, con.confrelid, con.oid;",
        ).splitlines()
        constraints = []
        for line in raw_constraints:
            fields = line.split("\t")
            if len(fields) != 13:
                raise ProvisioningError("Constraint snapshot returned an unclassifiable row")
            (
                kind, schema, table, constraint_columns, ref_schema, ref_table,
                constraint_column_names, match, on_update, on_delete, deferrable,
                deferred, check_def,
            ) = fields
            if kind in {"p", "u"}:
                constraints.append((kind, schema, table, constraint_columns))
            elif kind == "f":
                constraints.append((kind, schema, table, constraint_columns, ref_schema, ref_table,
                                    constraint_column_names, match, on_update, on_delete, deferrable, deferred))
            else:
                constraints.append((kind, schema, table, _normalize_check_definition(check_def)))
        indexes = self._psql(
            database,
            "SELECT tablename || E'\\t' || indexname || E'\\t' || indexdef FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY tablename, indexname;",
        ).splitlines()
        object_counts = self._psql(
            database,
            "SELECT 'tables=' || count(*) FILTER (WHERE c.relkind='r') || E'\\n' || "
            "'sequences=' || count(*) FILTER (WHERE c.relkind='S') || E'\\n' || "
            "'views=' || count(*) FILTER (WHERE c.relkind='v') || E'\\n' || "
            "'materialized_views=' || count(*) FILTER (WHERE c.relkind='m') "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public';",
        ).splitlines()
        return SchemaSnapshot(tuple(column_rows), tuple(constraints), tuple(indexes), tuple(object_counts))

    def verify(
        self, source_counts: dict[str, int] | None, target_database: str,
        source_structure: SchemaSnapshot | None = None,
    ) -> Verification:
        if source_counts is None or source_structure is None:
            return Verification(counts_ok=False, structure_ok=False, columns_ok=False,
                                constraints_ok=False, indexes_ok=False)
        tables = int(self._psql(
            target_database,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='r';",
        ).strip() or "-1")
        sequences = int(self._psql(
            target_database,
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='S';",
        ).strip() or "-1")
        prohibited = self._psql(
            target_database,
            "SELECT count(*) FROM pg_namespace WHERE nspname IN "
            "('saas_control','auth','storage','realtime','vault','graphql','graphql_public','supabase_functions');",
        ).strip()
        counts_ok = (
            set(source_counts) == set(SOURCE_TABLES)
            and tables == len(SOURCE_TABLES)
            and sequences == len(SOURCE_TABLES)
        )
        for table, expected in source_counts.items():
            actual = self.count_tables(target_database, (table,))[table]
            counts_ok = counts_ok and actual == expected
        target_structure = self._schema_snapshot(target_database)
        return Verification(
            structure_ok=target_structure.object_counts == source_structure.object_counts,
            counts_ok=counts_ok,
            schemas_ok=prohibited == "0",
            columns_ok=target_structure.columns == source_structure.columns,
            constraints_ok=target_structure.constraints == source_structure.constraints,
            indexes_ok=target_structure.indexes == source_structure.indexes,
        )

    def verify_existing(
        self, source_counts: dict[str, int], target_database: str,
        source_structure: SchemaSnapshot,
    ) -> Verification:
        clean = self.verify_clean(target_database, source_structure, CLEAN_ADMIN_EMAIL)
        target_structure = self._schema_snapshot(target_database)
        expected_tables = set(SOURCE_TABLES)
        source_tables_ok = set(source_counts) == expected_tables
        return Verification(
            structure_ok=clean.structure_ok,
            counts_ok=source_tables_ok,
            schemas_ok=clean.schemas_ok,
            columns_ok=target_structure.columns == source_structure.columns,
            constraints_ok=target_structure.constraints == source_structure.constraints,
            indexes_ok=target_structure.indexes == source_structure.indexes,
            catalogs_ok=clean.catalogs_ok,
            admin_ok=clean.bootstrap_ok,
            clean_data_ok=clean.empty_ok,
        )

    def health_check(self, mapping: TenantMapping) -> bool:
        return self._psql(mapping.database_name, "SELECT 1;").strip() == "1"

    def runtime_health_check(self, mapping: TenantMapping) -> bool:
        return self.health_check(mapping)

    def cleanup_dump(self, dump_path: Path) -> None:
        dump_path.unlink(missing_ok=True)

    def recovery_audit(self, database_name: str) -> RecoveryAudit:
        exists = self.database_exists(database_name)
        output = self._psql(
            "postgres",
            "SELECT count(*) OVER (), pid, coalesce(usename, '<NULL>'), "
            "coalesce(application_name, '<NULL>'), coalesce(backend_type, '<NULL>'), "
            "coalesce(state, '<NULL>'), coalesce(xact_start::text, '<NULL>'), "
            "coalesce(query_start::text, '<NULL>') FROM pg_stat_activity WHERE datname = "
            + _sql_literal(database_name) + " AND pid <> pg_backend_pid();",
        )
        sessions_list = []
        for line in output.splitlines():
            fields = line.split("|", 7)
            if len(fields) != 8:
                raise ProvisioningError("Recovery audit returned an unclassifiable session")
            try:
                sessions_list.append(RecoverySession(
                    pid=int(fields[1]), usename=_audit_field(fields[2]),
                    application_name=_audit_field(fields[3]),
                    backend_type=_audit_field(fields[4]), state=_audit_field(fields[5]),
                    xact_start=_audit_field(fields[6]), query_start=_audit_field(fields[7]),
                ))
            except ValueError as exc:
                raise ProvisioningError("Recovery audit returned an unclassifiable session") from exc
        sessions = tuple(sessions_list)
        residual_only = len(sessions) == 1 and _is_supavisor_residual(sessions[0])
        if residual_only:
            sessions = (RecoverySession(
                sessions[0].pid, sessions[0].usename, sessions[0].application_name,
                sessions[0].backend_type, sessions[0].state, sessions[0].xact_start,
                sessions[0].query_start, True,
            ),)
        return RecoveryAudit(
            exists, sessions, "Control Plane mapping", database_name,
            residual_only=residual_only,
        )

    def dispose_tenant_engine(self, tenant_database_id: int) -> None:
        if self.engine_disposer is not None:
            self.engine_disposer(tenant_database_id)

    def terminate_residual_sessions(
        self, database_name: str, sessions: tuple[RecoverySession, ...],
    ) -> int:
        if not sessions or not all(session.residual_only for session in sessions):
            raise ProvisioningError("Only explicitly residual provisioning sessions may be terminated")
        pids = ",".join(str(session.pid) for session in sessions)
        output = self._psql(
            "postgres",
            "SELECT CASE WHEN pg_terminate_backend(pid) THEN 1 ELSE 0 END "
            "FROM pg_stat_activity WHERE datname = " + _sql_literal(database_name)
            + " AND pid IN (" + pids + ") AND pid <> pg_backend_pid();",
        )
        return sum(line.strip() == "1" for line in output.splitlines())

    def drop_database(self, database_name: str) -> None:
        validate_database_identifier(database_name)
        if database_name != EXPECTED_DATABASE:
            raise ProvisioningError("DROP target is not the expected tenant")
        if self._psql("postgres", "SELECT current_database() <> " + _sql_literal(database_name) + ";").strip() != "t":
            raise ProvisioningError("DROP must run from a database other than the target")
        if self._psql(
            "postgres",
            "SELECT datistemplate FROM pg_database WHERE datname = " + _sql_literal(database_name) + ";",
        ).strip() != "f":
            raise ProvisioningError("DROP target is a template or is unavailable")
        # psql starts a fresh autocommit command against postgres, outside any
        # transaction.  FORCE is the only mechanism allowed to clear sessions.
        self._run([
            str(self.tools.psql), *self._common("postgres"), "-v", "ON_ERROR_STOP=1",
            "-c", f'DROP DATABASE "{database_name}" WITH (FORCE);',
        ])


class PathToolRunner:
    def __init__(self, override: str | os.PathLike[str] | None = None):
        self.override = Path(override) if override else None

    def path(self, name: str) -> Path | None:
        if self.override is not None:
            candidate = self.override / f"{name}.exe"
            return candidate if candidate.is_file() else None
        found = shutil.which(name)
        if found:
            return Path(found)
        for directory in PG_BIN_DIRS:
            candidate = directory / f"{name}.exe"
            if candidate.is_file():
                return candidate
        return None

    def available(self, name: str) -> bool:
        return self.path(name) is not None

    def resolve(self) -> ToolPaths:
        missing = [name for name in REQUIRED_TOOLS if self.path(name) is None]
        if missing:
            raise ProvisioningError("Missing PostgreSQL tools: " + ", ".join(missing))
        return ToolPaths(*(self.path(name) for name in REQUIRED_TOOLS))  # type: ignore[arg-type]

    def resolve_psql(self) -> ToolPaths:
        psql = self.path("psql")
        if psql is None:
            raise ProvisioningError("Missing PostgreSQL tools: psql")
        return ToolPaths(Path("unused"), Path("unused"), psql)


class SqlAlchemyControlPlaneReader:
    def __init__(self, session_factory: Callable[[], object]):
        self._session_factory = session_factory

    def read_mapping(self, code: str) -> TenantMapping:
        from sqlalchemy import text

        session = self._session_factory()
        try:
            row = session.execute(
                text("""SELECT e.id AS empresa_id, e.codigo AS codigo,
                              e.estado AS empresa_state,
                              td.id AS tenant_database_id,
                              td.database_name AS database_name,
                              td.estado AS tenant_state,
                              pt.id AS provisioning_id,
                              pt.estado AS provisioning_state,
                              s.id AS subscription_id,
                              s.estado AS subscription_state,
                              s.fecha_inicio AS subscription_start,
                              s.fecha_fin AS subscription_end
                       FROM saas_control.empresa AS e
                       JOIN saas_control.tenant_database AS td ON td.empresa_id = e.id
                       JOIN saas_control.provisionamiento_tenant AS pt
                         ON pt.tenant_database_id = td.id AND pt.empresa_id = e.id
                       LEFT JOIN saas_control.suscripcion AS s ON s.empresa_id = e.id
                       WHERE e.codigo = :code"""),
                {"code": code},
            ).mappings().first()
            if row is None:
                raise ProvisioningError("Control Plane mapping was not found")
            return TenantMapping(
                code=row["codigo"], database_name=row["database_name"],
                tenant_state=row["tenant_state"], provisioning_state=row["provisioning_state"],
                empresa_id=row["empresa_id"], tenant_database_id=row["tenant_database_id"],
                provisioning_id=row["provisioning_id"],
                empresa_state=row.get("empresa_state"),
                subscription_id=row.get("subscription_id"),
                subscription_state=row.get("subscription_state"),
                subscription_start=row.get("subscription_start"),
                subscription_end=row.get("subscription_end"),
            )
        finally:
            session.close()


class SqlAlchemyControlPlaneWriter:
    def __init__(self, session_factory: Callable[[], object]):
        self._session_factory = session_factory

    def _execute(self, mapping: TenantMapping, sql: str, params: dict) -> None:
        from sqlalchemy import text

        if mapping.tenant_database_id is None or mapping.provisioning_id is None:
            raise ProvisioningError("Control Plane identifiers are unavailable")
        session = self._session_factory()
        try:
            session.execute(text(sql), params)
            session.commit()
        finally:
            session.close()

    def start(self, mapping: TenantMapping) -> None:
        self._execute(mapping, """UPDATE saas_control.tenant_database
            SET estado = 'PROVISIONANDO' WHERE id = :tenant_id AND estado = 'PENDIENTE'""",
            {"tenant_id": mapping.tenant_database_id})
        self._execute(mapping, """UPDATE saas_control.provisionamiento_tenant
            SET estado = 'EN_PROCESO', intentos = intentos + 1,
                fecha_inicio = now(), paso_actual = 'PROVISIONANDO'
            WHERE id = :provisioning_id AND estado = 'PENDIENTE'""",
            {"provisioning_id": mapping.provisioning_id})

    def fail(self, mapping: TenantMapping, message: str) -> None:
        safe = message.replace("\n", " ")[:500]
        self._execute(mapping, "UPDATE saas_control.tenant_database SET estado = 'ERROR' WHERE id = :id",
                      {"id": mapping.tenant_database_id})
        self._execute(mapping, """UPDATE saas_control.provisionamiento_tenant
            SET estado = 'ERROR', fecha_fin = now(), mensaje_error = :message
            WHERE id = :id""", {"id": mapping.provisioning_id, "message": safe})

    def complete(self, mapping: TenantMapping, version: str = "v1") -> None:
        self._execute(mapping, """UPDATE saas_control.tenant_database
            SET estado = 'ACTIVA', version_schema = :version,
                fecha_provisionamiento = now(), ultima_verificacion = now()
            WHERE id = :id""", {"id": mapping.tenant_database_id, "version": version})
        self._execute(mapping, """UPDATE saas_control.provisionamiento_tenant
            SET estado = 'COMPLETADO', paso_actual = 'COMPLETADO',
                fecha_fin = now(), mensaje_error = NULL
            WHERE id = :id""", {"id": mapping.provisioning_id})

    def reset_after_recovery(self, mapping: TenantMapping) -> None:
        self._execute(mapping, """UPDATE saas_control.tenant_database
            SET estado = 'PENDIENTE', fecha_provisionamiento = NULL,
                ultima_verificacion = NULL
            WHERE id = :id AND estado = 'ERROR'""", {"id": mapping.tenant_database_id})
        self._execute(mapping, """UPDATE saas_control.provisionamiento_tenant
            SET estado = 'PENDIENTE', paso_actual = 'RECUPERACION_COMPLETADA',
                fecha_inicio = NULL, fecha_fin = NULL, mensaje_error = NULL
            WHERE id = :id AND estado = 'ERROR'""", {"id": mapping.provisioning_id})


def validate_mapping(mapping: TenantMapping, requested_database: str | None = None) -> None:
    if mapping.code != EXPECTED_CODE:
        raise ProvisioningError("Control Plane mapping is not VISION-CLARA")
    if mapping.database_name != EXPECTED_DATABASE:
        raise ProvisioningError("Control Plane database_name is not the expected tenant")
    if requested_database is not None and requested_database != mapping.database_name:
        raise ProvisioningError("CLI database name does not match the Control Plane mapping")
    if mapping.tenant_state != PENDING or mapping.provisioning_state != PENDING:
        raise ProvisioningError("Only PENDIENTE tenant and provisioning states are allowed")


def _normalize_check_definition(definition: str) -> str:
    """Canonicalize only unbounded varchar/text cast spelling.

    This intentionally does not remove casts, fold expressions, or rewrite
    operators/literals.  Thus an equivalence that cannot be proven by this
    narrow, semantics-preserving alias normalization remains a mismatch.
    """
    # Keep bounded varchar casts intact: unlike unbounded varchar, varchar(n)
    # is not an interchangeable spelling of text.
    normalized = re.sub(
        r"\b(?:character\s+varying|varchar)\s*\[\]",
        "text[]", definition, flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\bcharacter\s+varying\b(?!\s*\()", "text", normalized, flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\bvarchar\b(?!\s*\()", "text", normalized, flags=re.IGNORECASE)

    # PostgreSQL can emit redundant identical scalar casts.  Collapse only a
    # contiguous run of text casts; mixed casts and bounded varchar casts are
    # intentionally left untouched because their equivalence is not proven.
    normalized = re.sub(
        r"(?:\s*::\s*text){2,}", "::text", normalized, flags=re.IGNORECASE,
    )

    # PostgreSQL may print the same text array as ARRAY[...]::text[] or as an
    # array already typed by text elements.  Remove only casts proven
    # redundant by this narrow check; operators, literals and expressions are
    # otherwise left untouched.
    while True:
        changed = False
        for start, end, body in reversed(_array_expressions(normalized)):
            if not _array_elements_are_text(body):
                continue
            tail = normalized[end:]
            match = re.match(r"(?:\s*::\s*text\s*\[\])+(?=\s*(?:\)|$))", tail, re.IGNORECASE)
            if match:
                normalized = normalized[:end] + tail[match.end():]
                changed = True
                continue
            if end < len(normalized) and normalized[end] == ")":
                match = re.match(r"\s*::\s*text\s*\[\]", normalized[end + 1:], re.IGNORECASE)
                if match:
                    cast_start = end + 1
                    normalized = normalized[:cast_start] + normalized[cast_start + match.end():]
                    changed = True
        for start, end, body in reversed(_array_expressions(normalized)):
            if _array_elements_are_text(body):
                left = start - 1
                right = end
                if (
                    left > 0 and right + 1 < len(normalized)
                    and normalized[left] == "(" and normalized[right] == ")"
                    and normalized[left - 1] == "(" and normalized[right + 1] == ")"
                ):
                    normalized = normalized[:left] + normalized[start:right] + normalized[right + 1:]
                    changed = True
        if not changed:
            return normalized


def _array_expressions(value: str) -> list[tuple[int, int, str]]:
    """Return ARRAY[...] spans without interpreting arbitrary SQL."""
    expressions = []
    cursor = 0
    while True:
        match = re.search(r"\bARRAY\s*\[", value[cursor:], re.IGNORECASE)
        if match is None:
            return expressions
        start = cursor + match.start()
        opening = cursor + match.end() - 1
        depth = 1
        quote = False
        index = opening + 1
        while index < len(value) and depth:
            char = value[index]
            if char == "'":
                if quote and index + 1 < len(value) and value[index + 1] == "'":
                    index += 1
                else:
                    quote = not quote
            elif not quote:
                if char == "[":
                    depth += 1
                elif char == "]":
                    depth -= 1
            index += 1
        if depth:
            return expressions
        expressions.append((start, index, value[opening + 1:index - 1]))
        cursor = index


def _array_elements_are_text(body: str) -> bool:
    elements = []
    start = 0
    depth = 0
    quote = False
    for index, char in enumerate(body):
        if char == "'":
            quote = not quote
        elif not quote:
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == "," and depth == 0:
                elements.append(body[start:index])
                start = index + 1
    elements.append(body[start:])
    return bool(elements) and all(re.search(r"::\s*text\s*$", item.strip(), re.IGNORECASE) for item in elements)


def validate_database_identifier(database_name: str) -> None:
    if not isinstance(database_name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database_name):
        raise ProvisioningError("Control Plane database name is not a safe identifier")


def validate_table_identifier(table_name: str) -> str:
    if not isinstance(table_name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table_name):
        raise ProvisioningError("Control Plane table name is not a safe identifier")
    return table_name


def _sql_literal(value: str) -> str:
    if not isinstance(value, str):
        raise ProvisioningError("SQL literal must be text")
    return "'" + value.replace("'", "''") + "'"


def _audit_field(value: str) -> str | None:
    value = value.strip()
    return None if value in {"", "<NULL>"} else value


def _is_supavisor_residual(session: RecoverySession) -> bool:
    return (
        session.application_name is not None
        and session.application_name.casefold() == "supavisor"
        and session.backend_type == "client backend"
        and session.state == "idle"
        and session.xact_start is None
    )


def _recovery_session_report(audit: RecoveryAudit) -> str:
    details = []
    for session in audit.sessions:
        details.append(
            "pid={pid} usename={usename} application_name={application} "
            "state={state} backend_type={backend} xact_start={xact} "
            "query_start={query} classification={classification}".format(
                pid=session.pid,
                usename=session.usename or "<NULL>",
                application=session.application_name or "<NULL>",
                state=session.state or "<NULL>",
                backend=session.backend_type or "<NULL>",
                xact=session.xact_start or "<NULL>",
                query=session.query_start or "<NULL>",
                classification="residual_supavisor_idle"
                if session.residual_only else "blocked",
            )
        )
    return "count_total={count}; {details}".format(
        count=audit.session_count,
        details="; ".join(details) if details else "no target sessions",
    )


def _sanitized_error(message: str) -> str:
    """Keep operational errors useful without persisting credentials or URLs."""
    sanitized = re.sub(r"(?i)(postgres(?:ql)?://[^:]+:)[^@\s]+@", r"\1[REDACTED]@", message)
    sanitized = re.sub(r"(?i)(password|pwd|pgpassword)=([^&\s]+)", r"\1=[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)DATABASE_URL\s*=\s*[^\s]+", "DATABASE_URL=[REDACTED]", sanitized)
    sanitized = re.sub(r"(?i)postgres(?:ql)?://\S+", "[CONNECTION_STRING_REDACTED]", sanitized)
    return sanitized.replace("\n", " ")[:500]


def dry_run(reader: ControlPlaneReader, tools: ToolRunner, requested_database: str | None = None) -> str:
    mapping = reader.read_mapping(EXPECTED_CODE)
    validate_mapping(mapping, requested_database)
    missing = [name for name in REQUIRED_TOOLS if not tools.available(name)]
    tool_status = "available" if not missing else "missing: " + ", ".join(missing)
    return "\n".join((
        "DRY-RUN only; no database or Control Plane state change was attempted.",
        f"Control Plane mapping validated for {mapping.code}.",
        f"Planned database: {mapping.database_name}.",
        f"tenant_database estado: {mapping.tenant_state}.",
        f"provisionamiento estado: {mapping.provisioning_state}.",
        f"PostgreSQL client tools: {tool_status}.",
        "Future migration scope: public schema only.",
        "Excluded schemas: " + ", ".join(EXCLUDED_SCHEMAS) + ".",
    ))


def execute_provision(
    reader: ControlPlaneReader,
    writer: ControlPlaneWriter,
    backend: ProvisioningBackend,
    tools: ToolRunner,
    confirm: str,
    requested_database: str | None = None,
    dump_directory: Path | None = None,
    reporter: ProgressReporter | Callable[[int, str], None] | None = None,
) -> str:
    progress = _progress_callback(reporter)
    mapping = reader.read_mapping(EXPECTED_CODE)
    validate_mapping(mapping, requested_database)
    if confirm != EXPECTED_DATABASE:
        raise ProvisioningError("Strong confirmation does not match the target database")
    if not all(tools.available(name) for name in REQUIRED_TOOLS):
        raise ProvisioningError("Required PostgreSQL client tools are unavailable")

    progress(1, "Preflight")
    preflight = backend.preflight(mapping)
    if not preflight.role_createdb or not preflight.dependencies_ok:
        raise ProvisioningError("Read-only prechecks failed")
    if backend.database_exists(mapping.database_name):
        raise ProvisioningError("Target database already exists")

    dump_path: Path | None = None
    writer.start(mapping)
    progress(2, "Snapshot fuente")
    try:
        try:
            progress(3, "Estado PROVISIONANDO")
            progress(4, "Creando database")
            backend.create_database(mapping.database_name)
            directory = dump_directory or Path(tempfile.gettempdir())
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="tenant_vision_clara_", suffix=".dump", dir=directory, delete=False) as handle:
                dump_path = Path(handle.name)
            progress(5, "Generando dump")
            backend.dump_public("postgres", dump_path, preflight.source_counts)
            progress(6, "Restaurando")
            backend.restore_public(mapping.database_name, dump_path)
            progress(7, "Verificando estructura")
            verification = backend.verify(
                preflight.source_counts, mapping.database_name, preflight.source_structure,
            )
            if not verification.ok:
                raise ProvisioningError("Tenant verification failed")
            progress(8, "Verificando conteos")
            if not verification.counts_ok:
                raise ProvisioningError("Tenant count verification failed")
            progress(9, "Health check")
            if not backend.health_check(mapping):
                raise ProvisioningError("Tenant health check failed")
            progress(10, "Activando tenant")
        finally:
            if dump_path is not None:
                backend.cleanup_dump(dump_path)
        writer.complete(mapping, version=SCHEMA_VERSION)
        return "Provisioning completed: tenant_vision_clara is ACTIVA/COMPLETADO."
    except Exception as exc:
        writer.fail(mapping, _sanitized_error(str(exc)))
        raise ProvisioningError("Provisioning failed; Control Plane marked ERROR") from exc


def _validate_bootstrap_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        raise ProvisioningError("Bootstrap admin email is invalid")
    return normalized


def validate_clean_mapping(
    mapping: TenantMapping,
    requested_database: str | None = None,
    expected_code: str = CLEAN_EXPECTED_CODE,
    expected_database: str | None = None,
) -> None:
    expected_database = expected_database or mapping.database_name
    if mapping.code != expected_code:
        raise ProvisioningError("Control Plane mapping is not the requested clean tenant")
    validate_database_identifier(mapping.database_name)
    if mapping.database_name != expected_database:
        raise ProvisioningError("Control Plane database_name is not the requested tenant")
    if requested_database is not None and requested_database != mapping.database_name:
        raise ProvisioningError("confirmation does not match the Control Plane database")
    if mapping.tenant_state != PENDING or mapping.provisioning_state != PENDING:
        raise ProvisioningError("Only PENDIENTE tenant and provisioning states are allowed")


def _validate_batch_mapping(mapping: TenantMapping, expected_code: str, expected_database: str) -> None:
    if mapping.code in EXCLUDED_BATCH_CODES:
        raise ProvisioningError(f"Batch target is excluded: {mapping.code}")
    validate_clean_mapping(mapping, expected_database, expected_code, expected_database)
    if mapping.empresa_state != ACTIVE:
        raise ProvisioningError(f"Empresa {mapping.code} is not ACTIVA")
    if mapping.subscription_state != ACTIVE:
        raise ProvisioningError(f"Subscription for {mapping.code} is not ACTIVA")
    today = date.today()
    if mapping.subscription_start is None or mapping.subscription_end is None or not (
        mapping.subscription_start <= today <= mapping.subscription_end
    ):
        raise ProvisioningError(f"Subscription for {mapping.code} is not vigente")


def execute_provision_clean(
    reader: ControlPlaneReader,
    writer: ControlPlaneWriter,
    backend: ProvisioningBackend,
    tools: ToolRunner,
    confirm: str,
    admin_email: str,
    password_provider: Callable[[str], str] | None = None,
    dump_directory: Path | None = None,
    reporter: ProgressReporter | Callable[[int, str], None] | None = None,
) -> str:
    """Provision a new tenant from postgres.public without copying tenant data."""
    mapping = reader.read_mapping(CLEAN_EXPECTED_CODE)
    validate_clean_mapping(mapping, confirm, CLEAN_EXPECTED_CODE, CLEAN_EXPECTED_DATABASE)
    return _execute_provision_clean_mapping(
        mapping, writer, backend, tools, confirm, admin_email, password_provider,
        dump_directory, reporter,
    )


def _execute_provision_clean_mapping(
    mapping: TenantMapping,
    writer: ControlPlaneWriter,
    backend: ProvisioningBackend,
    tools: ToolRunner,
    confirm: str,
    admin_email: str,
    password_provider: Callable[[str], str] | None = None,
    dump_directory: Path | None = None,
    reporter: ProgressReporter | Callable[[int, str], None] | None = None,
    preflight: Preflight | None = None,
) -> str:
    progress = _progress_callback(reporter)
    validate_clean_mapping(mapping, confirm, mapping.code, mapping.database_name)
    if confirm != mapping.database_name:
        raise ProvisioningError("strong confirmation does not match the Control Plane mapping")
    if not all(tools.available(name) for name in REQUIRED_TOOLS):
        raise ProvisioningError("Required PostgreSQL client tools are unavailable")
    email = _validate_bootstrap_email(admin_email)

    progress(1, "Preflight clean")
    preflight = preflight or backend.preflight(mapping)
    if not preflight.role_createdb or not preflight.dependencies_ok or preflight.source_structure is None:
        raise ProvisioningError("Read-only clean prechecks failed")
    if backend.database_exists(mapping.database_name):
        raise ProvisioningError("Target database already exists")

    prompt_password = password_provider or getpass.getpass
    password = prompt_password("Bootstrap admin password: ")
    confirmation = prompt_password("Confirm bootstrap admin password: ")
    if not password or password != confirmation:
        raise ProvisioningError("Bootstrap passwords do not match")
    from app.core.security import hash_password
    password_hash = hash_password(password)

    dump_path: Path | None = None
    writer.start(mapping)
    try:
        try:
            directory = dump_directory or Path(tempfile.gettempdir())
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix="tenant_clean_", suffix=".dump", dir=directory, delete=False) as handle:
                dump_path = Path(handle.name)
            progress(2, "Estado PROVISIONANDO")
            progress(3, "Creando database")
            backend.create_database(mapping.database_name)
            progress(4, "Generando schema-only desde postgres.public")
            backend.dump_public("postgres", dump_path, None, schema_only=True)
            progress(5, "Restaurando public")
            backend.restore_public(mapping.database_name, dump_path)
            progress(6, "Sembrando catálogos permitidos")
            backend.seed_catalogs("postgres", mapping.database_name, directory)
            progress(7, "Creando bootstrap admin")
            backend.bootstrap_admin(mapping.database_name, email, password_hash)
            progress(8, "Verificando tenant limpio")
            verification = backend.verify_clean(mapping.database_name, preflight.source_structure, email)
            if not verification.ok:
                raise ProvisioningError("Clean tenant verification failed")
            progress(9, "Health check")
            if not backend.health_check(mapping):
                raise ProvisioningError("Tenant health check failed")
            progress(10, "Activando tenant")
        finally:
            if dump_path is not None:
                try:
                    backend.cleanup_dump(dump_path)
                finally:
                    dump_path = None
        writer.complete(mapping, version=SCHEMA_VERSION)
        return f"Clean provisioning completed: {mapping.code} is ACTIVA/COMPLETADO."
    except Exception as exc:
        writer.fail(mapping, _sanitized_error(str(exc)))
        raise ProvisioningError("Clean provisioning failed; Control Plane marked ERROR") from exc


def _validate_batch_source_preflight(preflight: Preflight) -> None:
    if not preflight.role_createdb or not preflight.dependencies_ok:
        raise ProvisioningError("Batch read-only PostgreSQL prechecks failed")
    if preflight.source_structure is None or preflight.source_counts is None:
        raise ProvisioningError("Batch source schema v1 snapshot is unavailable")
    if set(preflight.source_counts) != set(SOURCE_TABLES):
        raise ProvisioningError("Batch source counts are incomplete or unexpected")
    object_counts = set(preflight.source_structure.object_counts)
    if {"tables=27", "sequences=27"} - object_counts:
        raise ProvisioningError("Batch source schema must contain 27 tables and 27 sequences")
    if len(preflight.source_structure.columns) != 182:
        raise ProvisioningError("Batch source schema must contain 182 columns")
    if len(preflight.source_structure.indexes) != 67:
        raise ProvisioningError("Batch source schema must contain 67 indexes")


def execute_provision_pending_batch(
    reader: ControlPlaneReader,
    writer: ControlPlaneWriter,
    backend: ProvisioningBackend,
    tools: ToolRunner,
    password_provider: Callable[[str], str] | None = None,
    dump_directory: Path | None = None,
    reporter: ProgressReporter | Callable[[int, str], None] | None = None,
) -> str:
    """Provision the immutable five-tenant demo batch sequentially.

    All reads and read-only PostgreSQL checks happen before the first writer or
    CREATE DATABASE operation.  The caller must inject the password provider so
    passwords never become CLI arguments, log messages, or Control Plane data.
    """
    if not all(tools.available(name) for name in REQUIRED_TOOLS):
        raise ProvisioningError("Required PostgreSQL client tools are unavailable")

    mappings: list[tuple[TenantMapping, str, Preflight]] = []
    database_names: set[str] = set()
    for code, database_name, admin_email in PENDING_BATCH_TARGETS:
        mapping = reader.read_mapping(code)
        _validate_batch_mapping(mapping, code, database_name)
        if mapping.database_name in database_names:
            raise ProvisioningError("Batch database names must be distinct")
        database_names.add(mapping.database_name)
        if backend.database_exists(mapping.database_name):
            raise ProvisioningError(f"Batch target database already exists: {mapping.database_name}")
        preflight = backend.preflight(mapping)
        _validate_batch_source_preflight(preflight)
        mappings.append((mapping, admin_email, preflight))

    prompt_password = password_provider or getpass.getpass
    progress = _progress_callback(reporter)
    completed = []
    for index, (planned_mapping, admin_email, preflight) in enumerate(mappings, start=1):
        mapping = reader.read_mapping(planned_mapping.code)
        _validate_batch_mapping(mapping, planned_mapping.code, planned_mapping.database_name)
        if mapping.tenant_database_id != planned_mapping.tenant_database_id or mapping.provisioning_id != planned_mapping.provisioning_id:
            raise ProvisioningError(f"Batch mapping changed for {mapping.code}; refusing to continue")
        progress(index, f"Tenant {mapping.code}")
        result = _execute_provision_clean_mapping(
            mapping, writer, backend, tools, mapping.database_name, admin_email,
            password_provider=prompt_password, dump_directory=dump_directory,
            reporter=reporter, preflight=preflight,
        )
        completed.append(result)
    return "Batch provisioning completed: " + ", ".join(completed)


def reconcile_existing(
    reader: ControlPlaneReader,
    writer: ControlPlaneWriter,
    backend: ProvisioningBackend,
    confirm: str,
    reporter: ProgressReporter | Callable[[int, str], None] | None = None,
) -> str:
    """Verify an existing ERROR tenant and activate it without provisioning.

    This path deliberately has no start/create/dump/restore operation.  It is
    also intentionally independent of the PostgreSQL client-tool availability
    gate used by normal provisioning.
    """
    if confirm != CLEAN_EXPECTED_DATABASE:
        raise ProvisioningError("Strong confirmation does not match the target database")
    mapping = reader.read_mapping(CLEAN_EXPECTED_CODE)
    if mapping.code != CLEAN_EXPECTED_CODE or mapping.database_name != CLEAN_EXPECTED_DATABASE:
        raise ProvisioningError("Reconciliation mapping is not the expected Control Plane tenant")
    if mapping.tenant_state != ERROR or mapping.provisioning_state != ERROR:
        raise ProvisioningError("Reconciliation requires ERROR tenant and provisioning states")
    validate_database_identifier(mapping.database_name)
    progress = _progress_callback(reporter)
    try:
        progress(1, "Preflight reconciliation")
        if not backend.database_exists(mapping.database_name):
            raise ProvisioningError("Reconciliation target database does not exist")
        backend.dispose_tenant_engine(mapping.tenant_database_id or -1)

        progress(2, "Snapshot source")
        source_snapshot = getattr(backend, "reconciliation_source_snapshot", backend.preflight)
        preflight = source_snapshot(mapping)
        if not preflight.role_createdb or not preflight.dependencies_ok:
            raise ProvisioningError("Reconciliation read-only prechecks failed")
        if preflight.source_counts is None or preflight.source_structure is None:
            raise ProvisioningError("Reconciliation source snapshot is unavailable")
        if set(preflight.source_counts) != set(SOURCE_TABLES):
            raise ProvisioningError("Reconciliation source counts are incomplete or unexpected")
        source_counts = dict(preflight.source_counts)

        progress(3, "Snapshot target")
        # The concrete verifier captures the target snapshot during the next
        # step.  This hook makes the phase explicit without duplicating SQL.
        snapshot_target = getattr(backend, "snapshot_target", None)
        if snapshot_target is not None:
            snapshot_target(mapping.database_name)

        progress(4, "Structural verification")
        verify_existing = getattr(backend, "verify_existing", None)
        verification = (
            verify_existing(source_counts, mapping.database_name, preflight.source_structure)
            if verify_existing is not None
            else backend.verify(source_counts, mapping.database_name, preflight.source_structure)
        )
        structural_ok = verification.ok
        if not structural_ok:
            raise ProvisioningError("Tenant reconciliation verification failed")

        progress(5, "Row counts + health check")
        if not verification.counts_ok:
            raise ProvisioningError("Tenant reconciliation count verification failed")
        runtime_check = getattr(backend, "runtime_health_check", backend.health_check)
        if not runtime_check(mapping):
            raise ProvisioningError("Tenant reconciliation health check failed")

        progress(6, "Control Plane activation")
        writer.complete(mapping, version=SCHEMA_VERSION)
        return "Reconciliation completed: OFTALMO-NORTE is ACTIVA/COMPLETADO."
    except Exception as exc:
        writer.fail(mapping, _sanitized_error(str(exc)))
        if isinstance(exc, ProvisioningError):
            raise
        raise ProvisioningError("Reconciliation failed; Control Plane remains ERROR") from exc


def _progress_callback(reporter):
    if reporter is None:
        return lambda _step, _label: None
    if hasattr(reporter, "report"):
        return lambda step, label: reporter.report(step, label)
    return reporter


def execute_recovery(
    reader: ControlPlaneReader,
    writer: ControlPlaneWriter,
    backend: ProvisioningBackend,
    confirm: str,
    reporter: ProgressReporter | Callable[[int, str], None] | None = None,
) -> str:
    """Recover only the exact ERROR mapping; never complete or restore it."""
    if confirm != EXPECTED_DATABASE:
        raise ProvisioningError("Strong confirmation does not match the target database")
    mapping = reader.read_mapping(EXPECTED_CODE)
    if mapping.code != EXPECTED_CODE or mapping.database_name != EXPECTED_DATABASE:
        raise ProvisioningError("Recovery mapping is not the expected Control Plane tenant")
    if mapping.tenant_state != ERROR or mapping.provisioning_state != ERROR:
        raise ProvisioningError("Recovery requires ERROR tenant and provisioning states")
    validate_database_identifier(mapping.database_name)
    audit = backend.recovery_audit(mapping.database_name)
    if audit.source != "Control Plane mapping" or audit.database_name != mapping.database_name:
        raise ProvisioningError("Recovery audit source or target is inconsistent")
    if not audit.target_exists:
        raise ProvisioningError("Recovery target database does not exist")
    if audit.sessions and not (
        audit.residual_only
        and len(audit.sessions) == 1
        and all(session.residual_only for session in audit.sessions)
    ):
        raise ProvisioningError(
            "Recovery blocked: sessions are not exactly one residual Supavisor session"
        )
    progress = _progress_callback(reporter)
    progress(1, "Recovery audit: " + _recovery_session_report(audit))
    backend.dispose_tenant_engine(mapping.tenant_database_id or -1)
    progress(2, "Tenant engine disposed")
    # Do not manually terminate the sole Supavisor session.  DROP ... WITH
    # (FORCE) is the required and only session cleanup mechanism here.
    progress(3, "Target sessions verified")
    try:
        backend.drop_database(mapping.database_name)
        if backend.database_exists(mapping.database_name):
            raise ProvisioningError("Recovery DROP completed but target database still exists")
    except Exception as exc:
        writer.fail(mapping, _sanitized_error(str(exc)))
        if isinstance(exc, ProvisioningError):
            raise
        raise ProvisioningError("Recovery DROP failed; Control Plane remains ERROR") from exc
    progress(4, "Target database dropped")
    writer.reset_after_recovery(mapping)
    progress(5, "Control Plane reset")
    return "Recovery completed: tenant_vision_clara reset to PENDIENTE."


def build_reader() -> SqlAlchemyControlPlaneReader:
    _ensure_repository_root_on_sys_path()
    from app.database.session import SessionLocal
    return SqlAlchemyControlPlaneReader(SessionLocal)


def build_execute_components(pg_bin_dir: str | None, command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS):
    """Build production collaborators without opening a tenant connection."""
    _ensure_repository_root_on_sys_path()
    from sqlalchemy.engine import make_url
    from app.core.config import DATABASE_URL
    from app.database.session import SessionLocal

    url = make_url(DATABASE_URL)
    if not url.host or not url.username or url.password is None:
        raise ProvisioningError("DATABASE_URL lacks an administrative connection context")
    tools = PathToolRunner(pg_bin_dir)
    paths = tools.resolve()
    connection = PostgresConnection(
        host=url.host, port=url.port or 5432, user=url.username,
        password=url.password, sslmode=url.query.get("sslmode", "require"),
    )
    # Reuse the application's registry so an already cached local tenant
    # engine is actually disposed before administrative session cleanup.
    from app.modules.gestion_usuarios_seguridad.api.router import tenant_engine_registry
    return (
        SqlAlchemyControlPlaneReader(SessionLocal),
        SqlAlchemyControlPlaneWriter(SessionLocal),
        SubprocessPostgresBackend(paths, connection,
                                  command_timeout_seconds=command_timeout_seconds,
                                  engine_disposer=tenant_engine_registry.dispose_tenant),
        tools,
    )


def build_reconcile_components(pg_bin_dir: str | None, command_timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS):
    """Build the read-only reconciliation collaborators; only psql is resolved."""
    _ensure_repository_root_on_sys_path()
    from sqlalchemy.engine import make_url
    from app.core.config import DATABASE_URL
    from app.database.session import SessionLocal

    url = make_url(DATABASE_URL)
    if not url.host or not url.username or url.password is None:
        raise ProvisioningError("DATABASE_URL lacks an administrative connection context")
    tools = PathToolRunner(pg_bin_dir)
    connection = PostgresConnection(
        host=url.host, port=url.port or 5432, user=url.username,
        password=url.password, sslmode=url.query.get("sslmode", "require"),
    )
    from app.modules.gestion_usuarios_seguridad.api.router import tenant_engine_registry
    return (
        SqlAlchemyControlPlaneReader(SessionLocal),
        SqlAlchemyControlPlaneWriter(SessionLocal),
        SubprocessPostgresBackend(tools.resolve_psql(), connection,
                                  command_timeout_seconds=command_timeout_seconds,
                                  engine_disposer=tenant_engine_registry.dispose_tenant),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded tenant provisioner")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--provision-clean", action="store_true")
    parser.add_argument("--provision-pending-batch", action="store_true")
    parser.add_argument("--recover-error", action="store_true")
    parser.add_argument("--reconcile-clean-existing", dest="reconcile_clean_existing", action="store_true")
    parser.add_argument("--reconcile-existing", dest="reconcile_clean_existing", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--empresa",
                        help="exact Control Plane company code")
    parser.add_argument("--confirm")
    parser.add_argument("--pg-bin-dir")
    parser.add_argument("--database-name", help="optional assertion; never an input mapping")
    parser.add_argument("--bootstrap-admin-email")
    parser.add_argument("--command-timeout-seconds", type=int, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    modes = sum(bool(value) for value in (args.dry_run, args.execute, args.provision_clean,
                                          args.provision_pending_batch, args.recover_error,
                                          args.reconcile_clean_existing))
    if modes != 1:
        parser.error("choose exactly one provisioning mode")
    if args.command_timeout_seconds <= 0:
        parser.error("--command-timeout-seconds must be a positive integer")
    if not args.provision_pending_batch and not args.empresa:
        parser.error("--empresa is required for this mode")
    if args.dry_run and args.empresa != EXPECTED_CODE:
        parser.error("--dry-run requires --empresa VISION-CLARA")
    if (args.execute or args.recover_error) and args.empresa != EXPECTED_CODE:
        parser.error("this mode requires --empresa VISION-CLARA")
    if args.reconcile_clean_existing and args.empresa != CLEAN_EXPECTED_CODE:
        parser.error("--reconcile-clean-existing requires --empresa OFTALMO-NORTE")
    if args.provision_clean and args.empresa != CLEAN_EXPECTED_CODE:
        parser.error("--provision-clean requires --empresa OFTALMO-NORTE")
    if args.provision_pending_batch and args.empresa is not None:
        parser.error("--provision-pending-batch uses its closed tenant list; do not pass --empresa")
    if (args.execute or args.recover_error) and args.confirm != EXPECTED_DATABASE:
        parser.error("mutating modes require --confirm tenant_vision_clara")
    if args.reconcile_clean_existing and args.confirm != CLEAN_EXPECTED_DATABASE:
        parser.error("--reconcile-clean-existing requires --confirm tenant_oftalmo_norte")
    if args.provision_clean and (not args.confirm or not args.bootstrap_admin_email):
        parser.error("--provision-clean requires --confirm and --bootstrap-admin-email")
    if args.recover_error and args.database_name is not None:
        parser.error("--recover-error never accepts --database-name")
    if args.reconcile_clean_existing and args.database_name is not None:
        parser.error("--reconcile-clean-existing never accepts --database-name")
    if args.provision_clean and args.database_name is not None:
        parser.error("--provision-clean never accepts --database-name")
    if args.provision_pending_batch and any(value is not None for value in (
        args.confirm, args.database_name, args.bootstrap_admin_email,
    )):
        parser.error("--provision-pending-batch does not accept confirmation, database, or password arguments")
    try:
        tools = PathToolRunner(args.pg_bin_dir)
        if args.dry_run:
            print(dry_run(build_reader(), tools, args.database_name))
            return 0
        if args.reconcile_clean_existing:
            reader, writer, backend = build_reconcile_components(
                args.pg_bin_dir, args.command_timeout_seconds,
            )
            print(reconcile_existing(reader, writer, backend, args.confirm,
                                     reporter=ConsoleProgressReporter()))
            return 0
        if args.provision_clean:
            reader, writer, backend, tools = build_execute_components(
                args.pg_bin_dir, args.command_timeout_seconds,
            )
            print(execute_provision_clean(
                reader, writer, backend, tools, args.confirm, args.bootstrap_admin_email,
                reporter=ConsoleProgressReporter(),
            ))
            return 0
        if args.provision_pending_batch:
            reader, writer, backend, tools = build_execute_components(
                args.pg_bin_dir, args.command_timeout_seconds,
            )
            print(execute_provision_pending_batch(
                reader, writer, backend, tools, reporter=ConsoleProgressReporter(),
            ))
            return 0
        reader, writer, backend, tools = build_execute_components(
            args.pg_bin_dir, args.command_timeout_seconds,
        )
        if args.recover_error:
            print(execute_recovery(
                reader, writer, backend, args.confirm,
                reporter=ConsoleProgressReporter(),
            ))
        else:
            print(execute_provision(
                reader, writer, backend, tools, args.confirm, args.database_name,
                reporter=ConsoleProgressReporter(),
            ))
        return 0
    except ProvisioningError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception:
        print("provisioning failed while reading the Control Plane", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
