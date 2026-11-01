"""Versioned, resumable tenant migrations for the clinical tenant schema.

The orchestration is deliberately dependency-injected. Unit tests never open a
database, while the production adapter lives in ``scripts/saas``. A migration
is considered applied only when its version and checksum exist in the tenant
ledger. The control-plane schema version is advanced after all postconditions
and the legacy-table fingerprint have been verified.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


EXPECTED_VERSIONS = (
    "20261007211200",
    "20261007211247",
    "20261019000000",
    "20261031000000",
)
EXPECTED_EXTERNAL_MIGRATIONS = (
    (
        "20261024150000",
        "cu26_cu27_cu29_inventario_compras",
        "d94e3e71041b5c823a08143081a8a6d9b341893a41f32ba15d072083163409b1",
    ),
)
TRANSACTION_RUNNER = "runner"
TRANSACTION_SCRIPT = "script_idempotent"


class MigrationError(RuntimeError):
    """Base error for a refused or failed tenant migration."""


class ManifestError(MigrationError):
    """The migration manifest or an artifact is not trustworthy."""


class PlanChangedError(MigrationError):
    """The database state changed after the confirmed plan was produced."""


class ChecksumMismatchError(MigrationError):
    """A recorded or local checksum does not match the immutable manifest."""


@dataclass(frozen=True)
class MigrationArtifact:
    version: str
    name: str
    path: Path
    checksum: str
    transaction: str
    sql: str


@dataclass(frozen=True)
class ExternalMigration:
    """Immutable ledger prerequisite whose original SQL is not in this repo."""

    version: str
    name: str
    checksum: str
    required_for_schema_versions: tuple[str, ...]


@dataclass(frozen=True)
class MigrationBundle:
    schema_version: str
    runner_version: str
    migrations: tuple[MigrationArtifact, ...]
    bootstrap_sql: str
    preflight_sql: str
    verification_sql: str
    supporting_checksums: tuple[tuple[str, str], ...]
    allowed_source_schema_versions: tuple[str, ...]
    external_migrations: tuple[ExternalMigration, ...]


@dataclass(frozen=True)
class TenantTarget:
    tenant_database_id: int | None
    empresa_codigo: str
    database_name: str
    version_schema: str | None
    managed: bool = True


@dataclass(frozen=True)
class LedgerState:
    exists: bool
    entries: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, str]:
        return dict(self.entries)


@dataclass(frozen=True)
class PlannedTenant:
    target: TenantTarget
    ledger_exists: bool
    ledger_entries: tuple[tuple[str, str], ...]
    migration_states: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MigrationPlan:
    schema_version: str
    runner_version: str
    migrations: tuple[tuple[str, str], ...]
    external_migrations: tuple[tuple[str, str, str], ...]
    supporting_checksums: tuple[tuple[str, str], ...]
    tenants: tuple[PlannedTenant, ...]
    plan_hash: str

    def to_public_dict(self) -> dict:
        return {
            "mode": "plan",
            "schema_version": self.schema_version,
            "runner_version": self.runner_version,
            "plan_hash": self.plan_hash,
            "migrations": [
                {"version": version, "sha256": checksum}
                for version, checksum in self.migrations
            ],
            "external_migrations": [
                {"version": version, "name": name, "sha256": checksum}
                for version, name, checksum in self.external_migrations
            ],
            "supporting_checksums": dict(self.supporting_checksums),
            "tenants": [
                {
                    "empresa_codigo": item.target.empresa_codigo,
                    "database_name": item.target.database_name,
                    "version_schema": item.target.version_schema,
                    "managed": item.target.managed,
                    "ledger_exists": item.ledger_exists,
                    "ledger_versions": [version for version, _ in item.ledger_entries],
                    "migrations": [
                        {"version": version, "state": state}
                        for version, state in item.migration_states
                    ],
                }
                for item in self.tenants
            ],
            "execute_requires": ["--confirm-plan " + self.plan_hash],
        }


@dataclass(frozen=True)
class TenantExecution:
    database_name: str
    applied: tuple[str, ...]
    skipped: tuple[str, ...]
    version_updated: bool


class TenantDatabase(Protocol):
    def ledger_state(self) -> LedgerState: ...
    def run_preflight(self, sql: str) -> None: ...
    def acquire_lock(self) -> bool: ...
    def release_lock(self) -> None: ...
    def fingerprint_legacy_table(self) -> str: ...
    def bootstrap_ledger(self, sql: str) -> None: ...
    def apply_transactional(
        self, migration: MigrationArtifact, runner_version: str,
    ) -> None: ...
    def apply_idempotent_script(
        self, migration: MigrationArtifact, runner_version: str,
    ) -> None: ...
    def verify(self, sql: str) -> None: ...
    def close(self) -> None: ...


class TenantDatabaseFactory(Protocol):
    def open(self, target: TenantTarget) -> TenantDatabase: ...


class ControlPlane(Protocol):
    def assert_target_current(self, target: TenantTarget) -> None: ...
    def update_schema_version(
        self, target: TenantTarget, schema_version: str,
    ) -> None: ...


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"Manifest field {field} is invalid")
    return value


def _resolve_artifact(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    repository = root.parents[1].resolve()
    try:
        candidate.relative_to(repository)
    except ValueError as exc:
        raise ManifestError("Migration artifact escapes the repository") from exc
    if not candidate.is_file():
        raise ManifestError(f"Migration artifact is missing: {relative}")
    return candidate


def load_bundle(root: Path) -> MigrationBundle:
    """Load and authenticate all CU31 artifacts before any DB connection."""
    root = root.resolve()
    manifest_path = root / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError("Migration manifest cannot be loaded") from exc

    schema_version = _require_string(payload.get("schema_version"), "schema_version")
    runner_version = _require_string(payload.get("runner_version"), "runner_version")
    raw_migrations = payload.get("migrations")
    if not isinstance(raw_migrations, list):
        raise ManifestError("Manifest migrations must be a list")
    raw_allowed = payload.get("allowed_source_schema_versions", [])
    if not isinstance(raw_allowed, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_allowed
    ):
        raise ManifestError("Manifest allowed_source_schema_versions is invalid")
    allowed_source_schema_versions = tuple(raw_allowed)

    raw_external = payload.get("external_migrations", [])
    if not isinstance(raw_external, list):
        raise ManifestError("Manifest external_migrations is invalid")
    external_migrations: list[ExternalMigration] = []
    for index, raw in enumerate(raw_external):
        if not isinstance(raw, dict):
            raise ManifestError(f"External migration {index} is invalid")
        version = _require_string(
            raw.get("version"), f"external_migrations[{index}].version",
        )
        name = _require_string(
            raw.get("name"), f"external_migrations[{index}].name",
        )
        checksum = _require_string(
            raw.get("sha256"), f"external_migrations[{index}].sha256",
        )
        required = raw.get("required_for_schema_versions")
        if (
            not isinstance(required, list) or not required
            or any(not isinstance(item, str) or not item.strip() for item in required)
        ):
            raise ManifestError(
                f"External migration {version} has invalid required schema versions"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ManifestError(
                f"External migration {version} has an invalid SHA-256"
            )
        external_migrations.append(ExternalMigration(
            version=version,
            name=name,
            checksum=checksum,
            required_for_schema_versions=tuple(required),
        ))

    artifacts: list[MigrationArtifact] = []
    seen_names: set[str] = set()
    seen_versions: set[str] = set()
    for index, raw in enumerate(raw_migrations):
        if not isinstance(raw, dict):
            raise ManifestError(f"Migration {index} is invalid")
        version = _require_string(raw.get("version"), f"migrations[{index}].version")
        name = _require_string(raw.get("name"), f"migrations[{index}].name")
        relative = _require_string(raw.get("path"), f"migrations[{index}].path")
        checksum = _require_string(raw.get("sha256"), f"migrations[{index}].sha256")
        transaction = _require_string(
            raw.get("transaction"), f"migrations[{index}].transaction",
        )
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ManifestError(f"Migration {version} has an invalid SHA-256")
        if version in seen_versions or name in seen_names:
            raise ManifestError("Manifest contains duplicate versions or names")
        if transaction not in {TRANSACTION_RUNNER, TRANSACTION_SCRIPT}:
            raise ManifestError(f"Migration {version} has an invalid transaction mode")

        path = _resolve_artifact(root, relative)
        raw_bytes = path.read_bytes()
        actual = _sha256(raw_bytes)
        if actual != checksum:
            raise ChecksumMismatchError(
                f"Local checksum mismatch for migration {version}"
            )
        sql = raw_bytes.decode("utf-8")
        controls = re.findall(r"(?im)^\s*(BEGIN|COMMIT|ROLLBACK)\s*;", sql)
        if transaction == TRANSACTION_RUNNER and controls:
            raise ManifestError(
                f"Runner-controlled migration {version} contains transaction control"
            )
        if transaction == TRANSACTION_SCRIPT and (
            controls.count("BEGIN") != 1 or controls.count("COMMIT") != 1
            or "ROLLBACK" in controls
        ):
            raise ManifestError(
                f"Script-controlled migration {version} is not one atomic script"
            )
        artifacts.append(MigrationArtifact(
            version=version,
            name=name,
            path=path,
            checksum=checksum,
            transaction=transaction,
            sql=sql,
        ))
        seen_versions.add(version)
        seen_names.add(name)

    if tuple(item.version for item in artifacts) != EXPECTED_VERSIONS:
        raise ManifestError("CU31 migration order differs from the approved sequence")
    external_identity = tuple(
        (item.version, item.name, item.checksum) for item in external_migrations
    )
    if external_identity != EXPECTED_EXTERNAL_MIGRATIONS:
        raise ManifestError("External migration identity differs from the approved ledger")
    regular_versions = {item.version for item in artifacts}
    external_versions = {item.version for item in external_migrations}
    if len(external_versions) != len(external_migrations) or regular_versions & external_versions:
        raise ManifestError("Manifest contains duplicate regular/external versions")
    valid_schema_versions = {
        schema_version, *allowed_source_schema_versions,
    }
    if any(
        version not in valid_schema_versions
        for item in external_migrations
        for version in item.required_for_schema_versions
    ):
        raise ManifestError("External migration references an unknown schema version")

    def read_guarded(filename: str, read_only: bool = False) -> tuple[str, str]:
        path = root / filename
        try:
            sql = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"Required SQL is missing: {filename}") from exc
        if read_only and not re.search(
            r"(?im)^\s*BEGIN\s+TRANSACTION\s+READ\s+ONLY\s*;", sql,
        ):
            raise ManifestError(f"{filename} is not guarded by READ ONLY")
        return sql, _sha256(sql.encode("utf-8"))

    support = payload.get("supporting_files", {})
    if not isinstance(support, dict):
        raise ManifestError("Manifest supporting_files is invalid")
    bootstrap_name = support.get("bootstrap", "bootstrap_ledger.sql")
    preflight_name = support.get("preflight", "000_preflight_cu31.sql")
    verification_name = support.get("verification", "999_verify_cu31.sql")
    for field, value in (
        ("bootstrap", bootstrap_name),
        ("preflight", preflight_name),
        ("verification", verification_name),
    ):
        _require_string(value, f"supporting_files.{field}")

    bootstrap_sql, bootstrap_checksum = read_guarded(bootstrap_name)
    preflight_sql, preflight_checksum = read_guarded(
        preflight_name, read_only=True,
    )
    verification_sql, verification_checksum = read_guarded(
        verification_name, read_only=True,
    )

    return MigrationBundle(
        schema_version=schema_version,
        runner_version=runner_version,
        migrations=tuple(artifacts),
        bootstrap_sql=bootstrap_sql,
        preflight_sql=preflight_sql,
        verification_sql=verification_sql,
        supporting_checksums=(
            (bootstrap_name, bootstrap_checksum),
            (preflight_name, preflight_checksum),
            (verification_name, verification_checksum),
        ),
        allowed_source_schema_versions=allowed_source_schema_versions,
        external_migrations=tuple(external_migrations),
    )


def _validate_ledger(bundle: MigrationBundle, ledger: LedgerState) -> None:
    entries = ledger.as_dict()
    if len(entries) != len(ledger.entries):
        raise MigrationError("Tenant ledger contains duplicate versions")
    expected = {item.version: item.checksum for item in bundle.migrations}
    expected.update(
        {item.version: item.checksum for item in bundle.external_migrations}
    )
    for version, checksum in entries.items():
        if version not in expected:
            raise MigrationError(
                f"Tenant ledger contains unknown migration {version}"
            )
        if checksum != expected[version]:
            raise ChecksumMismatchError(
                f"Recorded checksum mismatch for migration {version}"
            )


def _validate_schema_ledger_prerequisites(
    bundle: MigrationBundle,
    target: TenantTarget,
    ledger: LedgerState,
) -> None:
    if not target.managed:
        return
    entries = ledger.as_dict()
    required_external = [
        item for item in bundle.external_migrations
        if target.version_schema in item.required_for_schema_versions
    ]
    missing_external = [
        item.version for item in required_external if item.version not in entries
    ]
    if missing_external:
        raise MigrationError(
            "Tenant ledger is missing required external migrations: "
            + ", ".join(missing_external)
        )
    if target.version_schema in bundle.allowed_source_schema_versions:
        # A v3 tenant is an already established baseline. Replaying CU31 here
        # would hide an incomplete or incorrectly labelled tenant.
        missing_baseline = [
            item.version for item in bundle.migrations[:-1]
            if item.version not in entries
        ]
        if missing_baseline:
            raise MigrationError(
                "Tenant source ledger is incomplete: "
                + ", ".join(missing_baseline)
            )


def _migration_states(
    bundle: MigrationBundle, ledger: LedgerState,
) -> tuple[tuple[str, str], ...]:
    _validate_ledger(bundle, ledger)
    entries = ledger.as_dict()
    return tuple(
        (item.version, "applied" if item.version in entries else "pending")
        for item in bundle.migrations
    )


def _plan_payload(
    bundle: MigrationBundle, tenants: tuple[PlannedTenant, ...],
) -> dict:
    return {
        "schema_version": bundle.schema_version,
        "runner_version": bundle.runner_version,
        "migrations": [
            [item.version, item.checksum] for item in bundle.migrations
        ],
        "external_migrations": [
            [item.version, item.name, item.checksum]
            for item in bundle.external_migrations
        ],
        "supporting_checksums": [list(item) for item in bundle.supporting_checksums],
        "tenants": [
            {
                "tenant_database_id": item.target.tenant_database_id,
                "empresa_codigo": item.target.empresa_codigo,
                "database_name": item.target.database_name,
                "version_schema": item.target.version_schema,
                "managed": item.target.managed,
                "ledger_exists": item.ledger_exists,
                "ledger_entries": [list(entry) for entry in item.ledger_entries],
                "migration_states": [list(state) for state in item.migration_states],
            }
            for item in tenants
        ],
    }


def build_plan(
    targets: tuple[TenantTarget, ...],
    bundle: MigrationBundle,
    factory: TenantDatabaseFactory,
) -> MigrationPlan:
    if not targets:
        raise MigrationError("No tenant databases matched the requested scope")
    names = [target.database_name for target in targets]
    if len(set(names)) != len(names):
        raise MigrationError("Control plane returned duplicate tenant databases")

    planned: list[PlannedTenant] = []
    for target in sorted(targets, key=lambda item: item.database_name):
        if target.managed and (
            target.version_schema != bundle.schema_version
            and target.version_schema not in bundle.allowed_source_schema_versions
        ):
            raise MigrationError(
                f"Tenant {target.database_name} has unsupported schema version "
                f"{target.version_schema}; refusing a possible downgrade"
            )
        database = factory.open(target)
        try:
            ledger = database.ledger_state()
            # El preflight es de solo lectura y debe ejecutarse incluso cuando
            # ya existe ledger: cada nueva version puede agregar prerrequisitos.
            database.run_preflight(bundle.preflight_sql)
            states = _migration_states(bundle, ledger)
            _validate_schema_ledger_prerequisites(bundle, target, ledger)
            if target.version_schema == bundle.schema_version and any(
                state != "applied" for _, state in states
            ):
                raise MigrationError(
                    f"Tenant {target.database_name} claims {bundle.schema_version} "
                    "but its ledger is incomplete"
                )
            planned.append(PlannedTenant(
                target=target,
                ledger_exists=ledger.exists,
                ledger_entries=ledger.entries,
                migration_states=states,
            ))
        finally:
            database.close()

    tenants = tuple(planned)
    canonical = json.dumps(
        _plan_payload(bundle, tenants),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return MigrationPlan(
        schema_version=bundle.schema_version,
        runner_version=bundle.runner_version,
        migrations=tuple(
            (item.version, item.checksum) for item in bundle.migrations
        ),
        external_migrations=tuple(
            (item.version, item.name, item.checksum)
            for item in bundle.external_migrations
        ),
        supporting_checksums=bundle.supporting_checksums,
        tenants=tenants,
        plan_hash=_sha256(canonical),
    )


def execute_plan(
    plan: MigrationPlan,
    confirmed_hash: str,
    bundle: MigrationBundle,
    factory: TenantDatabaseFactory,
    control_plane: ControlPlane,
) -> tuple[TenantExecution, ...]:
    """Execute sequentially and stop on the first exception."""
    if not confirmed_hash or confirmed_hash != plan.plan_hash:
        raise PlanChangedError("Explicit plan confirmation does not match")
    if (
        plan.schema_version != bundle.schema_version
        or plan.migrations != tuple(
            (item.version, item.checksum) for item in bundle.migrations
        )
        or plan.external_migrations != tuple(
            (item.version, item.name, item.checksum)
            for item in bundle.external_migrations
        )
        or plan.supporting_checksums != bundle.supporting_checksums
    ):
        raise PlanChangedError("Migration artifacts changed after planning")

    results: list[TenantExecution] = []
    for planned in plan.tenants:
        database = factory.open(planned.target)
        locked = False
        try:
            locked = database.acquire_lock()
            if not locked:
                raise MigrationError(
                    f"Tenant {planned.target.database_name} is locked by another migrator"
                )

            if planned.target.managed:
                control_plane.assert_target_current(planned.target)

            ledger = database.ledger_state()
            current_states = _migration_states(bundle, ledger)
            _validate_schema_ledger_prerequisites(bundle, planned.target, ledger)
            if (
                ledger.exists != planned.ledger_exists
                or ledger.entries != planned.ledger_entries
                or current_states != planned.migration_states
            ):
                raise PlanChangedError(
                    f"Tenant {planned.target.database_name} changed after planning"
                )

            # Repetir bajo advisory lock cierra la ventana entre --plan y
            # --execute sin depender de que el tenant tenga o no ledger.
            database.run_preflight(bundle.preflight_sql)

            legacy_before = database.fingerprint_legacy_table()
            if not ledger.exists:
                database.bootstrap_ledger(bundle.bootstrap_sql)
                ledger = database.ledger_state()
                if not ledger.exists:
                    raise MigrationError("Tenant ledger bootstrap was not verifiable")

            applied: list[str] = []
            skipped: list[str] = []
            recorded = ledger.as_dict()
            for migration in bundle.migrations:
                if migration.version in recorded:
                    skipped.append(migration.version)
                    continue
                if migration.transaction == TRANSACTION_RUNNER:
                    database.apply_transactional(migration, bundle.runner_version)
                else:
                    database.apply_idempotent_script(migration, bundle.runner_version)
                applied.append(migration.version)
                recorded[migration.version] = migration.checksum

            final_ledger = database.ledger_state()
            _validate_ledger(bundle, final_ledger)
            missing = [
                item.version for item in bundle.migrations
                if item.version not in final_ledger.as_dict()
            ]
            if missing:
                raise MigrationError("Tenant ledger is incomplete after execution")

            database.verify(bundle.verification_sql)
            legacy_after = database.fingerprint_legacy_table()
            if legacy_before != legacy_after:
                raise MigrationError(
                    "Historical table fingerprint changed during CU31 migration"
                )

            version_updated = False
            if planned.target.managed:
                control_plane.update_schema_version(
                    planned.target, bundle.schema_version,
                )
                version_updated = True
            results.append(TenantExecution(
                database_name=planned.target.database_name,
                applied=tuple(applied),
                skipped=tuple(skipped),
                version_updated=version_updated,
            ))
        finally:
            try:
                if locked:
                    database.release_lock()
            finally:
                database.close()

    return tuple(results)
