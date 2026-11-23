"""Guardrails for F-1/B-1: coherent, non-wildcard forwarded headers.

Static checks keep the deployment files consistent; the executable checks run
the real POSIX launcher with a fake ``python`` so the shell logic (unset vs
empty, wildcard rejection, argument passing) is actually exercised.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "docker" / "run-api.sh"
FAKE_PYTHON = """#!/bin/sh
printf '%s\\n' "$@" > "$ARGV_OUT"
"""


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _posix_sh():
    """Locate a POSIX shell (MSYS/Git for Windows, /bin/sh or bash elsewhere)."""
    candidates = [
        shutil.which("sh"),
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\sh.exe",
        r"C:\Program Files\Git\usr\bin\sh.exe",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        # ``bash.exe`` in System32 is the WSL relay, not a usable POSIX shell.
        if "system32" in str(candidate).casefold():
            continue
        return candidate
    return None


POSIX_SH = _posix_sh()
requires_sh = pytest.mark.skipif(
    POSIX_SH is None, reason="No POSIX shell available to execute the launcher",
)

UNSET = object()


def run_launcher(tmp_path, forwarded=UNSET):
    """Execute the launcher with a fake python; return (code, stderr, argv)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "python"
    fake.write_bytes(FAKE_PYTHON.encode("utf-8"))
    fake.chmod(0o755)
    argv_out = tmp_path / "argv.txt"
    if argv_out.exists():
        argv_out.unlink()

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + ":/usr/bin:/bin"
    env["ARGV_OUT"] = str(argv_out).replace("\\", "/")
    env.pop("FORWARDED_ALLOW_IPS", None)
    if forwarded is not UNSET:
        env["FORWARDED_ALLOW_IPS"] = forwarded

    completed = subprocess.run(
        [POSIX_SH, str(LAUNCHER)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    argv = argv_out.read_text(encoding="utf-8").splitlines() if argv_out.exists() else []
    return completed.returncode, completed.stderr.strip(), argv


def test_image_and_compose_use_the_same_proxy_aware_launcher():
    dockerfile = _read("Dockerfile")
    compose = _read("docker-compose.yml")

    assert 'CMD ["sh", "/opt/app/scripts/docker/run-api.sh"]' in dockerfile
    assert "- sh\n      - /opt/app/scripts/docker/run-api.sh" in compose


def test_launcher_enables_proxy_headers_with_a_safe_default():
    launcher = _read("scripts/docker/run-api.sh")

    assert "--proxy-headers" in launcher
    assert "--forwarded-allow-ips \"$forwarded\"" in launcher
    # B-1: `${VAR-default}` keeps "unset" distinguishable from "set and empty".
    assert 'FORWARDED_ALLOW_IPS-127.0.0.1' in launcher
    assert 'FORWARDED_ALLOW_IPS:-127.0.0.1' not in launcher
    assert 'exit 1' in launcher


def test_no_wildcard_forwarded_allow_ips_in_deployment_files():
    for relative in ("Dockerfile", "docker-compose.yml", "scripts/docker/run-api.sh"):
        content = _read(relative)
        assert "--forwarded-allow-ips=*" not in content
        assert "--forwarded-allow-ips *" not in content
        assert '--forwarded-allow-ips "*"' not in content


@requires_sh
def test_launcher_executes_with_unset_variable_and_applies_default(tmp_path):
    code, stderr, argv = run_launcher(tmp_path)

    assert code == 0, stderr
    assert "--proxy-headers" in argv
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "127.0.0.1"
    assert "-m" in argv and "uvicorn" in argv and "app.main:app" in argv


@requires_sh
def test_launcher_rejects_empty_or_blank_value_without_starting_uvicorn(tmp_path):
    for value in ("", "   ", "\t"):
        code, stderr, argv = run_launcher(tmp_path, value)
        assert code == 1, (value, stderr)
        assert "empty" in stderr
        assert argv == []


@requires_sh
@pytest.mark.parametrize("value", [
    "*",
    "*.*",
    "0.0.0.0/0, *",
    "127.0.0.1,*",
])
def test_launcher_rejects_wildcard_in_any_supported_form(tmp_path, value):
    code, stderr, argv = run_launcher(tmp_path, value)

    assert code == 1, stderr
    assert "wildcard" in stderr
    assert argv == []


@requires_sh
def test_launcher_passes_explicit_proxy_cidr_to_uvicorn(tmp_path):
    code, stderr, argv = run_launcher(tmp_path, "10.20.0.0/16")

    assert code == 0, stderr
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "10.20.0.0/16"


def test_runbook_documents_retained_databases_and_drop_prohibition():
    runbook = _read("docs/RUNBOOK_RESTORE_SAAS.md")

    assert "restore_backup_" in runbook
    assert "COMPLETADO_RESPALDO_RETENIDO" in runbook
    assert "Nunca** ejecute `DROP DATABASE`" in runbook
    assert "RENOMBRE_TEMPORAL" in runbook


def test_runbook_ledger_query_uses_the_real_table():
    runbook = _read("docs/RUNBOOK_RESTORE_SAAS.md")

    assert "SELECT * FROM app_migrations.schema_migration ORDER BY version;" in runbook
    assert "SELECT * FROM app_migrations ORDER BY 1;" not in runbook
    # Real ledger columns documented for verification (M-2).
    for column in ("version", "nombre", "checksum_sha256", "aplicada_en",
                   "duracion_ms", "runner_version"):
        assert column in runbook
