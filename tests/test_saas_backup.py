"""Isolated control-plane backup checks; no real PostgreSQL client is executed."""

import hashlib
import json
import os
import subprocess
import threading
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.core.security import crear_access_token, crear_tenant_access_token, hash_password
from app.core.tenancy.dependencies import get_control_db
from app.main import app
from app.modules.administracion_saas import backup_automatic, backup_service
from app.modules.administracion_saas.backup_automatic import (
    apply_retention, plan_automatic_backups, run_automatic_backups, select_retention_candidates)
from app.modules.administracion_saas.backup_policy import as_utc
from app.modules.administracion_saas.backup_runner import TenantBackupRunner
from app.modules.administracion_saas.backup_service import (
    BackupReservationError, create_automatic_backup, create_manual_backup)
from app.modules.administracion_saas.backup_storage import AclPolicy, LocalPrivateBackupStorage
from app.modules.administracion_saas.models import (BackupPolicy, BackupTenant, Empresa,
                                                     RestoreTenant, SaasBitacora, SaasUsuario,
                                                     Suscripcion, TenantDatabase)
from scripts.saas.provision_tenant import PostgresConnection, ProvisioningError


@compiles(INET, "sqlite")
def compile_inet_for_sqlite(_type, _compiler, **_kwargs):
    return "VARCHAR(45)"


class FakeRunner:
    def __init__(self, stage=None):
        self.stage = stage
        self.calls = []

    def dump(self, database, path):
        self.calls.append(("dump", database))
        if self.stage == "dump":
            raise RuntimeError("DATABASE_URL=postgresql://x:secret@private/db password=secret")
        path.write_bytes(b"" if self.stage == "empty" else b"PGDMP sample archive")

    def verify(self, path):
        self.calls.append(("list", path.read_bytes()))
        if self.stage == "list":
            raise RuntimeError("pg_restore password=secret")


class FakeStorage:
    def __init__(self, fail=False):
        self.files = {}
        self.fail = fail
        self.deleted = []

    def put(self, key, path):
        self.files[key] = path.read_bytes()
        if self.fail:
            raise RuntimeError("storage password=secret")

    def delete(self, key):
        self.deleted.append(key)
        self.files.pop(key, None)

    def exists(self, key):
        return key in self.files

    def get(self, key):
        return self.files[key]

    @contextmanager
    def staging(self):
        with tempfile.TemporaryDirectory(prefix="fake-backup-") as temporary:
            yield Path(temporary)

    def verify_private_archive(self, path):
        assert path.is_file()


@pytest.fixture
def setup_backup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'control.sqlite'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach(connection, _):
        connection.execute("ATTACH DATABASE ? AS saas_control", (str(tmp_path / "saas.sqlite"),))

    with engine.begin() as connection:
        for model in (Empresa, TenantDatabase, SaasUsuario, SaasBitacora, BackupTenant,
                      RestoreTenant):
            model.__table__.create(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            Empresa(id=1, codigo="ACME", slug="acme", estado="ACTIVA", nombre_comercial="Acme"),
            Empresa(id=2, codigo="BETA", slug="beta", estado="ACTIVA", nombre_comercial="Beta"),
            TenantDatabase(id=11, empresa_id=1, database_name="tenant_acme", estado="ACTIVA", version_schema="v1"),
            TenantDatabase(id=12, empresa_id=2, database_name="tenant_beta", estado="ACTIVA", version_schema="v2"),
            SaasUsuario(id=1, correo="admin@example.com", password_hash=hash_password("safe-password"),
                        nombres="Test", apellidos="Admin", rol="SUPERADMIN", estado=True),
        ])
        db.commit()

    def dependency():
        with factory() as db:
            yield db

    runner, storage = FakeRunner(), FakeStorage()
    monkeypatch.setattr(backup_service, "build_backup_dependencies", lambda: (runner, storage))
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_control_db] = dependency
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post("/saas/auth/login", json={"correo": "admin@example.com", "password": "safe-password"})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client, factory, runner, storage
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def test_success_metadata_hash_list_detail_and_audit(setup_backup):
    client, factory, runner, storage = setup_backup
    response = client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["estado"] == "COMPLETADO" and row["formato"] == "CUSTOM"
    assert row["size_bytes"] == len(b"PGDMP sample archive")
    assert row["sha256"] == hashlib.sha256(b"PGDMP sample archive").hexdigest()
    assert row["version_schema"] == "v1" and row["fecha_fin"]
    assert runner.calls[0] == ("dump", "tenant_acme") and runner.calls[1][0] == "list"
    assert len(storage.files) == 1
    listing = client.get("/saas/backups").json()[0]
    detail = client.get(f"/saas/backups/{row['id']}").json()
    assert listing["id"] == detail["id"] == row["id"]
    assert listing["sha256"] == detail["sha256"] == row["sha256"]
    assert client.get("/saas/backups?empresa_id=2").json() == []
    assert client.get("/saas/backups?estado=COMPLETADO&tipo=MANUAL").json()[0]["id"] == row["id"]
    with factory() as db:
        record = db.get(BackupTenant, row["id"])
        assert record.storage_key in storage.files
        audit = db.scalars(select(SaasBitacora).where(SaasBitacora.accion == "CREAR_BACKUP_MANUAL")).one()
        assert audit.resultado == "EXITO" and "backup_id=" in audit.descripcion
    assert "storage_key" not in row and "database_name" not in row


@pytest.mark.parametrize("token", [None, "tenant", "legacy"])
@pytest.mark.parametrize("method,path", [("post", "/saas/backups"), ("get", "/saas/backups"),
                                         ("get", "/saas/backups/1")])
def test_saas_auth_required(setup_backup, token, method, path):
    client, _, runner, _ = setup_backup
    if token == "tenant":
        credential = crear_tenant_access_token(1, 1, 11, 1, "ACME")
    elif token == "legacy":
        credential = crear_access_token(1, 1)
    else:
        credential = None
    headers = {"Authorization": f"Bearer {credential}"} if credential else {"Authorization": ""}
    response = getattr(client, method)(path, headers=headers,
                                      **({"json": {"empresa_id": 1}} if method == "post" else {}))
    assert response.status_code in (401, 403)
    assert runner.calls == []


@pytest.mark.parametrize("payload", [{"empresa_id": 1, "database_name": "tenant_beta"},
                                           {"empresa_id": 1, "storage_key": "secret"},
                                           {"empresa_id": -1}])
def test_request_rejects_untrusted_fields(setup_backup, payload):
    client, _, runner, _ = setup_backup
    assert client.post("/saas/backups", json=payload).status_code == 422
    assert runner.calls == []


@pytest.mark.parametrize("state_field,state,status", [
    ("missing", "", 404), ("company", "SUSPENDIDA", 409),
    ("tenant", "SUSPENDIDA", 409), ("database", "Bad;database", 409),
])
def test_mapping_validation_before_dump(setup_backup, state_field, state, status):
    client, factory, runner, _ = setup_backup
    with factory() as db:
        if state_field == "company":
            db.get(Empresa, 1).estado = state
        elif state_field == "tenant":
            db.get(TenantDatabase, 11).estado = state
        elif state_field == "database":
            db.get(TenantDatabase, 11).database_name = state
        db.commit()
    response = client.post("/saas/backups", json={"empresa_id": 999 if state_field == "missing" else 1})
    assert response.status_code == status
    assert runner.calls == []
    with factory() as db:
        assert db.query(BackupTenant).count() == 0


