"""Guarded restore rehearsal entry point.

Modes:
    --prepare --backup-id N     Read-only plan; creates nothing. Derives the
                                deterministic probe name an --execute must confirm.
    --dry-run                   Explicit alias of --prepare.
    --execute --backup-id N --probe P --confirm P
                                Runs the full rehearsal against a disposable probe
                                database. Requires SAAS_RESTORE_REHEARSAL_ALLOW=1
                                and an exact probe/confirm match.
    --cleanup --probe P --confirm P
                                Drops a preserved probe database after an exact
                                confirmation.

Exit codes:
    0  EXIT_OK        success.
    2  EXIT_CONFIG    usage/configuration error before any database work.
    3  EXIT_INTERNAL  unexpected execution failure.
    4  EXIT_REFUSED   a guard or prohibition stopped the operation.

This script never prints a password, host or DATABASE_URL. On failure it prints
a fixed sanitized message plus one sanitized JSON diagnostic line carrying the
exact failed stage, exception class, optional SQLSTATE/returncode and a scrubbed
message, so the next run identifies the failing stage without leaking secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_INTERNAL = 3
EXIT_REFUSED = 4
ALLOW_ENV = "SAAS_RESTORE_REHEARSAL_ALLOW"

PLAN_STEPS = (
    "confirmation",
    "guard",
    "archive",
    "list_archive",
    "create_probe_database",
    "preflight_public_schema",
    "reset_public_schema",
    "restore_target",
    "fingerprint_before",
    "pre_restore",
    "simulate_mutation",
    "fingerprint_after_mutation",
    "rollback",
    "fingerprint_after_rollback",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rehearse_restore",
        description="Rehearse a tenant restore against a disposable probe database.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true",
                      help="Print the read-only plan and the deterministic probe name.")
    mode.add_argument("--dry-run", action="store_true",
                      help="Explicit alias of --prepare.")
    mode.add_argument("--execute", action="store_true",
                      help="Run the rehearsal against the prepared probe database.")
    mode.add_argument("--cleanup", action="store_true",
                      help="Drop a preserved probe database after exact confirmation.")
    parser.add_argument("--backup-id", type=int, default=None,
                        help="Backup identifier (prepare/execute).")
    parser.add_argument("--probe", default=None,
                        help="Probe database name (required for --execute and --cleanup).")
    parser.add_argument("--confirm", default=None,
                        help="Exact probe name confirmation (required for --execute and --cleanup).")
    parser.add_argument("--pg-bin-dir", default=None, help="Directory containing psql.")
    return parser


def _read_context(backup_id):
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.modules.administracion_saas.models import TenantDatabase
    from app.modules.administracion_saas.restore_service import (
        _load_backup,
        _load_mapping,
        _require_valid_backup,
    )

    with SessionLocal() as db:
        backup = _load_backup(db, backup_id)
        empresa, tenant = _load_mapping(db, backup)
        _require_valid_backup(backup, tenant)
        databases = frozenset(db.scalars(select(TenantDatabase.database_name)).all())
        return {
            "empresa_codigo": empresa.codigo,
            "tenant_database": tenant.database_name,
            "storage_key": backup.storage_key,
            "size_bytes": backup.size_bytes,
            "sha256": backup.sha256,
            "version_schema": backup.version_schema,
            "tenant_databases": databases,
        }


def _tenant_databases() -> frozenset[str]:
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.modules.administracion_saas.models import TenantDatabase

    with SessionLocal() as db:
        return frozenset(db.scalars(select(TenantDatabase.database_name)).all())


def _build_dependencies():
    from app.modules.administracion_saas.restore_rehearsal import (
        PostgresRehearsalDatabase,
        RehearsalDependencies,
    )
    from app.modules.administracion_saas.restore_service import build_restore_dependencies

    restore_deps = build_restore_dependencies()
    database = PostgresRehearsalDatabase(
        restore_deps.backend.connection,
        pg_bin_dir=os.getenv("SAAS_BACKUP_PG_BIN_DIR"),
    )
    return RehearsalDependencies(
        backend=restore_deps.backend,
        backup_runner=restore_deps.backup_runner,
        storage=restore_deps.storage,
        database=database,
    )


def _report_failure(exc: BaseException, default_stage: str) -> None:
    """Print a fixed message plus one sanitized JSON diagnostic to stderr."""
    from app.modules.administracion_saas import restore_rehearsal

    diagnostic = getattr(exc, "diagnostic", None)
    stage = getattr(exc, "stage", None) or default_stage
    if diagnostic is None:
        diagnostic = restore_rehearsal.build_rehearsal_diagnostic(exc, stage)
    payload = {"error": "restore_rehearsal_failed"}
    payload.update(diagnostic.to_dict())
    print("Restore rehearsal failed", file=sys.stderr)
    print(json.dumps(payload, sort_keys=True), file=sys.stderr)


def _run_prepare(args) -> int:
    from app.modules.administracion_saas import restore_rehearsal

    if args.backup_id is None or args.backup_id <= 0:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        context = _read_context(args.backup_id)
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        deps = _build_dependencies()
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        probe = restore_rehearsal.build_prepared_probe_name(
            context["tenant_database"], args.backup_id)
    except restore_rehearsal.RehearsalRefused:
        print("Restore rehearsal refused", file=sys.stderr)
        return EXIT_REFUSED
    try:
        exists = deps.backend.database_exists(probe)
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    if exists:
        print("Restore rehearsal refused", file=sys.stderr)
        return EXIT_REFUSED

    payload = {
        "mode": "prepare",
        "backup_id": args.backup_id,
        "empresa_codigo": context["empresa_codigo"],
        "tenant_database": context["tenant_database"],
        "probe_database": probe,
        "size_bytes": context["size_bytes"],
        "sha256": context["sha256"],
        "version_schema": context["version_schema"],
        "steps": list(PLAN_STEPS),
        "execute_requires": ["--probe " + probe, "--confirm " + probe],
        "allow_env": ALLOW_ENV,
    }
    print(json.dumps(payload, sort_keys=True))
    return EXIT_OK


def _run_execute(args) -> int:
    from app.modules.administracion_saas import restore_rehearsal

    if (os.getenv(ALLOW_ENV) or "").strip() != "1":
        print("Restore rehearsal refused: explicit allow flag is not set", file=sys.stderr)
        return EXIT_REFUSED
    probe = (args.probe or "").strip()
    confirm = (args.confirm or "").strip()
    if not probe or not confirm or probe != confirm:
        print("Restore rehearsal refused: --probe and --confirm must match", file=sys.stderr)
        return EXIT_REFUSED
    if args.backup_id is None or args.backup_id <= 0:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        context = _read_context(args.backup_id)
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        deps = _build_dependencies()
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        expected = restore_rehearsal.build_prepared_probe_name(
            context["tenant_database"], args.backup_id)
        restore_rehearsal.assert_disposable_probe(
            probe, tenant_databases=context["tenant_databases"])
    except restore_rehearsal.RehearsalRefused:
        print("Restore rehearsal refused", file=sys.stderr)
        return EXIT_REFUSED
    if probe != expected:
        print("Restore rehearsal refused: probe does not match the prepared name",
              file=sys.stderr)
        return EXIT_REFUSED
    try:
        if deps.backend.database_exists(probe):
            print("Restore rehearsal refused: probe database already exists", file=sys.stderr)
            return EXIT_REFUSED
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG

    print(probe)
    workdir = Path(tempfile.mkdtemp(prefix="saas-restore-rehearsal-"))
    try:
        report = restore_rehearsal.rehearse_restore(
            deps,
            backup_id=args.backup_id,
            storage_key=context["storage_key"],
            size_bytes=context["size_bytes"],
            sha256=context["sha256"],
            probe_database=probe,
            tenant_databases=context["tenant_databases"],
            confirmation=probe,
            workdir=workdir,
        )
    except restore_rehearsal.RehearsalRefused:
        print("Restore rehearsal refused", file=sys.stderr)
        return EXIT_REFUSED
    except restore_rehearsal.RehearsalExecutionError as exc:
        _report_failure(exc, "execute")
        return EXIT_INTERNAL
    except Exception as exc:
        _report_failure(exc, "execute")
        return EXIT_INTERNAL
    print(json.dumps(report.to_dict(), sort_keys=True))
    return EXIT_OK


def _run_cleanup(args) -> int:
    from app.modules.administracion_saas import restore_rehearsal

    if not args.probe or args.probe != args.confirm:
        print("Restore rehearsal refused", file=sys.stderr)
        return EXIT_REFUSED
    try:
        deps = _build_dependencies()
        databases = _tenant_databases()
    except Exception:
        print("Restore rehearsal configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG
    try:
        restore_rehearsal.cleanup_probe(
            deps, probe_database=args.probe,
            tenant_databases=databases, confirmation=args.confirm,
        )
    except restore_rehearsal.RehearsalRefused:
        print("Restore rehearsal refused", file=sys.stderr)
        return EXIT_REFUSED
    except restore_rehearsal.RehearsalExecutionError as exc:
        _report_failure(exc, "cleanup")
        return EXIT_INTERNAL
    except Exception as exc:
        _report_failure(exc, "cleanup")
        return EXIT_INTERNAL
    print(json.dumps({"probe_database": args.probe, "dropped": True}, sort_keys=True))
    return EXIT_OK


def main(argv=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_CONFIG

    if args.pg_bin_dir:
        os.environ["SAAS_BACKUP_PG_BIN_DIR"] = args.pg_bin_dir

    if args.prepare or args.dry_run:
        return _run_prepare(args)
    if args.execute:
        return _run_execute(args)
    return _run_cleanup(args)


if __name__ == "__main__":
    raise SystemExit(main())
