"""Reusable PostgreSQL tenant restore runner.

It reuses the proven 8A client plumbing (``_client_path`` and the minimal child
environment from :mod:`backup_runner`) and the :class:`PostgresConnection` /
:class:`SubprocessRunner` contract from the provisioner. No tenant credentials
ever travel in argv, and ``shell=True`` is never used. The password is only ever
injected into the child environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scripts.saas.provision_tenant import (
    PostgresConnection,
    SubprocessRunner,
    validate_database_identifier,
)

from .backup_runner import _client_path, _minimal_client_environment

# Public tables whose presence is required before a restored tenant is trusted.
CRITICAL_TENANT_TABLES = ("usuario", "rol", "paciente", "cita", "historial_clinico")
_RESERVED_DATABASES = frozenset({"postgres", "template0", "template1", "saas_control"})

# ACL privilege characters relevant to a schema owner/grants round-trip.
_SCHEMA_ACL_PRIVILEGES = {"U": "USAGE", "C": "CREATE"}

# Cross-schema dependency probes used by the exact-restore preflight. Both are
# read-only and return a single integer.
_CROSS_SCHEMA_FK_SQL = (
    "SELECT count(*) FROM pg_constraint con "
    "JOIN pg_class c ON c.oid=con.conrelid "
    "JOIN pg_namespace n ON n.oid=c.relnamespace "
    "JOIN pg_class r ON r.oid=con.confrelid "
    "JOIN pg_namespace rn ON rn.oid=r.relnamespace "
    "WHERE rn.nspname='public' AND n.nspname<>'public' "
    "AND left(n.nspname,3) <> 'pg_' AND n.nspname<>'information_schema';"
)
_CROSS_SCHEMA_VIEW_SQL = (
    "SELECT count(*) FROM pg_depend d "
    "JOIN pg_rewrite rw ON rw.oid=d.objid AND d.classid='pg_rewrite'::regclass "
    "JOIN pg_class v ON v.oid=rw.ev_class "
    "JOIN pg_namespace vn ON vn.oid=v.relnamespace "
    "JOIN pg_class t ON t.oid=d.refobjid "
    "JOIN pg_namespace tn ON tn.oid=t.relnamespace "
    "WHERE tn.nspname='public' AND vn.nspname<>'public' "
    "AND left(vn.nspname,3) <> 'pg_' AND vn.nspname<>'information_schema';"
)


class SchemaResetRefused(RuntimeError):
    """The exact-restore preflight refused to reset the public schema."""


@dataclass(frozen=True)
class PublicSchemaState:
    owner: str
    grants: tuple[str, ...]
    extensions: tuple[str, ...]
    foreign_schemas: tuple[str, ...]
    cross_schema_dependencies: int


@dataclass(frozen=True)
class RestoreCapabilities:
    """Runtime-probed admin capabilities; never assumed."""

    supports_validation_database: bool = False


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _schema_grants_from_acl(acl_text: str) -> tuple[str, ...]:
    """Translate a ``pg_namespace.nspacl`` text form into GRANT statements.

    Each ACL item is ``grantee=privileges/grantor``; an empty grantee means
    ``PUBLIC``. Unknown privilege characters are ignored.
    """
    if not acl_text or not acl_text.strip():
        return ()
    statements: list[str] = []
    for item in acl_text.split("|"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        grantee, _, rest = item.partition("=")
        privileges, _, _grantor = rest.partition("/")
        words: list[str] = []
        for char in privileges:
            word = _SCHEMA_ACL_PRIVILEGES.get(char)
            if word and word not in words:
                words.append(word)
        if not words:
            continue
        target = "PUBLIC" if grantee == "" else _quote_identifier(grantee)
        statements.append(f"GRANT {', '.join(words)} ON SCHEMA public TO {target};")
    return tuple(statements)


class TenantRestoreRunner:
    def __init__(self, connection: PostgresConnection, pg_bin_dir: str | None = None,
                 timeout_seconds: int = 900, runner=None):
        if timeout_seconds <= 0:
            raise ValueError("Restore timeout must be positive")
        self.pg_restore = _client_path("pg_restore", pg_bin_dir)
        self.psql = _client_path("psql", pg_bin_dir)
        if self.pg_restore is None or self.psql is None:
            raise RuntimeError("PostgreSQL restore clients are unavailable")
        self.connection = connection
        self.timeout_seconds = timeout_seconds
        self.runner = runner or SubprocessRunner()

    # ------------------------------------------------------------------ admin
    def _env(self) -> dict[str, str]:
        env = _minimal_client_environment()
        env["PGPASSWORD"] = self.connection.password
        env["PGSSLMODE"] = self.connection.sslmode
        return env

    def _host_args(self) -> list[str]:
        return ["-h", self.connection.host, "-p", str(self.connection.port), "-U", self.connection.user]

    def _admin_query(self, sql: str) -> str:
        return self.runner.run(
            [str(self.psql), *self._host_args(), "-d", "postgres", "-tAc", sql],
            self._env(), self.timeout_seconds,
        )

    def _admin_command(self, sql: str) -> None:
        self.runner.run(
            [str(self.psql), *self._host_args(), "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", sql],
            self._env(), self.timeout_seconds,
        )

    def _query_db(self, database_name: str, sql: str) -> str:
        validate_database_identifier(database_name)
        return self.runner.run(
            [str(self.psql), *self._host_args(), "-d", database_name, "-tAc", sql],
            self._env(), self.timeout_seconds,
        )

    def _command_db(self, database_name: str, sql: str) -> None:
        validate_database_identifier(database_name)
        self.runner.run(
            [str(self.psql), *self._host_args(), "-d", database_name, "-v", "ON_ERROR_STOP=1", "-c", sql],
            self._env(), self.timeout_seconds,
        )

    @staticmethod
    def _require_manageable(database_name: str) -> str:
        validate_database_identifier(database_name)
        if database_name in _RESERVED_DATABASES:
            raise ValueError("Restore target is a reserved database")
        return database_name

    # ------------------------------------------------------------- operations
    def list_archive(self, archive: Path) -> None:
        self.runner.run([str(self.pg_restore), "--list", str(archive)],
                        self._env(), self.timeout_seconds)

    def probe_capabilities(self) -> RestoreCapabilities:
        role = self._admin_query(
            "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user;"
        ).strip()
        return RestoreCapabilities(supports_validation_database=role == "t")

    def capabilities(self) -> RestoreCapabilities:
        """Canonical capability probe consumed by the restore service."""
        return self.probe_capabilities()

    def database_exists(self, database_name: str) -> bool:
        validate_database_identifier(database_name)
        return self._admin_query(
            "SELECT 1 FROM pg_database WHERE datname = '"
            + database_name.replace("'", "''") + "';"
        ).strip() == "1"

    def create_database(self, database_name: str) -> None:
        self._require_manageable(database_name)
        self._admin_command(f'CREATE DATABASE "{database_name}" TEMPLATE template0;')

    def drop_database(self, database_name: str) -> None:
        self._require_manageable(database_name)
        self._admin_command(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE);')

    def rename_database(self, current_name: str, new_name: str) -> None:
        self._require_manageable(current_name)
        self._require_manageable(new_name)
        self._admin_command(f'ALTER DATABASE "{current_name}" RENAME TO "{new_name}";')

    def terminate_sessions(self, database_name: str) -> int:
        validate_database_identifier(database_name)
        output = self._admin_query(
            "SELECT count(*) FILTER (WHERE pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE datname = '" + database_name.replace("'", "''")
            + "' AND pid <> pg_backend_pid();"
        )
        return int(output.strip() or "0")

    def restore_into(self, database_name: str, archive: Path, *, clean: bool = False) -> None:
        validate_database_identifier(database_name)
        argv = [str(self.pg_restore), "--dbname", database_name,
                "--no-owner", "--no-privileges", "--exit-on-error"]
        if clean:
            argv += ["--clean", "--if-exists"]
        argv += [*self._host_args(), str(archive)]
        self.runner.run(argv, self._env(), self.timeout_seconds)

    # --------------------------------------------------------- exact restore
    def inspect_public_schema(self, database_name: str) -> PublicSchemaState:
        """Read-only snapshot of the public schema that a reset must preserve."""
        validate_database_identifier(database_name)
        owner = self._query_db(
            database_name,
            "SELECT COALESCE(pg_get_userbyid(nspowner), '') FROM pg_namespace "
            "WHERE nspname='public';",
        ).strip()
        raw_acl = self._query_db(
            database_name,
            "SELECT COALESCE(array_to_string(nspacl, '|'), '') FROM pg_namespace "
            "WHERE nspname='public';",
        ).strip()
        extensions = tuple(
            line.strip()
            for line in self._query_db(
                database_name,
                "SELECT extname FROM pg_extension WHERE extnamespace = 'public'::regnamespace "
                "ORDER BY extname;",
            ).splitlines()
            if line.strip()
        )
        foreign_schemas = tuple(
            line.strip()
            for line in self._query_db(
                database_name,
                "SELECT nspname FROM pg_namespace WHERE nspname <> 'public' "
                "AND left(nspname,3) <> 'pg_' AND nspname <> 'information_schema' "
                "ORDER BY nspname;",
            ).splitlines()
            if line.strip()
        )
        fk_count = self._query_db(database_name, _CROSS_SCHEMA_FK_SQL).strip()
        view_count = self._query_db(database_name, _CROSS_SCHEMA_VIEW_SQL).strip()
        cross_schema_dependencies = int(fk_count or "0") + int(view_count or "0")
        return PublicSchemaState(
            owner=owner,
            grants=_schema_grants_from_acl(raw_acl),
            extensions=extensions,
            foreign_schemas=foreign_schemas,
            cross_schema_dependencies=cross_schema_dependencies,
        )

    def assert_reset_safe(self, state: PublicSchemaState) -> None:
        """Refuse a public-schema reset that would destroy shared objects."""
        if state.extensions:
            raise SchemaResetRefused("extensions are installed in the public schema")
        if state.cross_schema_dependencies > 0:
            raise SchemaResetRefused("external objects depend on the public schema")

    def reset_public_schema(self, database_name: str) -> None:
        self._command_db(database_name, "DROP SCHEMA IF EXISTS public CASCADE;")
        self._command_db(database_name, "CREATE SCHEMA public;")

    def apply_public_schema_state(self, database_name: str, state: PublicSchemaState) -> None:
        if state.owner and state.owner != self.connection.user:
            self._command_db(
                database_name,
                f"ALTER SCHEMA public OWNER TO {_quote_identifier(state.owner)};",
            )
        for statement in state.grants:
            self._command_db(database_name, statement)

    def restore_exact(self, database_name: str, archive: Path) -> PublicSchemaState:
        """Rebuild ``public`` from an archive so the target matches it exactly."""
        self._require_manageable(database_name)
        state = self.inspect_public_schema(database_name)
        self.assert_reset_safe(state)
        self.reset_public_schema(database_name)
        self.restore_into(database_name, archive, clean=True)
        self.apply_public_schema_state(database_name, state)
        return state

    # -------------------------------------------------------------- verifying
    def verify_structure(self, database_name: str) -> bool:
        validate_database_identifier(database_name)
        tables = "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'"
        sequences = "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S'"
        critical = "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename IN (" + ", ".join(
            "'" + name + "'" for name in CRITICAL_TENANT_TABLES) + ")"
        output = self.runner.run(
            [str(self.psql), *self._host_args(), "-d", database_name, "-tAc",
             f"SELECT ({tables}), ({sequences}), ({critical});"],
            self._env(), self.timeout_seconds,
        )
        fields = [part.strip() for part in output.strip().split("|")]
        if len(fields) != 3 or not all(re.fullmatch(r"\d+", part or "") for part in fields):
            return False
        table_count, sequence_count, critical_count = (int(part) for part in fields)
        return table_count >= 1 and sequence_count >= 1 and critical_count == len(CRITICAL_TENANT_TABLES)

    def health_check(self, database_name: str) -> bool:
        validate_database_identifier(database_name)
        return self.runner.run(
            [str(self.psql), *self._host_args(), "-d", database_name, "-tAc", "SELECT 1;"],
            self._env(), self.timeout_seconds,
        ).strip() == "1"