@pytest.mark.parametrize("stage", ["dump", "empty", "list"])
def test_dump_failures_mark_error_and_remove_files(setup_backup, stage):
    client, factory, runner, storage = setup_backup
    runner.stage = stage
    response = client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 503
    assert "secret" not in response.text and "postgres" not in response.text
    with factory() as db:
        record = db.scalars(select(BackupTenant)).one()
        assert record.estado == "ERROR" and record.fecha_fin and record.storage_key is None
        assert "secret" not in record.mensaje_error
        assert db.get(TenantDatabase, 11).estado == "ACTIVA"
        audit = db.scalars(select(SaasBitacora).where(SaasBitacora.accion == "CREAR_BACKUP_MANUAL")).one()
        assert audit.resultado == "ERROR" and "secret" not in audit.descripcion
    assert storage.files == {} and len(storage.deleted) == 1


def test_storage_partial_write_failure_cleans_and_marks_error(setup_backup):
    client, factory, _, storage = setup_backup
    storage.fail = True
    assert client.post("/saas/backups", json={"empresa_id": 1}).status_code == 503
    assert storage.files == {}
    with factory() as db:
        assert db.scalars(select(BackupTenant.estado)).one() == "ERROR"


def test_unique_reservation_blocks_same_tenant_across_sessions_and_allows_other(setup_backup):
    _, factory, _, _ = setup_backup
    started = threading.Event()
    release = threading.Event()

    class SlowRunner(FakeRunner):
        def dump(self, database, path):
            if database == "tenant_acme":
                started.set()
                assert release.wait(10)
            super().dump(database, path)

    runner, storage = SlowRunner(), FakeStorage()
    outcomes = []

    def first():
        try:
            outcomes.append(create_manual_backup(factory, 1, 1, runner=runner, storage=storage).estado)
        except Exception as exc:
            outcomes.append(type(exc).__name__)

    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert started.wait(10)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            create_manual_backup(factory, 1, 1, runner=runner, storage=storage)
        assert exc.value.status_code == 409
        assert create_manual_backup(factory, 2, 1, runner=runner, storage=storage).estado == "COMPLETADO"
        with factory() as db:
            assert db.scalars(select(BackupTenant.estado).where(BackupTenant.empresa_id == 1)).one() == "EN_PROCESO"
    finally:
        release.set()
        thread.join(10)
    assert outcomes == ["COMPLETADO"]
    assert [call for call in runner.calls if call == ("dump", "tenant_acme")] == [("dump", "tenant_acme")]


def test_runner_full_dump_no_secrets_and_read_only_list(tmp_path, monkeypatch):
    for name in ("pg_dump", "pg_restore"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    calls = []

    class Capture:
        def run(self, argv, env, timeout):
            calls.append((argv, env, timeout))

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:database-secret@private/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "api-secret")
    runner = TenantBackupRunner(PostgresConnection("private", 5432, "admin", "SECRET"),
                                str(tmp_path), 17, Capture())
    path = tmp_path / "archive.dump"
    runner.dump("tenant_acme", path)
    runner.verify(path)
    assert len(calls) == 2
    dump, env, timeout = calls[0]
    assert timeout == 17 and "--format=custom" in dump
    assert {"--no-owner", "--no-privileges"} <= set(dump)
    assert "--schema-only" not in dump and "--schema=public" not in dump
    assert dump[dump.index("-d") + 1] == "tenant_acme"
    assert "SECRET" not in " ".join(dump) and env["PGPASSWORD"] == "SECRET"
    assert calls[1][0][1:] == ["--list", str(path)]
    assert "--dbname" not in calls[1][0]
    for _, child_env, _ in calls:
        assert child_env["PGPASSWORD"] == "SECRET"
        assert child_env["PGSSLMODE"] == "require"
        assert not ({"DATABASE_URL", "JWT_SECRET_KEY", "SMTP_PASSWORD", "DEEPSEEK_API_KEY"} & child_env.keys())
        assert "PATH" not in child_env


@pytest.mark.parametrize(("windows", "required"), [
    (True, {"SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP"}),
    (False, {"LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP"}),
])
def test_runner_minimal_platform_environment_for_dump_and_verify(tmp_path, monkeypatch, windows, required):
    from app.modules.administracion_saas import backup_runner
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: windows)
    for key in required:
        monkeypatch.setenv(key, f"runtime-{key}")
    for key in ("DATABASE_URL", "JWT_SECRET_KEY", "SMTP_PASSWORD", "DEEPSEEK_API_KEY", "PATH"):
        monkeypatch.setenv(key, f"forbidden-{key}")
    suffix = ".exe" if windows else ""
    for name in ("pg_dump", "pg_restore"):
        candidate = tmp_path / f"{name}{suffix}"
        candidate.write_bytes(b"not executed")
        candidate.chmod(0o755)
    monkeypatch.setattr(backup_runner.os, "access", lambda path, mode: True)
    calls = []
    class Capture:
        def run(self, argv, env, timeout):
            calls.append(env)
    runner = TenantBackupRunner(
        PostgresConnection("private", 5432, "admin", "PASSWORD", sslmode="verify-full"),
        str(tmp_path), runner=Capture(),
    )
    archive = tmp_path / "archive.dump"
    runner.dump("tenant_acme", archive)
    runner.verify(archive)
    expected = {key.casefold() for key in required | {"PGPASSWORD", "PGSSLMODE"}}
    assert len(calls) == 2
    assert all({key.casefold() for key in env} == expected for env in calls)
    assert all(env["PGPASSWORD"] == "PASSWORD" and env["PGSSLMODE"] == "verify-full" for env in calls)


def test_posix_explicit_pg_bin_dir_uses_executable_without_suffix(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: False)
    for name in ("pg_dump", "pg_restore"):
        candidate = tmp_path / name
        candidate.write_bytes(b"not executed")
        candidate.chmod(0o755)
    monkeypatch.setattr(backup_runner.os, "access", lambda path, mode: True)
    runner = TenantBackupRunner(PostgresConnection("private", 5432, "user", "SECRET"), str(tmp_path))
    assert runner.pg_dump == tmp_path / "pg_dump"
    assert runner.pg_restore == tmp_path / "pg_restore"
    assert backup_runner._client_path("missing", str(tmp_path)) is None


def test_posix_explicit_pg_bin_dir_rejects_nonexecutable(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: False)
    for name in ("pg_dump", "pg_restore"):
        (tmp_path / name).write_bytes(b"not executed")
    monkeypatch.setattr(backup_runner.os, "access", lambda path, mode: False)
    with pytest.raises(RuntimeError, match="clients are unavailable"):
        TenantBackupRunner(PostgresConnection("private", 5432, "user", "SECRET"), str(tmp_path))


