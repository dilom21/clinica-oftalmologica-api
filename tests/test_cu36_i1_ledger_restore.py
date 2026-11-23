"""I-1: exact migration-ledger expectations for every supported schema version.

The auditor found that ``TenantRestoreRunner._expected_ledger`` collapsed v3,
v4 and v5 onto the same ledger (every migration except the last one). A v3 or
v4 backup could therefore never verify its own ledger. These tests pin the
exact ledger of v2-cu31, v3, v4, v5 and v6 to ``BASELINE_LEDGER_VERSIONS`` and
exercise the backup/restore/rollback verification path with a fake psql.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.administracion_saas import backup_runner
from app.modules.administracion_saas.restore_runner import TenantRestoreRunner
from app.modules.administracion_saas.tenant_migrator import (
    BASELINE_LEDGER_VERSIONS,
    load_bundle,
)
from scripts.saas.provision_tenant import PostgresConnection

ROOT = Path(__file__).resolve().parents[1] / "database" / "tenant_migrations"
BUNDLE = load_bundle(ROOT)
REGULAR = {item.version: item.checksum for item in BUNDLE.migrations}
EXTERNAL = {item.version: item.checksum for item in BUNDLE.external_migrations}


def _ledger_lines(ledger: dict[str, str]) -> str:
    return "".join(
        f"{version}\t{checksum}\n" for version, checksum in sorted(ledger.items())
    )


class LedgerCapture:
    """Fake psql that answers the exact queries used by the restore runner."""

    def __init__(self, *, ledger: dict[str, str] | None = None,
                 archive_schemas: str = "SCHEMA - app_migrations admin"):
        self.ledger = dict(ledger or {})
        self.archive_schemas = archive_schemas
        self.calls: list[list[str]] = []

    def run(self, argv, env, timeout):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "--list" in argv:
            return "1; 0 0 SCHEMA - public admin\n2; 0 0 " + self.archive_schemas
        if "pg_get_userbyid" in joined:
            return "admin"
        if "array_to_string" in joined:
            return ""
        if "extnamespace" in joined:
            return ""
        if "left(nspname,3)" in joined:
            return ""
        if "con.confrelid" in joined:
            return "0"
        if "pg_rewrite" in joined:
            return "0"
        if "to_regclass('app_migrations.schema_migration')" in joined:
            return "1" if self.ledger else "0"
        if "SELECT version ||" in joined:
            return _ledger_lines(self.ledger)
        return ""


def _runner(tmp_path: Path, capture: LedgerCapture) -> TenantRestoreRunner:
    backup_runner._is_windows  # keep the import meaningful for monkeypatch users
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    return TenantRestoreRunner(
        PostgresConnection("private", 5432, "admin", "SECRET"),
        str(tmp_path), 30, capture,
    )


def _expected_v2() -> dict[str, str]:
    return {
        version: REGULAR[version]
        for version in BASELINE_LEDGER_VERSIONS["v3-sprint3-cu26-cu27-cu29"]
    }


def _expected_v3() -> dict[str, str]:
    return {
        **{
            version: REGULAR[version]
            for version in BASELINE_LEDGER_VERSIONS["v3-sprint3-cu26-cu27-cu29"]
        },
        **EXTERNAL,
    }


def _expected_v4() -> dict[str, str]:
    return {
        **{
            version: REGULAR[version]
            for version in BASELINE_LEDGER_VERSIONS["v4-sprint3-cu32-cu33"]
        },
        **EXTERNAL,
    }


def _expected_v5() -> dict[str, str]:
    return {
        **{
            version: REGULAR[version]
            for version in BASELINE_LEDGER_VERSIONS["v5-sprint4-cu36"]
        },
        **EXTERNAL,
    }


def _expected_v6() -> dict[str, str]:
    return {
        **{
            version: REGULAR[version]
            for version in BASELINE_LEDGER_VERSIONS["v6-sprint4-cu36"]
        },
        **EXTERNAL,
    }


EXPECTED = {
    "v2-cu31": _expected_v2,
    "v3-sprint3-cu26-cu27-cu29": _expected_v3,
    "v4-sprint3-cu32-cu33": _expected_v4,
    "v5-sprint4-cu36": _expected_v5,
    "v6-sprint4-cu36": _expected_v6,
    "v7-sprint5-notificaciones-push": lambda: {
        **{
            version: REGULAR[version]
            for version in BASELINE_LEDGER_VERSIONS[
                "v7-sprint5-notificaciones-push"
            ]
        },
        **EXTERNAL,
    },
    "v8-sprint5-worker-fcm-push": lambda: {
        **{
            version: REGULAR[version]
            for version in BASELINE_LEDGER_VERSIONS[
                "v8-sprint5-worker-fcm-push"
            ]
        },
        **EXTERNAL,
    },
    "v9-sprint5-revocacion-global-push": lambda: {**REGULAR, **EXTERNAL},
}


@pytest.mark.parametrize("version", sorted(EXPECTED))
def test_expected_ledger_is_exact_per_version(version):
    expected = EXPECTED[version]()
    assert TenantRestoreRunner._expected_ledger(version) == expected
    assert len(TenantRestoreRunner._expected_ledger(version)) == len(expected)


def test_expected_ledgers_are_distinct_and_monotonic():
    ledgers = [set(EXPECTED[version]()) for version in sorted(EXPECTED)]
    assert len({frozenset(item) for item in ledgers}) == len(ledgers)
    # Each source version is a strict subset of the next one.
    ordered = [
        set(EXPECTED["v2-cu31"]()),
        set(EXPECTED["v3-sprint3-cu26-cu27-cu29"]()),
        set(EXPECTED["v4-sprint3-cu32-cu33"]()),
        set(EXPECTED["v5-sprint4-cu36"]()),
        set(EXPECTED["v6-sprint4-cu36"]()),
        set(EXPECTED["v7-sprint5-notificaciones-push"]()),
        set(EXPECTED["v8-sprint5-worker-fcm-push"]()),
        set(EXPECTED["v9-sprint5-revocacion-global-push"]()),
    ]
    for previous, following in zip(ordered, ordered[1:]):
        assert previous < following


def test_expected_ledger_covers_all_manifest_migrations():
    actual = EXPECTED["v9-sprint5-revocacion-global-push"]()
    assert set(actual) == set(REGULAR) | set(EXTERNAL)
    assert actual == {**REGULAR, **EXTERNAL}


def test_unknown_schema_version_has_no_expected_ledger():
    for unknown in ("v0", "v99-unknown", "", "v4"):
        assert TenantRestoreRunner._expected_ledger(unknown) is None
        assert TenantRestoreRunner.is_known_schema_version(unknown) is False


@pytest.mark.parametrize("version", sorted(EXPECTED))
def test_verify_migration_ledger_accepts_only_the_exact_ledger(tmp_path, version):
    expected = EXPECTED[version]()
    capture = LedgerCapture(ledger=expected)
    runner = _runner(tmp_path, capture)
    assert runner.verify_migration_ledger("tenant_acme", version) is True

    # One missing entry is a mismatch.
    missing = sorted(expected)[0]
    capture.ledger = {k: v for k, v in expected.items() if k != missing}
    assert runner.verify_migration_ledger("tenant_acme", version) is False

    # One extra entry is a mismatch.
    capture.ledger = {**expected, "20990101000000": "f" * 64}
    assert runner.verify_migration_ledger("tenant_acme", version) is False

    # A wrong checksum is a mismatch.
    capture.ledger = dict(expected)
    first = sorted(expected)[0]
    capture.ledger[first] = "0" * 64
    assert runner.verify_migration_ledger("tenant_acme", version) is False


@pytest.mark.parametrize("version", sorted(EXPECTED))
def test_supports_archive_version_requires_a_ledger_for_every_version(tmp_path, version):
    assert TenantRestoreRunner.supports_archive_version(
        version, has_ledger=True,
    ) is True
    assert TenantRestoreRunner.is_known_schema_version(version) is True
    capture = LedgerCapture(ledger=EXPECTED[version]())
    runner = _runner(tmp_path, capture)
    assert runner.verify_database_schema_version("tenant_acme", version) is True
    assert runner.verify_database_schema_version("tenant_acme", "v99") is False


@pytest.mark.parametrize("version", ["v2-cu31", "v3-sprint3-cu26-cu27-cu29",
                                    "v4-sprint3-cu32-cu33", "v5-sprint4-cu36",
                                    "v6-sprint4-cu36",
                                    "v7-sprint5-notificaciones-push",
                                    "v8-sprint5-worker-fcm-push",
                                    "v9-sprint5-revocacion-global-push"])
def test_restore_exact_verifies_the_archive_ledger(tmp_path, monkeypatch, version):
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    capture = LedgerCapture(ledger=EXPECTED[version]())
    runner = _runner(tmp_path, capture)
    archive = tmp_path / "archive.dump"
    runner.restore_exact("tenant_acme", archive, expected_schema_version=version)

    joined = [" ".join(argv) for argv in capture.calls]
    drop = next(i for i, text in enumerate(joined)
                if "DROP SCHEMA IF EXISTS public CASCADE;" in text)
    restore = next(i for i, text in enumerate(joined) if "--dbname" in text)
    assert drop < restore
    assert any("DROP SCHEMA IF EXISTS app_migrations CASCADE;" in text
               for text in joined)


def test_restore_exact_refuses_a_mislabeled_backup(tmp_path, monkeypatch):
    """A v4 archive cannot be restored while the catalog claims v5."""
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    capture = LedgerCapture(ledger=EXPECTED["v4-sprint3-cu32-cu33"]())
    runner = _runner(tmp_path, capture)
    with pytest.raises(RuntimeError, match="ledger"):
        runner.restore_exact(
            "tenant_acme", tmp_path / "archive.dump",
            expected_schema_version="v5-sprint4-cu36",
        )


def test_rollback_target_uses_the_previous_version_ledger(tmp_path):
    """Rollback verifies the pre-restore backup against its own version."""
    capture = LedgerCapture(ledger=EXPECTED["v4-sprint3-cu32-cu33"]())
    runner = _runner(tmp_path, capture)
    assert runner.verify_migration_ledger(
        "tenant_acme", "v4-sprint3-cu32-cu33",
    ) is True
    assert runner.verify_migration_ledger(
        "tenant_acme", "v5-sprint4-cu36",
    ) is False


def test_verify_migration_ledger_reads_the_real_checksum_column(tmp_path):
    """The ledger column is ``checksum_sha256`` (bootstrap + migrator)."""
    capture = LedgerCapture(ledger=EXPECTED["v7-sprint5-notificaciones-push"]())
    runner = _runner(tmp_path, capture)
    assert runner.verify_migration_ledger("tenant_acme", None) is True
    query = next(
        " ".join(argv) for argv in capture.calls if "SELECT version ||" in " ".join(argv)
    )
    assert "checksum_sha256" in query
    assert "|| checksum " not in query + " "


def test_v1_has_no_ledger_and_stays_ledgerless_only():
    assert TenantRestoreRunner._expected_ledger("v1") is None
    assert TenantRestoreRunner.is_known_schema_version("v1") is True
    assert TenantRestoreRunner.supports_archive_version("v1", has_ledger=False) is True
    assert TenantRestoreRunner.supports_archive_version("v1", has_ledger=True) is False
