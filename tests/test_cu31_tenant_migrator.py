from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.administracion_saas.tenant_migrator import (
    ChecksumMismatchError,
    LedgerState,
    MigrationError,
    PlanChangedError,
    TenantTarget,
    build_plan,
    execute_plan,
    load_bundle,
)
from scripts.saas import migrate_active_tenants as cli


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = ROOT / "database" / "tenant_migrations"


class FakeDatabase:
    def __init__(
        self,
        *,
        ledger_exists=False,
        entries=(),
        fingerprint="legacy-ok",
        fingerprint_after=None,
        fail_on=None,
        lock=True,
        shared_events=None,
    ):
        self.exists = ledger_exists
        self.entries = dict(entries)
        self.fingerprint = fingerprint
        self.fingerprint_after = fingerprint_after or fingerprint
        self.fail_on = fail_on
        self.lock = lock
        self.events = shared_events if shared_events is not None else []
        self.fingerprint_calls = 0
        self.fail_once = True

    def _event(self, name):
        self.events.append(name)
        if self.fail_on == name and self.fail_once:
            self.fail_once = False
            raise RuntimeError("isolated failure")

    def ledger_state(self):
        self._event("ledger")
        return LedgerState(self.exists, tuple(sorted(self.entries.items())))

    def run_preflight(self, sql):
        assert "BEGIN TRANSACTION READ ONLY" in sql
        self._event("preflight")

    def acquire_lock(self):
        self._event("lock")
        return self.lock

    def release_lock(self):
        self._event("unlock")

    def fingerprint_legacy_table(self):
        self._event("fingerprint")
        self.fingerprint_calls += 1
        return self.fingerprint if self.fingerprint_calls == 1 else self.fingerprint_after

    def bootstrap_ledger(self, sql):
        assert "app_migrations.schema_migration" in sql
        self._event("bootstrap")
        self.exists = True

    def apply_transactional(self, migration, runner_version):
        self._event("apply:" + migration.version)
        self.entries[migration.version] = migration.checksum

    def apply_idempotent_script(self, migration, runner_version):
        self._event("apply:" + migration.version)
        self.entries[migration.version] = migration.checksum

    def verify(self, sql):
        assert "BEGIN TRANSACTION READ ONLY" in sql
        self._event("verify")

    def close(self):
        self.events.append("close")


class FakeFactory:
    def __init__(self, databases):
        self.databases = databases
        self.opened = []

    def open(self, target):
        self.opened.append(target.database_name)
        return self.databases[target.database_name]


class FakeControlPlane:
    def __init__(self, events=None, fail=False):
        self.events = events if events is not None else []
        self.fail = fail
        self.updates = []

    def assert_target_current(self, target):
        self.events.append("control-check:" + target.database_name)
        if self.fail:
            raise RuntimeError("control failure")

    def update_schema_version(self, target, schema_version):
        self.events.append("version:" + target.database_name)
        if self.fail:
            raise RuntimeError("control failure")
        self.updates.append((target.database_name, schema_version))


@pytest.fixture(scope="module")
def bundle():
    return load_bundle(MIGRATION_ROOT)


def target(name="tenant_alpha", *, managed=True, version="v1"):
    return TenantTarget(
        tenant_database_id=11 if managed else None,
        empresa_codigo="ALPHA" if managed else "DISPOSABLE-TEST",
        database_name=name,
        version_schema=version,
        managed=managed,
    )


def test_manifest_has_exact_approved_order_and_valid_checksums(bundle):
    assert [item.version for item in bundle.migrations] == [
        "20261007211200", "20261007211247", "20261019000000",
    ]
    assert bundle.schema_version == "v2-cu31"


def test_changed_local_artifact_is_rejected_before_database_access(tmp_path):
    repository = tmp_path / "repo"
    root = repository / "database" / "tenant_migrations"
    shutil.copytree(MIGRATION_ROOT, root)
    (repository / "db").mkdir(parents=True)
    shutil.copy2(
        ROOT / "db" / "cu31_pagos_administrativos_resend.sql",
        repository / "db" / "cu31_pagos_administrativos_resend.sql",
    )
    migration = root / "versions" / "20261007211247_ajustar_indices_pago.sql"
    migration.write_text(migration.read_text() + "\n-- drift\n", encoding="utf-8")

    with pytest.raises(ChecksumMismatchError):
        load_bundle(root)


