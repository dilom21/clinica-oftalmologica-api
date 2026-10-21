"""External-scheduler entry point for automatic tenant backups.

This script is meant to be invoked by an external scheduler (for example a
Render Cron job). It never runs inside the web process, accepts no password
argument, and prints only fixed sanitized messages to stderr.

Exit codes:
    0  EXIT_OK       no due backups, or all due backups completed.
    1  EXIT_PARTIAL  at least one tenant failed while others continued.
    2  EXIT_CONFIG   usage/config error or unavailable configuration, before any dump.
    3  EXIT_INTERNAL unexpected internal failure during execution.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_CONFIG = 2
EXIT_INTERNAL = 3


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_automatic_backups",
        description="Run due automatic tenant backups (external scheduler entry point).",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="Print the sanitized plan without writing, dumping, or touching storage.")
    mode.add_argument("--execute", action="store_true",
                      help="Execute due automatic backups.")
    parser.add_argument("--purge-retention", action="store_true",
                        help="Purge expired automatic backups after success (execute only).")
    parser.add_argument("--pg-bin-dir", default=None,
                        help="Directory containing pg_dump/pg_restore.")
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else EXIT_CONFIG

    if args.purge_retention and not args.execute:
        print("--purge-retention requires --execute", file=sys.stderr)
        return EXIT_CONFIG
    if args.pg_bin_dir:
        os.environ["SAAS_BACKUP_PG_BIN_DIR"] = args.pg_bin_dir

    try:
        from app.database.session import SessionLocal
        from app.modules.administracion_saas import backup_automatic, backup_service
    except Exception:
        print("Automatic backup configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG

    if args.dry_run:
        try:
            plan = backup_automatic.plan_automatic_backups(SessionLocal)
        except Exception:
            print("Automatic backup configuration is unavailable", file=sys.stderr)
            return EXIT_CONFIG
        print(json.dumps(plan, sort_keys=True))
        return EXIT_OK

    try:
        runner, storage = backup_service.build_backup_dependencies()
    except Exception:
        print("Automatic backup configuration is unavailable", file=sys.stderr)
        return EXIT_CONFIG

    try:
        summary = backup_automatic.run_automatic_backups(
            SessionLocal, runner=runner, storage=storage,
            purge_retention=args.purge_retention,
        )
    except Exception:
        print("Automatic backup execution failed", file=sys.stderr)
        return EXIT_INTERNAL

    print(json.dumps(summary, sort_keys=True))
    return EXIT_PARTIAL if summary.get("fallidas") else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