@pytest.mark.parametrize("failure", ["timeout", "client"])
def test_runner_subprocess_failure_is_controlled(tmp_path, failure):
    import subprocess
    from scripts.saas.provision_tenant import SubprocessRunner
    def raise_failure(*_args, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("pg_dump", 1)
        raise subprocess.CalledProcessError(1, "pg_dump", stderr="password=SECRET")
    from unittest.mock import patch
    with patch("scripts.saas.provision_tenant.subprocess.run", side_effect=raise_failure):
        with pytest.raises(ProvisioningError):
            SubprocessRunner().run(["pg_dump"], {"PGPASSWORD": "SECRET"}, 1)


def test_local_storage_stays_private_and_rejects_traversal(tmp_path, monkeypatch):
    if os.name == "nt":
        from app.modules.administracion_saas import backup_storage
        monkeypatch.setattr(backup_storage, "_inspect_windows_acl", lambda *args, **kwargs: None)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    storage = LocalPrivateBackupStorage(root)
    key = "a" * 32 + ".dump"
    with storage.staging() as directory:
        assert directory.parent == root
        source = directory / "tenant.dump"
        source.write_bytes(b"private archive")
        storage.verify_private_archive(source)
        storage.put(key, source)
    assert not directory.exists()
    assert storage.exists(key)
    with storage.get(key) as handle:
        assert handle.read() == b"private archive"
    with pytest.raises(ValueError):
        storage.delete("../public.dump")
    storage.delete(key)
    assert not storage.exists(key)


def test_windows_local_provider_accepts_only_verified_acl(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage
    monkeypatch.setattr(backup_storage, "_is_windows", lambda: True)
    inspected = []
    monkeypatch.setattr(backup_storage, "_inspect_windows_acl",
                        lambda path, *, policy, directory: inspected.append((path, policy, directory)))
    storage = LocalPrivateBackupStorage(tmp_path)
    with storage.staging() as directory:
        file = directory / "tenant.dump"
        file.write_bytes(b"fake backup")
        storage.put("b" * 32 + ".dump", file)
    assert (tmp_path, AclPolicy.ROOT, True) in inspected
    assert (directory, AclPolicy.CHILD, True) in inspected
    assert (file, AclPolicy.CHILD, False) in inspected
    assert any(path.suffix == ".partial" and policy is AclPolicy.CHILD and not is_dir
               for path, policy, is_dir in inspected)
    assert not directory.exists()


def test_windows_acl_check_rejects_broad_unknown_unparseable_and_missing_tool(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage
    from types import SimpleNamespace
    monkeypatch.setattr(backup_storage, "_is_windows", lambda: True)
    monkeypatch.setattr(backup_storage, "_powershell_path", lambda: Path("powershell.exe"))
    calls = []
    temp_scripts = []

    def result(argv, **kwargs):
        script = Path(argv[-1])
        calls.append((argv, kwargs))
        temp_scripts.append((script, script.read_text(encoding="utf-8")))
        return SimpleNamespace(returncode=0, stdout="PRIVATE_ACL_OK", stderr="")

    monkeypatch.setattr(backup_storage.subprocess, "run", result)
    LocalPrivateBackupStorage(tmp_path)
    argv, kwargs = calls[0]
    assert argv[-2] == "-File" and argv[-1].endswith(".ps1")
    assert "-Command" not in argv and "input" not in kwargs
    assert str(tmp_path) not in " ".join(argv)
    assert kwargs["env"]["SAAS_BACKUP_ACL_TARGET"] == str(tmp_path)
    assert kwargs["env"]["SAAS_BACKUP_ACL_DIRECTORY"] == "1"
    assert kwargs["env"]["SAAS_BACKUP_ACL_REQUIRE_PROTECTED"] == "1"
    assert "DATABASE_URL" not in kwargs["env"]
    script = temp_scripts[0][1]
    assert script == backup_storage._WINDOWS_ACL_CHECK
    assert "RawSecurityDescriptor" in script and "DiscretionaryAcl" in script
    assert "AreAccessRulesProtected" in script and "DACL inheritance is enabled" in script
    assert "-notin $allowed" in script and "AccessAllowed" in script
    assert "S-1-5-18" in script and "S-1-5-32-544" in script
    for output in (SimpleNamespace(returncode=7, stdout="", stderr="broad ACE"),
                   SimpleNamespace(returncode=0, stdout="", stderr=""),
                   SimpleNamespace(returncode=0, stdout="unexpected", stderr="")):
        monkeypatch.setattr(backup_storage.subprocess, "run", lambda *args, **kwargs: output)
        with pytest.raises(ValueError, match="not private"):
            LocalPrivateBackupStorage(tmp_path)
    assert all(not candidate.exists() for candidate, _ in temp_scripts)
    monkeypatch.setattr(backup_storage, "_powershell_path", lambda: None)
    with pytest.raises(RuntimeError, match="unavailable"):
        LocalPrivateBackupStorage(tmp_path)


def test_windows_acl_transport_uses_temp_file_and_multiline_script(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage
    from types import SimpleNamespace
    monkeypatch.setattr(backup_storage, "_powershell_path", lambda: Path("powershell.exe"))
    captured = {}

    def result(argv, **kwargs):
        script = Path(argv[-1])
        captured["argv"], captured["kwargs"] = argv, kwargs
        captured["path"], captured["existed"] = script, script.is_file()
        captured["script"] = script.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="PRIVATE_ACL_OK", stderr="")

    monkeypatch.setattr(backup_storage.subprocess, "run", result)
    backup_storage._inspect_windows_acl(tmp_path, policy=AclPolicy.ROOT, directory=True)
    assert captured["argv"][1:4] == ["-NoProfile", "-NonInteractive", "-File"]
    assert "-Command" not in captured["argv"] and "input" not in captured["kwargs"]
    assert captured["existed"] and not captured["path"].exists()
    script = captured["script"]
    assert "\n" in script and "try {" in script and "catch {" in script and "exit 7" in script
    assert captured["kwargs"]["env"]["SAAS_BACKUP_ACL_DIRECTORY"] == "1"
    assert captured["kwargs"]["env"]["SAAS_BACKUP_ACL_REQUIRE_PROTECTED"] == "1"


def test_windows_acl_script_uses_integer_aceflags_comparisons():
    from app.modules.administracion_saas import backup_storage
    script = backup_storage._WINDOWS_ACL_CHECK
    for flag in ("ContainerInherit", "ObjectInherit", "InheritOnly"):
        assert f"([int]$ace.AceFlags -band [int][Security.AccessControl.AceFlags]::{flag})" in script
    assert "$ace.AceFlags -band [Security.AccessControl.AceFlags]" not in script
    assert "[int]$ace.AccessMask -band [int]0x1f01ff" in script


def test_windows_acl_inspector_execution_failure_is_fail_closed(tmp_path, monkeypatch):
    import subprocess
    from app.modules.administracion_saas import backup_storage
    monkeypatch.setattr(backup_storage, "_is_windows", lambda: True)
    monkeypatch.setattr(backup_storage, "_powershell_path", lambda: Path("powershell.exe"))
    monkeypatch.setattr(
        backup_storage.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("powershell", 15)),
    )
    with pytest.raises(RuntimeError, match="inspection failed"):
        LocalPrivateBackupStorage(tmp_path)


_WINDOWS_POWERSHELL = Path(
    os.environ.get("SystemRoot", r"C:\Windows")
) / "System32/WindowsPowerShell/v1.0/powershell.exe"

real_windows_acl = pytest.mark.skipif(
    os.name != "nt" or not _WINDOWS_POWERSHELL.is_file(),
    reason="real Windows PowerShell ACL test (Windows only)",
)

_SET_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$path = $env:ACL_TEST_TARGET
$serviceSid = New-Object Security.Principal.SecurityIdentifier([Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($existing in @($acl.Access)) { [void]$acl.RemoveAccessRuleAll($existing) }
$inherit = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
foreach ($sid in @($serviceSid.Value, 'S-1-5-18', 'S-1-5-32-544')) {
    $identity = New-Object Security.Principal.SecurityIdentifier($sid)
    $full = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inherit, [Security.AccessControl.PropagationFlags]::None, 'Allow')
    $acl.AddAccessRule($full)
}
if (-not [string]::IsNullOrEmpty($env:ACL_TEST_EXTRA_SID)) {
    $broadSid = New-Object Security.Principal.SecurityIdentifier($env:ACL_TEST_EXTRA_SID)
    $broad = New-Object Security.AccessControl.FileSystemAccessRule($broadSid, 'Read', 'Allow')
    $acl.AddAccessRule($broad)
}
Set-Acl -LiteralPath $path -AclObject $acl
"""


# Adds one explicit (non-inherited) ACE to a child that already has an inherited,
# safe DACL. Used to prove the CHILD policy still rejects broad/unknown SIDs while
# accepting an unprotected but exclusively private inherited ACL.
_ADD_CHILD_ACE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$path = $env:ACL_TEST_TARGET
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($false, $true)
$sid = New-Object Security.Principal.SecurityIdentifier($env:ACL_TEST_EXTRA_SID)
$rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'Read', 'Allow')
$acl.AddAccessRule($rule)
Set-Acl -LiteralPath $path -AclObject $acl
"""


_QUERY_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:ACL_TEST_TARGET
$protected = $acl.AreAccessRulesProtected
$owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
[Console]::Out.Write("protected=$protected;owner=$owner")
"""


_CREATE_JUNCTION_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
New-Item -ItemType Junction -Path $env:ACL_TEST_JUNCTION -Target $env:ACL_TEST_JUNCTION_TARGET | Out-Null
"""


def _run_acl_powershell(script_text: str, *, target: Path, extra_sid: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ACL_TEST_TARGET"] = str(target)
    env["ACL_TEST_EXTRA_SID"] = extra_sid
    descriptor, name = tempfile.mkstemp(prefix="saas-acl-test-", suffix=".ps1")
    os.close(descriptor)
    script = Path(name)
    try:
        script.write_text(script_text, encoding="utf-8")
        return subprocess.run(
            [str(_WINDOWS_POWERSHELL), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(script)],
            text=True, capture_output=True, env=env, timeout=30, check=False,
        )
    finally:
        script.unlink(missing_ok=True)


def _apply_real_acl(target: Path, *, extra_sid: str = "") -> None:
    completed = _run_acl_powershell(_SET_ACL_SCRIPT, target=target, extra_sid=extra_sid)
    assert completed.returncode == 0, completed.stderr


def _add_child_ace(target: Path, *, extra_sid: str) -> None:
    completed = _run_acl_powershell(_ADD_CHILD_ACE_SCRIPT, target=target, extra_sid=extra_sid)
    assert completed.returncode == 0, completed.stderr


def _read_real_acl(target: Path) -> tuple[bool, str]:
    completed = _run_acl_powershell(_QUERY_ACL_SCRIPT, target=target)
    assert completed.returncode == 0, completed.stderr
    fields = dict(part.split("=", 1) for part in completed.stdout.strip().split(";"))
    return fields["protected"] == "True", fields["owner"]


def _create_junction(link: Path, target: Path) -> bool:
    env = dict(os.environ)
    env["ACL_TEST_JUNCTION"] = str(link)
    env["ACL_TEST_JUNCTION_TARGET"] = str(target)
    descriptor, name = tempfile.mkstemp(prefix="saas-junction-", suffix=".ps1")
    os.close(descriptor)
    script = Path(name)
    try:
        script.write_text(_CREATE_JUNCTION_SCRIPT, encoding="utf-8")
        completed = subprocess.run(
            [str(_WINDOWS_POWERSHELL), "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(script)],
            text=True, capture_output=True, env=env, timeout=30, check=False,
        )
        return completed.returncode == 0
    finally:
        script.unlink(missing_ok=True)


def _real_acl_transport_result(target: Path, *, directory: bool,
                               require_protected: bool = True) -> subprocess.CompletedProcess:
    from app.modules.administracion_saas import backup_storage
    executable = backup_storage._powershell_path()
    assert executable is not None
    env = {key: value for key, value in os.environ.items()
           if key.casefold() in {"systemroot", "windir", "comspec", "psmodulepath", "temp", "tmp"}}
    env["SAAS_BACKUP_ACL_TARGET"] = str(target)
    env["SAAS_BACKUP_ACL_DIRECTORY"] = "1" if directory else "0"
    env["SAAS_BACKUP_ACL_REQUIRE_PROTECTED"] = "1" if require_protected else "0"
    return backup_storage._run_windows_acl_check(executable, env)


@real_windows_acl
def test_real_windows_inspector_accepts_private_acl(tmp_path):
    from app.modules.administracion_saas import backup_storage
    target = tmp_path / "private-root"
    target.mkdir()
    _apply_real_acl(target)
    result = _real_acl_transport_result(target, directory=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PRIVATE_ACL_OK"
    backup_storage._inspect_windows_acl(target, policy=AclPolicy.ROOT, directory=True)
    LocalPrivateBackupStorage(target)


@real_windows_acl
def test_real_windows_inspector_rejects_broad_acl(tmp_path):
    from app.modules.administracion_saas import backup_storage
    target = tmp_path / "broad-root"
    target.mkdir()
    _apply_real_acl(target, extra_sid="S-1-1-0")  # Everyone
    result = _real_acl_transport_result(target, directory=True)
    assert result.returncode == 7
    assert result.stdout.strip() != "PRIVATE_ACL_OK"
    with pytest.raises(ValueError, match="not private"):
        backup_storage._inspect_windows_acl(target, policy=AclPolicy.ROOT, directory=True)


@real_windows_acl
def test_real_windows_child_directory_inherited_secure_is_accepted(tmp_path):
    from app.modules.administracion_saas import backup_storage
    root = tmp_path / "root"
    root.mkdir()
    _apply_real_acl(root)
    child = root / "staging"
    child.mkdir()  # inherits the private root DACL (unprotected)
    protected, _ = _read_real_acl(child)
    assert protected is False
    backup_storage._inspect_windows_acl(child, policy=AclPolicy.CHILD, directory=True)


@real_windows_acl
def test_real_windows_child_file_inherited_secure_is_accepted(tmp_path):
    from app.modules.administracion_saas import backup_storage
    root = tmp_path / "root"
    root.mkdir()
    _apply_real_acl(root)
    child = root / "tenant.dump"
    child.write_bytes(b"private archive")
    protected, _ = _read_real_acl(child)
    assert protected is False
    backup_storage._inspect_windows_acl(child, policy=AclPolicy.CHILD, directory=False)


@real_windows_acl
def test_real_windows_partial_and_final_child_files_are_accepted(tmp_path):
    from app.modules.administracion_saas import backup_storage
    root = tmp_path / "root"
    root.mkdir()
    _apply_real_acl(root)
    partial = root / ".0123456789abcdef0123456789abcdef.partial"
    partial.write_bytes(b"partial")
    final = root / ("d" * 32 + ".dump")
    final.write_bytes(b"final")
    for path in (partial, final):
        protected, _ = _read_real_acl(path)
        assert protected is False
        backup_storage._inspect_windows_acl(path, policy=AclPolicy.CHILD, directory=False)


@real_windows_acl
@pytest.mark.parametrize("extra_sid", [
    "S-1-1-0",      # Everyone
    "S-1-5-32-545",  # BUILTIN\Users
    "S-1-5-11",      # Authenticated Users
    "S-1-5-21-111111111-222222222-333333333-4444",  # unknown SID
])
def test_real_windows_child_with_broad_or_unknown_sid_is_rejected(tmp_path, extra_sid):
    from app.modules.administracion_saas import backup_storage
    root = tmp_path / "root"
    root.mkdir()
    _apply_real_acl(root)
    child = root / "child"
    child.mkdir()
    _add_child_ace(child, extra_sid=extra_sid)
    result = _real_acl_transport_result(child, directory=True, require_protected=False)
    assert result.returncode == 7
    with pytest.raises(ValueError, match="not private"):
        backup_storage._inspect_windows_acl(child, policy=AclPolicy.CHILD, directory=True)


@real_windows_acl
def test_real_windows_child_outside_verified_root_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _apply_real_acl(root)
    storage = LocalPrivateBackupStorage(root)
    outside = tmp_path / "outside.dump"
    outside.write_bytes(b"outside")
    with pytest.raises(ValueError, match="private root"):
        storage.verify_private_archive(outside)


@real_windows_acl
def test_real_windows_child_reparse_escape_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    _apply_real_acl(root)
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "escape.dump").write_bytes(b"escape")
    link = root / "escape-link"
    if not _create_junction(link, outside):
        pytest.skip("junction could not be created on this host")
    try:
        storage = LocalPrivateBackupStorage(root)
        with pytest.raises(ValueError, match="private root"):
            storage.verify_private_archive(link / "escape.dump")
    finally:
        if link.exists() or link.is_symlink():
            os.rmdir(link)


@real_windows_acl
def test_real_windows_unprotected_root_is_rejected(tmp_path):
    from app.modules.administracion_saas import backup_storage
    outer = tmp_path / "safe-outer"
    outer.mkdir()
    _apply_real_acl(outer)
    unprotected_root = outer / "unprotected-root"
    unprotected_root.mkdir()  # inherits a private but unprotected DACL
    protected, _ = _read_real_acl(unprotected_root)
    assert protected is False
    with pytest.raises(ValueError, match="not private"):
        backup_storage._inspect_windows_acl(unprotected_root, policy=AclPolicy.ROOT, directory=True)
    with pytest.raises(ValueError, match="not private"):
        LocalPrivateBackupStorage(unprotected_root)


@real_windows_acl
def test_real_windows_inherited_child_regression_incident(tmp_path):
    """Reproduces backup_id=1: protected root, unprotected child with a private ACL.

    Before the fix the child was inspected with the ROOT policy and rejected;
    after the fix the CHILD policy accepts the same inherited, private ACL.
    """
    from app.modules.administracion_saas import backup_storage
    root = tmp_path / "incident-root"
    root.mkdir()
    _apply_real_acl(root)
    child = root / "staging"
    child.mkdir()
    protected, _ = _read_real_acl(child)
    assert protected is False
    with pytest.raises(ValueError, match="not private"):  # BEFORE
        backup_storage._inspect_windows_acl(child, policy=AclPolicy.ROOT, directory=True)
    backup_storage._inspect_windows_acl(child, policy=AclPolicy.CHILD, directory=True)  # AFTER
    assert _real_acl_transport_result(child, directory=True, require_protected=False).returncode == 0


@real_windows_acl
def test_real_windows_storage_full_cycle_with_inherited_acl(tmp_path):
    """Real Windows storage cycle over a private root with inherited child ACLs."""
    root = tmp_path / "private-root"
    root.mkdir()
    _apply_real_acl(root)
    storage = LocalPrivateBackupStorage(root)
    key = "c" * 32 + ".dump"
    with storage.staging() as directory:
        assert directory.parent == root
        protected, _ = _read_real_acl(directory)
        assert protected is False
        source = directory / "tenant.dump"
        source.write_bytes(b"private archive")
        storage.verify_private_archive(source)
        storage.put(key, source)
    assert not directory.exists()
    assert storage.exists(key)
    with storage.get(key) as handle:
        assert handle.read() == b"private archive"
    storage.delete(key)
    assert not storage.exists(key)


def test_windows_acl_inspection_failure_before_dump_has_no_reservation(setup_backup, tmp_path, monkeypatch):
    client, factory, runner, _ = setup_backup
    from app.modules.administracion_saas import backup_storage
    monkeypatch.setattr(backup_storage, "_is_windows", lambda: True)
    monkeypatch.setattr(backup_storage, "_powershell_path", lambda: None)
    monkeypatch.setattr(backup_service, "build_backup_dependencies", lambda: (runner, LocalPrivateBackupStorage(tmp_path)))
    response = client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 503 and "storage_key" not in response.text
    assert runner.calls == []
    with factory() as db:
        assert db.query(BackupTenant).count() == 0


def test_private_storage_cleanup_failure_retains_key_and_safe_error(setup_backup):
    client, factory, runner, storage = setup_backup
    storage.fail = True  # Simulate a partial write before storage raises.

    def fail_delete(key):
        raise OSError("password=SECRET")

    storage.delete = fail_delete
    response = client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 503
    assert "SECRET" not in response.text and "storage_key" not in response.text
    with factory() as db:
        record = db.scalars(select(BackupTenant)).one()
        assert record.estado == "ERROR" and record.fecha_fin
        assert record.storage_key in storage.files
        assert "cleanup requires operator review" in record.mensaje_error
        assert "SECRET" not in record.mensaje_error
    assert "storage_key" not in client.get("/saas/backups").text
    assert "storage_key" not in client.get(f"/saas/backups/{record.id}").text


def test_cleanup_noop_with_retained_object_marks_operator_review(setup_backup):
    client, factory, _, storage = setup_backup
    storage.fail = True
    storage.delete = lambda key: None
    assert client.post("/saas/backups", json={"empresa_id": 1}).status_code == 503
    with factory() as db:
        record = db.scalars(select(BackupTenant)).one()
        assert record.estado == "ERROR" and record.storage_key in storage.files


def test_reservation_persists_private_key_while_dump_is_in_progress(setup_backup):
    _, factory, _, storage = setup_backup
    started, release = threading.Event(), threading.Event()
    class BlockedRunner(FakeRunner):
        def dump(self, database, path):
            started.set()
            assert release.wait(10)
            super().dump(database, path)
    outcome = []
    def run():
        outcome.append(create_manual_backup(factory, 1, 1, runner=BlockedRunner(), storage=storage).estado)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(10)
        with factory() as db:
            reservation = db.scalars(select(BackupTenant)).one()
            assert reservation.estado == "EN_PROCESO"
            assert reservation.storage_key and reservation.storage_key.endswith(".dump")
    finally:
        release.set()
        thread.join(10)
    assert outcome == ["COMPLETADO"]


def test_interrupted_worker_keeps_reservation_and_key_until_operator_review(setup_backup):
    _, factory, _, storage = setup_backup

    class InterruptedRunner(FakeRunner):
        def dump(self, database, path):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        create_manual_backup(factory, 1, 1, runner=InterruptedRunner(), storage=storage)
    with factory() as db:
        reservation = db.scalars(select(BackupTenant)).one()
        assert reservation.estado == "EN_PROCESO" and reservation.fecha_fin is None
        assert reservation.storage_key and reservation.storage_key.endswith(".dump")


def test_list_validation_missing_detail_and_no_secret_fields(setup_backup):
    client, _, _, _ = setup_backup
    assert client.get("/saas/backups?estado=INVALID").status_code == 422
    assert client.get("/saas/backups?tipo=INVALID").status_code == 422
    assert client.get("/saas/backups/999").status_code == 404
    assert client.get("/saas/backups/0").status_code == 404
    response = client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 201
    for term in ("storage_key", "database_name", "password", "host_alias", "DATABASE_URL"):
        assert term not in response.text


def test_audit_failure_rolls_back_completion_and_deletes_archive(setup_backup, monkeypatch):
    client, factory, _, storage = setup_backup
    from app.modules.administracion_saas.repository import SaasRepository
    original = SaasRepository.log

    def fail_success(self, *args, **kwargs):
        if args[1] == "CREAR_BACKUP_MANUAL" and kwargs.get("result") == "EXITO":
            raise RuntimeError("password=private")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SaasRepository, "log", fail_success)
    assert client.post("/saas/backups", json={"empresa_id": 1}).status_code == 503
    assert storage.files == {}
    with factory() as db:
        assert db.scalars(select(BackupTenant.estado)).one() == "ERROR"
        assert db.scalars(select(SaasBitacora.resultado).where(
            SaasBitacora.accion == "CREAR_BACKUP_MANUAL")).one() == "ERROR"


def test_missing_storage_configuration_fails_closed_without_reservation(setup_backup, monkeypatch):
    client, factory, runner, _ = setup_backup
    def unavailable():
        raise RuntimeError("DATABASE_URL=postgresql://hidden")
    monkeypatch.setattr(backup_service, "build_backup_dependencies", unavailable)
    response = client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 503 and "postgresql" not in response.text
    assert runner.calls == []
    with factory() as db:
        assert db.query(BackupTenant).count() == 0


def test_sql_and_orm_concurrency_contract():
    sql = (Path(__file__).parents[1] / "database/saas_control/005_create_backup_tenant.sql").read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_backup_tenant_en_proceso" in sql
    assert "WHERE estado = 'EN_PROCESO'" in sql
    assert "REFERENCES saas_control.tenant_database(id)" in sql
    assert "REFERENCES saas_control.empresa(id)" in sql
    assert "REVOKE ALL ON saas_control.backup_tenant" in sql
    assert "DROP " not in sql.upper()
    assert any(index.unique and index.name == "uq_backup_tenant_en_proceso"
               for index in BackupTenant.__table__.indexes)


# ---------------------------------------------------------------------------
# PASO 8B - automatic per-tenant backups
# ---------------------------------------------------------------------------

PAZ = "America/La_Paz"
DUE = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)   # 03:00 local (UTC-4)
NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)  # 08:00 local
WINDOW = "2026-01-15T07:00:00Z"


class FailingRunner(FakeRunner):
    def __init__(self, failing):
        super().__init__()
        self.failing = failing

    def dump(self, database, path):
        if database == self.failing:
            self.calls.append(("dump", database))
            raise RuntimeError("DATABASE_URL=postgresql://x:secret@private/db password=secret")
        super().dump(database, path)


@pytest.fixture
def setup_automatic(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'automatic.sqlite'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach(connection, _):
        connection.execute("ATTACH DATABASE ? AS saas_control", (str(tmp_path / "automatic_saas.sqlite"),))

    with engine.begin() as connection:
        for model in (Empresa, TenantDatabase, SaasUsuario, SaasBitacora, BackupTenant,
                      Suscripcion, BackupPolicy, RestoreTenant):
            model.__table__.create(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            Empresa(id=1, codigo="ACME", slug="acme", estado="ACTIVA", nombre_comercial="Acme"),
            Empresa(id=2, codigo="BETA", slug="beta", estado="ACTIVA", nombre_comercial="Beta"),
            TenantDatabase(id=11, empresa_id=1, database_name="tenant_acme", estado="ACTIVA", version_schema="v1"),
            TenantDatabase(id=12, empresa_id=2, database_name="tenant_beta", estado="ACTIVA", version_schema="v2"),
            SaasUsuario(id=1, correo="admin@example.com", password_hash=hash_password("safe-password"),
                        nombres="Test", apellidos="Admin", rol="SUPERADMIN", estado=True),
            Suscripcion(id=1, empresa_id=1, plan_id=1, fecha_inicio=date(2020, 1, 1),
                        fecha_fin=date(2035, 1, 1), estado="ACTIVA"),
            Suscripcion(id=2, empresa_id=2, plan_id=1, fecha_inicio=date(2020, 1, 1),
                        fecha_fin=date(2035, 1, 1), estado="ACTIVA"),
            BackupPolicy(id=1, empresa_id=1, habilitado=True, frecuencia="DIARIA",
                         hora_local=time(3, 0), timezone=PAZ, retencion_cantidad=2, proximo_backup=None),
            BackupPolicy(id=2, empresa_id=2, habilitado=True, frecuencia="DIARIA",
                         hora_local=time(3, 0), timezone=PAZ, retencion_cantidad=2, proximo_backup=None),
        ])
        db.commit()
    yield factory
    engine.dispose()


def _set_due(factory, empresa_id, occurrence=DUE):
    with factory() as db:
        policy = db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == empresa_id)).one()
        policy.proximo_backup = occurrence
        db.commit()