def test_plan_is_read_only_and_does_not_bootstrap_or_apply(bundle):
    database = FakeDatabase()
    plan = build_plan((target(),), bundle, FakeFactory({"tenant_alpha": database}))

    assert "preflight" in database.events
    assert "bootstrap" not in database.events
    assert not any(event.startswith("apply:") for event in database.events)
    assert len(plan.plan_hash) == 64


def test_plan_rejects_recorded_checksum_drift(bundle):
    database = FakeDatabase(
        ledger_exists=True,
        entries=((bundle.migrations[0].version, "0" * 64),),
    )
    with pytest.raises(ChecksumMismatchError):
        build_plan((target(),), bundle, FakeFactory({"tenant_alpha": database}))


def test_execute_requires_exact_plan_confirmation(bundle):
    database = FakeDatabase()
    factory = FakeFactory({"tenant_alpha": database})
    plan = build_plan((target(),), bundle, factory)

    with pytest.raises(PlanChangedError):
        execute_plan(plan, "wrong", bundle, factory, FakeControlPlane())
    assert "lock" not in database.events


def test_supporting_sql_change_invalidates_confirmed_plan(bundle):
    database = FakeDatabase()
    factory = FakeFactory({"tenant_alpha": database})
    plan = build_plan((target(),), bundle, factory)
    altered = replace(
        bundle,
        verification_sql=bundle.verification_sql + "\n-- changed\n",
        supporting_checksums=tuple(
            (name, "f" * 64 if name == "999_verify_cu31.sql" else checksum)
            for name, checksum in bundle.supporting_checksums
        ),
    )

    with pytest.raises(PlanChangedError, match="artifacts changed"):
        execute_plan(plan, plan.plan_hash, altered, factory, FakeControlPlane())
    assert "lock" not in database.events


def test_complete_execution_orders_migrations_verification_and_version(bundle):
    events = []
    database = FakeDatabase(shared_events=events)
    factory = FakeFactory({"tenant_alpha": database})
    control = FakeControlPlane(events)
    plan = build_plan((target(),), bundle, factory)
    events.clear()

    result = execute_plan(plan, plan.plan_hash, bundle, factory, control)[0]

    apply_events = [event for event in events if event.startswith("apply:")]
    assert apply_events == ["apply:" + item.version for item in bundle.migrations]
    assert events.index("verify") < events.index("version:tenant_alpha")
    assert events.index("control-check:tenant_alpha") < events.index("bootstrap")
    assert events.index("fingerprint") < events.index("bootstrap")
    assert events[-2:] == ["unlock", "close"]
    assert result.applied == tuple(item.version for item in bundle.migrations)
    assert control.updates == [("tenant_alpha", "v2-cu31")]


def test_historical_table_change_blocks_version_update(bundle):
    database = FakeDatabase(fingerprint_after="legacy-changed")
    factory = FakeFactory({"tenant_alpha": database})
    control = FakeControlPlane()
    plan = build_plan((target(),), bundle, factory)

    with pytest.raises(MigrationError, match="Historical table fingerprint"):
        execute_plan(plan, plan.plan_hash, bundle, factory, control)
    assert control.updates == []


def test_resume_skips_confirmed_versions_without_duplicates(bundle):
    recorded = tuple(
        (item.version, item.checksum) for item in bundle.migrations[:2]
    )
    database = FakeDatabase(ledger_exists=True, entries=recorded)
    factory = FakeFactory({"tenant_alpha": database})
    plan = build_plan((target(),), bundle, factory)

    result = execute_plan(
        plan, plan.plan_hash, bundle, factory, FakeControlPlane(),
    )[0]

    assert result.skipped == tuple(item.version for item in bundle.migrations[:2])
    assert result.applied == (bundle.migrations[2].version,)
    assert len(database.entries) == 3


def test_failure_keeps_committed_ledger_and_resume_continues(bundle):
    database = FakeDatabase(fail_on="apply:20261019000000")
    factory = FakeFactory({"tenant_alpha": database})
    first = build_plan((target(),), bundle, factory)

    with pytest.raises(RuntimeError, match="isolated failure"):
        execute_plan(first, first.plan_hash, bundle, factory, FakeControlPlane())
    assert set(database.entries) == {
        "20261007211200", "20261007211247",
    }

    database.fingerprint_calls = 0
    resumed = build_plan((target(),), bundle, factory)
    result = execute_plan(
        resumed, resumed.plan_hash, bundle, factory, FakeControlPlane(),
    )[0]
    assert result.applied == ("20261019000000",)
    assert len(database.entries) == 3