def test_policy_engine_occurrences_for_all_frequencies():
    from types import SimpleNamespace
    from app.modules.administracion_saas.backup_policy import next_occurrence, window_start

    daily = SimpleNamespace(frecuencia="DIARIA", hora_local=time(3, 0), timezone=PAZ)
    assert window_start(daily, NOW) == DUE
    assert next_occurrence(daily, NOW) == datetime(2026, 1, 16, 7, 0, tzinfo=timezone.utc)
    weekly = SimpleNamespace(frecuencia="SEMANAL", hora_local=time(3, 0), timezone=PAZ)
    assert window_start(weekly, NOW) == datetime(2026, 1, 12, 7, 0, tzinfo=timezone.utc)
    assert next_occurrence(weekly, NOW) == datetime(2026, 1, 19, 7, 0, tzinfo=timezone.utc)
    monthly = SimpleNamespace(frecuencia="MENSUAL", hora_local=time(3, 0), timezone=PAZ)
    assert window_start(monthly, NOW) == datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)
    assert next_occurrence(monthly, NOW) == datetime(2026, 2, 1, 7, 0, tzinfo=timezone.utc)
    late = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)   # 19:00 local, past 03:00
    assert window_start(daily, late) == DUE
    early = datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)   # 01:00 local, before 03:00
    assert window_start(daily, early) == datetime(2026, 1, 14, 7, 0, tzinfo=timezone.utc)


def test_automatic_due_policy_completes_and_advances(setup_automatic):
    factory = setup_automatic
    runner, storage = FakeRunner(), FakeStorage()
    _set_due(factory, 1)
    summary = run_automatic_backups(factory, runner=runner, storage=storage, now=NOW)
    assert summary["debidas"] == 1 and summary["completadas"] == 1
    assert ("dump", "tenant_acme") in runner.calls
    with factory() as db:
        row = db.scalars(select(BackupTenant).where(BackupTenant.tipo == "AUTOMATICO")).one()
        assert row.estado == "COMPLETADO" and row.ventana == WINDOW
        policy = db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 1)).one()
        assert as_utc(policy.proximo_backup) == datetime(2026, 1, 16, 7, 0, tzinfo=timezone.utc)
        assert as_utc(policy.ultimo_backup_automatico) == NOW
        audit = db.scalars(select(SaasBitacora).where(SaasBitacora.accion == "CREAR_BACKUP_AUTOMATICO")).one()
        assert audit.resultado == "EXITO"


def test_suspended_company_excluded(setup_automatic):
    factory = setup_automatic
    _set_due(factory, 1)
    with factory() as db:
        db.get(Empresa, 1).estado = "SUSPENDIDA"
        db.commit()
    plan = plan_automatic_backups(factory, now=NOW)
    assert plan["politicas"] == 1 and plan["debidas"] == 0