def test_state_change_after_plan_is_detected_under_lock(bundle):
    database = FakeDatabase()
    factory = FakeFactory({"tenant_alpha": database})
    plan = build_plan((target(),), bundle, factory)
    database.exists = True
    database.entries[bundle.migrations[0].version] = bundle.migrations[0].checksum

    with pytest.raises(PlanChangedError, match="changed after planning"):
        execute_plan(plan, plan.plan_hash, bundle, factory, FakeControlPlane())


def test_lock_contention_stops_without_migrations(bundle):
    database = FakeDatabase(lock=False)
    factory = FakeFactory({"tenant_alpha": database})
    plan = build_plan((target(),), bundle, factory)

    with pytest.raises(MigrationError, match="locked"):
        execute_plan(plan, plan.plan_hash, bundle, factory, FakeControlPlane())
    assert not any(event.startswith("apply:") for event in database.events)


def test_first_tenant_failure_stops_batch(bundle):
    first = FakeDatabase(fail_on="apply:20261007211200")
    second = FakeDatabase()
    factory = FakeFactory({"tenant_a": first, "tenant_b": second})
    targets = (target("tenant_a"), replace(target("tenant_b"), tenant_database_id=12))
    plan = build_plan(targets, bundle, factory)
    factory.opened.clear()

    with pytest.raises(RuntimeError):
        execute_plan(plan, plan.plan_hash, bundle, factory, FakeControlPlane())
    assert factory.opened == ["tenant_a"]


def test_all_applied_is_idempotent_and_only_verifies(bundle):
    entries = tuple((item.version, item.checksum) for item in bundle.migrations)
    database = FakeDatabase(ledger_exists=True, entries=entries)
    factory = FakeFactory({"tenant_alpha": database})
    plan = build_plan((target(),), bundle, factory)

    result = execute_plan(
        plan, plan.plan_hash, bundle, factory, FakeControlPlane(),
    )[0]
    assert result.applied == ()
    assert result.skipped == tuple(item.version for item in bundle.migrations)
    assert not any(event.startswith("apply:") for event in database.events)
    assert "verify" in database.events


def test_disposable_target_never_updates_control_plane(bundle):
    database = FakeDatabase()
    factory = FakeFactory({"tenant_cu31_test": database})
    control = FakeControlPlane()
    disposable = target("tenant_cu31_test", managed=False, version=None)
    plan = build_plan((disposable,), bundle, factory)

    result = execute_plan(plan, plan.plan_hash, bundle, factory, control)[0]
    assert result.version_updated is False
    assert control.updates == []


def test_version_claim_without_complete_ledger_is_rejected(bundle):
    database = FakeDatabase(ledger_exists=True)
    with pytest.raises(MigrationError, match="ledger is incomplete"):
        build_plan(
            (target(version="v2-cu31"),),
            bundle,
            FakeFactory({"tenant_alpha": database}),
        )


def test_cli_execute_requires_confirmation_and_allow_flag(monkeypatch, capsys):
    monkeypatch.delenv(cli.ALLOW_ENV, raising=False)
    assert cli.main(["--execute"]) == cli.EXIT_REFUSED
    assert "--confirm-plan" in capsys.readouterr().err

    assert cli.main(["--execute", "--confirm-plan", "a" * 64]) == cli.EXIT_REFUSED
    assert cli.ALLOW_ENV in capsys.readouterr().err


def test_public_plan_contains_no_connection_secrets(bundle):
    database = FakeDatabase()
    plan = build_plan((target(),), bundle, FakeFactory({"tenant_alpha": database}))
    text = json.dumps(plan.to_public_dict())
    assert "postgresql://" not in text
    assert "password" not in text.lower()


def test_postgres_timeout_settings_use_literals_not_bind_parameters():
    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, *args):
            self.calls.append(args)

    cursor = Cursor()
    database = cli.PsycopgTenantDatabase(
        connection=object(),
        database_name="tenant_cu31_test",
        runner_version="cu31-tenant-migrator-v1",
    )

    database._set_local_timeouts(cursor)

    assert cursor.calls == [
        ("SET LOCAL lock_timeout = '5s'",),
        ("SET LOCAL statement_timeout = '120s'",),
    ]


def test_control_plane_database_filter_has_explicit_postgres_type():
    """Psycopg cannot infer ``:database_name`` inside ``IS NULL`` alone."""
    source = Path(cli.__file__).read_text(encoding="utf-8")

    assert "CAST(:database_name AS VARCHAR) IS NULL" in source