@pytest.mark.parametrize("state,fecha_fin", [("SUSPENDIDA", date(2035, 1, 1)),
                                             ("ACTIVA", date(2025, 12, 31))])
def test_inactive_or_expired_subscription_excluded(setup_automatic, state, fecha_fin):
    factory = setup_automatic
    _set_due(factory, 1)
    with factory() as db:
        subscription = db.get(Suscripcion, 1)
        subscription.estado = state
        subscription.fecha_fin = fecha_fin
        db.commit()
    assert plan_automatic_backups(factory, now=NOW)["debidas"] == 0


def test_inactive_tenant_excluded(setup_automatic):
    factory = setup_automatic
    _set_due(factory, 1)
    with factory() as db:
        db.get(TenantDatabase, 11).estado = "SUSPENDIDA"
        db.commit()
    assert plan_automatic_backups(factory, now=NOW)["debidas"] == 0


def test_disabled_policy_excluded(setup_automatic):
    factory = setup_automatic
    _set_due(factory, 1)
    with factory() as db:
        db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 1)).one().habilitado = False
        db.commit()
    assert plan_automatic_backups(factory, now=NOW)["debidas"] == 0


def test_idempotency_window_and_duplicate_reservation(setup_automatic):
    factory = setup_automatic
    runner, storage = FakeRunner(), FakeStorage()
    _set_due(factory, 1)
    with factory() as db:
        db.add(BackupTenant(empresa_id=1, tenant_database_id=11, tipo="AUTOMATICO",
                            estado="COMPLETADO", storage_key="9" * 32 + ".dump", ventana=WINDOW))
        db.commit()
    plan = plan_automatic_backups(factory, now=NOW)
    assert plan["ya_ejecutadas"] == 1 and plan["debidas"] == 0
    assert run_automatic_backups(factory, runner=runner, storage=storage, now=NOW)["completadas"] == 0
    assert runner.calls == []
    assert create_automatic_backup(factory, 2, WINDOW, runner=runner, storage=storage).estado == "COMPLETADO"
    with pytest.raises(BackupReservationError) as exc:
        create_automatic_backup(factory, 2, WINDOW, runner=runner, storage=storage)
    assert exc.value.reason == "already_done"


def test_manual_backup_does_not_block_or_get_purged(setup_automatic):
    factory = setup_automatic
    runner, storage = FakeRunner(), FakeStorage()
    _set_due(factory, 1)
    with factory() as db:
        db.add(BackupTenant(empresa_id=1, tenant_database_id=11, tipo="MANUAL",
                            estado="COMPLETADO", storage_key="8" * 32 + ".dump", ventana=WINDOW))
        db.commit()
    assert run_automatic_backups(factory, runner=runner, storage=storage, now=NOW)["completadas"] == 1
    with factory() as db:
        assert db.scalars(select(BackupTenant).where(BackupTenant.tipo == "MANUAL")).one().estado == "COMPLETADO"
        candidates = select_retention_candidates(db, 1, 0)
        assert [row.tipo for row in candidates] == ["AUTOMATICO"]


def test_en_proceso_lock_omits_tenant_and_continues(setup_automatic):
    factory = setup_automatic
    runner, storage = FakeRunner(), FakeStorage()
    _set_due(factory, 1)
    _set_due(factory, 2)
    with factory() as db:
        db.add(BackupTenant(empresa_id=1, tenant_database_id=11, tipo="AUTOMATICO",
                            estado="EN_PROCESO", storage_key="7" * 32 + ".dump",
                            ventana="2026-01-15T06:00:00Z"))
        db.commit()
    summary = run_automatic_backups(factory, runner=runner, storage=storage, now=NOW)
    assert summary["omitidas"] == 1 and summary["completadas"] == 1
    assert [call for call in runner.calls if call == ("dump", "tenant_acme")] == []
    assert ("dump", "tenant_beta") in runner.calls


def test_failure_of_one_tenant_continues_the_other(setup_automatic):
    factory = setup_automatic
    runner, storage = FailingRunner("tenant_acme"), FakeStorage()
    _set_due(factory, 1)
    _set_due(factory, 2)
    summary = run_automatic_backups(factory, runner=runner, storage=storage, now=NOW)
    assert summary["fallidas"] == 1 and summary["completadas"] == 1
    assert ("dump", "tenant_beta") in runner.calls
    with factory() as db:
        a_policy = db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 1)).one()
        assert as_utc(a_policy.proximo_backup) == DUE
        assert a_policy.ultimo_backup_automatico is None
        b_policy = db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 2)).one()
        assert as_utc(b_policy.proximo_backup) == datetime(2026, 1, 16, 7, 0, tzinfo=timezone.utc)


def test_failure_summary_and_error_record_are_sanitized(setup_automatic):
    factory = setup_automatic
    runner, storage = FailingRunner("tenant_acme"), FakeStorage()
    _set_due(factory, 1)
    summary = run_automatic_backups(factory, runner=runner, storage=storage, now=NOW)
    rendered = json.dumps(summary)
    for term in ("secret", "postgresql", "password", "DATABASE_URL"):
        assert term not in rendered
    with factory() as db:
        error = db.scalars(select(BackupTenant).where(BackupTenant.estado == "ERROR")).one()
        assert "secret" not in error.mensaje_error
        assert "postgresql" not in error.mensaje_error
        assert "password" not in error.mensaje_error


def test_retention_only_purges_automatic(setup_automatic):
    factory = setup_automatic
    storage = FakeStorage()
    automatic_keys = ["a" * 32 + ".dump", "b" * 32 + ".dump", "c" * 32 + ".dump"]
    manual_key = "d" * 32 + ".dump"
    pre_restore_key = "e" * 32 + ".dump"
    with factory() as db:
        for index, key in enumerate(automatic_keys):
            storage.files[key] = b"automatic"
            db.add(BackupTenant(id=100 + index, empresa_id=1, tenant_database_id=11, tipo="AUTOMATICO",
                                estado="COMPLETADO", storage_key=key,
                                ventana=f"2026-01-1{index}T07:00:00Z"))
        storage.files[manual_key] = b"manual"
        db.add(BackupTenant(id=200, empresa_id=1, tenant_database_id=11, tipo="MANUAL",
                            estado="COMPLETADO", storage_key=manual_key))
        storage.files[pre_restore_key] = b"pre-restore"
        db.add(BackupTenant(id=201, empresa_id=1, tenant_database_id=11, tipo="PRE_RESTORE",
                            estado="COMPLETADO", storage_key=pre_restore_key))
        db.commit()
    with factory() as db:
        assert [row.id for row in select_retention_candidates(db, 1, 1)] == [101, 100]
    assert apply_retention(factory, 1, 1, storage) == {"seleccionados": 2, "purgados": 2}
    with factory() as db:
        remaining = {row.id: row.tipo for row in db.scalars(select(BackupTenant)).all()}
        assert remaining == {102: "AUTOMATICO", 200: "MANUAL", 201: "PRE_RESTORE"}
        actions = db.scalars(select(SaasBitacora.accion).where(
            SaasBitacora.accion == "PURGAR_BACKUP_AUTOMATICO")).all()
        assert len(actions) == 2
    assert manual_key in storage.files and pre_restore_key in storage.files
    assert automatic_keys[2] in storage.files
    assert automatic_keys[0] not in storage.files and automatic_keys[1] not in storage.files


def test_select_retention_rejects_negative_keep(setup_automatic):
    factory = setup_automatic
    with factory() as db:
        with pytest.raises(ValueError):
            select_retention_candidates(db, 1, -1)


def _private_root(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage
    monkeypatch.setattr(backup_storage, "_inspect_windows_acl", lambda *args, **kwargs: None)
    root = tmp_path / "private-root"
    root.mkdir(mode=0o700)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def test_storage_factory_defaults_to_local_provider(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage_factory
    root = _private_root(tmp_path, monkeypatch)
    monkeypatch.setenv("SAAS_BACKUP_PRIVATE_DIR", str(root))
    monkeypatch.delenv("SAAS_BACKUP_STORAGE_PROVIDER", raising=False)
    assert isinstance(backup_storage_factory.build_backup_storage(), LocalPrivateBackupStorage)


def test_supabase_provider_requires_configuration(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage_factory
    root = _private_root(tmp_path, monkeypatch)
    monkeypatch.setenv("SAAS_BACKUP_PRIVATE_DIR", str(root))
    monkeypatch.setenv("SAAS_BACKUP_STORAGE_PROVIDER", "supabase")
    monkeypatch.delenv("SAAS_BACKUP_SUPABASE_URL", raising=False)
    monkeypatch.delenv("SAAS_BACKUP_SUPABASE_BUCKET", raising=False)
    monkeypatch.delenv("SAAS_BACKUP_SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(ValueError):
        backup_storage_factory.build_backup_storage()


def test_supabase_rejects_shared_bucket_and_public_flag(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage_factory
    root = _private_root(tmp_path, monkeypatch)
    monkeypatch.setenv("SAAS_BACKUP_PRIVATE_DIR", str(root))
    monkeypatch.setenv("SAAS_BACKUP_STORAGE_PROVIDER", "supabase")
    monkeypatch.setenv("SAAS_BACKUP_SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SAAS_BACKUP_SUPABASE_SERVICE_ROLE_KEY", "service-role-secret")
    monkeypatch.setenv("SAAS_BACKUP_SUPABASE_BUCKET", "product-images")
    with pytest.raises(ValueError):
        backup_storage_factory.build_backup_storage()
    monkeypatch.setenv("SAAS_BACKUP_SUPABASE_BUCKET", "saas-backups")
    monkeypatch.setenv("SAAS_BACKUP_SUPABASE_PUBLIC", "1")
    with pytest.raises(ValueError):
        backup_storage_factory.build_backup_storage()


def test_supabase_put_uses_authorization_header_without_leaking_key(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_storage_factory
    root = _private_root(tmp_path, monkeypatch)
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return (404, b"") if method == "HEAD" else (200, b"")

    local = LocalPrivateBackupStorage(root)
    provider = backup_storage_factory.SupabasePrivateBackupStorage(
        local=local, endpoint="https://example.supabase.co", bucket="saas-backups",
        service_key="service-role-secret", transport=transport)
    key = "f" * 32 + ".dump"
    with local.staging() as directory:
        source = directory / "tenant.dump"
        source.write_bytes(b"private archive")
        provider.put(key, source)
    method, url, headers, _ = calls[-1]
    assert method == "POST"
    assert headers["Authorization"] == "Bearer service-role-secret"
    assert "service-role-secret" not in url
    assert "product-images" not in url
    assert url.endswith(f"/storage/v1/object/saas-backups/{key}")


def test_run_automatic_backups_cli_exit_codes(monkeypatch):
    from app.database import session as session_module
    from scripts.saas import run_automatic_backups as cli
    monkeypatch.setattr(session_module, "SessionLocal", lambda: None)
    monkeypatch.setattr(backup_service, "build_backup_dependencies", lambda: (object(), object()))
    monkeypatch.setattr(backup_automatic, "run_automatic_backups",
                        lambda *args, **kwargs: {"politicas": 1, "debidas": 1, "completadas": 0,
                                                 "omitidas": 0, "fallidas": 1, "purgados": 0,
                                                 "detalles": []})
    assert cli.main(["--execute"]) == 1
    monkeypatch.setattr(backup_automatic, "run_automatic_backups",
                        lambda *args, **kwargs: {"politicas": 1, "debidas": 1, "completadas": 1,
                                                 "omitidas": 0, "fallidas": 0, "purgados": 0,
                                                 "detalles": []})
    assert cli.main(["--execute"]) == 0

    def unavailable():
        raise RuntimeError("DATABASE_URL=postgresql://hidden")

    monkeypatch.setattr(backup_service, "build_backup_dependencies", unavailable)
    assert cli.main(["--execute"]) == 2
    assert cli.main([]) == 2
    assert cli.main(["--dry-run", "--execute"]) == 2


def test_migration_006_and_orm_contract():
    base = Path(__file__).parents[1] / "database/saas_control"
    sql6 = (base / "006_create_backup_policy.sql").read_text(encoding="utf-8")
    assert "saas_control.backup_policy" in sql6
    assert "ADD COLUMN IF NOT EXISTS ventana VARCHAR(80)" in sql6
    assert "uq_backup_tenant_automatico_ventana" in sql6
    assert "REVOKE ALL ON saas_control.backup_policy" in sql6
    sql5 = (base / "005_create_backup_tenant.sql").read_text(encoding="utf-8")
    assert "backup_policy" not in sql5
    assert "ventana" not in sql5
    assert any(index.unique and index.name == "uq_backup_tenant_automatico_ventana"
               for index in BackupTenant.__table__.indexes)
