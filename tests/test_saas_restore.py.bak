"""Isolated 8C safe-restore checks; no real PostgreSQL client or restore runs."""

import hashlib
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.security import crear_access_token, crear_tenant_access_token, hash_password
from app.core.tenancy.context import TenantContext
from app.core.tenancy.dependencies import get_control_db
from app.core.tenancy.engine_registry import TenantEngineRegistry
from app.core.tenancy.exceptions import TenantDatabaseUnavailableError
from app.core.tenancy.resolver import TenantResolver
from app.main import app
from app.modules.administracion_saas import backup_service, restore_service
from app.modules.administracion_saas.models import (BackupTenant, Empresa, RestoreTenant,
                                                     SaasBitacora, SaasUsuario, TenantDatabase)
from app.modules.administracion_saas.restore_runner import (
    PublicSchemaState,
    SchemaResetRefused,
    TenantRestoreRunner,
)
from scripts.saas.provision_tenant import PostgresConnection


@compiles(INET, "sqlite")
def compile_inet_for_sqlite(_type, _compiler, **_kwargs):
    return "VARCHAR(45)"


TARGET_BYTES = b"TARGET-TENANT-ARCHIVE-DATA"
PRE_BYTES = b"PGDMP sample pre-restore archive"
SAFE_PUBLIC_SCHEMA = PublicSchemaState(owner="", grants=(), extensions=(),
                                       foreign_schemas=(), cross_schema_dependencies=0)


class FakeBackupRunner:
    """Minimal 8A engine stand-in used by the PRE_RESTORE snapshot."""

    def __init__(self):
        self.calls = []
        self.fail_dump = False
        self.fail_verify = False

    def dump(self, database, path):
        self.calls.append(("dump", database))
        if self.fail_dump:
            raise RuntimeError("DATABASE_URL=postgresql://x:secret@private/db password=secret")
        path.write_bytes(PRE_BYTES)

    def verify(self, path):
        self.calls.append(("verify", path.name))
        if self.fail_verify:
            raise RuntimeError("pg_restore --list password=secret")


class FakeStorage:
    def __init__(self):
        self.files = {}
        self.deleted = []

    def put(self, key, path):
        self.files[key] = path.read_bytes()

    def get(self, key):
        return self.files[key]

    def delete(self, key):
        self.deleted.append(key)
        self.files.pop(key, None)

    def exists(self, key):
        return key in self.files

    @contextmanager
    def staging(self):
        with tempfile.TemporaryDirectory(prefix="fake-backup-") as temporary:
            yield Path(temporary)

    def verify_private_archive(self, path):
        assert path.is_file()


class FakeRestoreBackend:
    """Records the restore plan and injects controlled failures by archive content."""

    def __init__(self, *, supports_swap=False, fail_target=False, fail_verify=False,
                 fail_rollback=False, fail_rollback_verify=False, fail_list=False,
                 fail_dispose=False, public_schema_state=None, fail_inspect=False,
                 fail_reset=False):
        self.supports_swap = supports_swap
        self.fail_target = fail_target
        self.fail_verify = fail_verify
        self.fail_rollback = fail_rollback
        self.fail_rollback_verify = fail_rollback_verify
        self.fail_list = fail_list
        self.fail_dispose = fail_dispose
        self.fail_inspect = fail_inspect
        self.fail_reset = fail_reset
        self.public_schema_state = public_schema_state or SAFE_PUBLIC_SCHEMA
        self.calls = []
        self.last_data = None

    def capabilities(self):
        return SimpleNamespace(supports_validation_database=self.supports_swap)

    def list_archive(self, archive):
        self.calls.append(("list", Path(archive).name))
        if self.fail_list:
            raise RuntimeError("pg_restore --list password=secret")

    def terminate_sessions(self, database_name):
        self.calls.append(("terminate", database_name))
        return 0

    def create_database(self, database_name):
        self.calls.append(("create", database_name))

    def drop_database(self, database_name):
        self.calls.append(("drop", database_name))

    def rename_database(self, current_name, new_name):
        self.calls.append(("rename", current_name, new_name))

    def database_exists(self, database_name):
        self.calls.append(("exists", database_name))
        return True

    def restore_into(self, database_name, archive, *, clean=False):
        data = Path(archive).read_bytes()
        self.last_data = data
        self.calls.append(("restore", database_name, clean, data))
        if data == TARGET_BYTES and self.fail_target:
            raise RuntimeError("target restore failed password=secret")
        if data == PRE_BYTES and self.fail_rollback:
            raise RuntimeError("rollback failed password=secret")

    def inspect_public_schema(self, database_name):
        self.calls.append(("inspect", database_name))
        if self.fail_inspect:
            raise RuntimeError("inspect failed password=secret")
        return self.public_schema_state

    def assert_reset_safe(self, state):
        self.calls.append(("assert_reset_safe",))
        if state.extensions or state.cross_schema_dependencies > 0:
            raise SchemaResetRefused("unsafe public schema")

    def reset_public_schema(self, database_name):
        self.calls.append(("reset", database_name))
        if self.fail_reset:
            raise RuntimeError("reset failed password=secret")

    def restore_exact(self, database_name, archive):
        self.calls.append(("restore_exact", database_name, Path(archive).read_bytes()))
        state = self.inspect_public_schema(database_name)
        self.assert_reset_safe(state)
        self.reset_public_schema(database_name)
        self.restore_into(database_name, archive, clean=True)

    def verify_structure(self, database_name):
        self.calls.append(("verify", database_name))
        if self.last_data == TARGET_BYTES and self.fail_verify:
            return False
        if self.last_data == PRE_BYTES and self.fail_rollback_verify:
            return False
        return True

    def health_check(self, database_name):
        self.calls.append(("health", database_name))
        return True


def _key(backup_id):
    return f"{backup_id:032x}.dump"


def add_backup(factory, storage, *, backup_id=1, empresa_id=1, tenant_id=11, tipo="MANUAL",
               estado="COMPLETADO", version="v1", data=TARGET_BYTES, key=None):
    storage_key = key if key is not None else _key(backup_id)
    if estado != "COMPLETADO":
        storage_key = None
    with factory() as db:
        db.add(BackupTenant(
            id=backup_id, empresa_id=empresa_id, tenant_database_id=tenant_id, tipo=tipo,
            estado=estado, storage_key=storage_key, nombre_archivo="a.dump" if storage_key else None,
            formato="CUSTOM" if storage_key else None,
            size_bytes=len(data) if storage_key else None,
            sha256=hashlib.sha256(data).hexdigest() if storage_key else None,
            version_schema=version,
            fecha_inicio=datetime(2026, 1, 1, tzinfo=timezone.utc),
            fecha_fin=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc) if storage_key else None,
        ))
        db.commit()
    if storage_key is not None:
        storage.files[storage_key] = data
    return storage_key


def set_backup_field(factory, backup_id, **fields):
    with factory() as db:
        row = db.get(BackupTenant, backup_id)
        for name, value in fields.items():
            setattr(row, name, value)
        db.commit()


def add_restore(factory, *, restore_id=None, empresa_id=1, tenant_id=11, backup_id=1,
                estado="EN_PROCESO", etapa="RESTORE"):
    with factory() as db:
        row = RestoreTenant(id=restore_id, empresa_id=empresa_id, tenant_database_id=tenant_id,
                            backup_id=backup_id, estado=estado, etapa=etapa,
                            fecha_inicio=datetime(2026, 1, 1, tzinfo=timezone.utc))
        db.add(row)
        db.commit()
        return row.id


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'restore.sqlite'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach(connection, _):
        connection.execute("ATTACH DATABASE ? AS saas_control", (str(tmp_path / "restore_saas.sqlite"),))

    with engine.begin() as connection:
        for model in (Empresa, TenantDatabase, SaasUsuario, SaasBitacora, BackupTenant, RestoreTenant):
            model.__table__.create(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            Empresa(id=1, codigo="ACME", slug="acme", estado="ACTIVA", nombre_comercial="Acme"),
            Empresa(id=2, codigo="BETA", slug="beta", estado="ACTIVA", nombre_comercial="Beta"),
            TenantDatabase(id=11, empresa_id=1, database_name="tenant_acme", estado="ACTIVA",
                           version_schema="v1"),
            TenantDatabase(id=12, empresa_id=2, database_name="tenant_beta", estado="ACTIVA",
                           version_schema="v2"),
            SaasUsuario(id=1, correo="admin@example.com", password_hash=hash_password("safe-password"),
                        nombres="Test", apellidos="Admin", rol="SUPERADMIN", estado=True),
        ])
        db.commit()

    storage = FakeStorage()
    backup_runner = FakeBackupRunner()
    backend = FakeRestoreBackend()
    disposed = []

    def disposer(tenant_id):
        disposed.append(tenant_id)
        if backend.fail_dispose:
            raise RuntimeError("dispose failed password=secret")

    monkeypatch.setattr(restore_service, "build_restore_dependencies",
                        lambda: restore_service.RestoreDependencies(
                            backend=backend, backup_runner=backup_runner, storage=storage))
    monkeypatch.setattr(backup_service, "build_backup_dependencies", lambda: (backup_runner, storage))
    monkeypatch.setattr(restore_service, "get_engine_disposer", lambda: disposer)

    def dependency():
        with factory() as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_control_db] = dependency
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post("/saas/auth/login",
                            json={"correo": "admin@example.com", "password": "safe-password"})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield SimpleNamespace(client=client, factory=factory, storage=storage, backend=backend,
                              backup_runner=backup_runner, disposed=disposed, monkeypatch=monkeypatch)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def _audit(factory):
    with factory() as db:
        return [(row.accion, row.resultado) for row in
                db.scalars(select(SaasBitacora).order_by(SaasBitacora.id)).all()]


def _restore(factory, restore_id):
    with factory() as db:
        return db.get(RestoreTenant, restore_id)


def _tenant(factory, tenant_id=11):
    with factory() as db:
        return db.get(TenantDatabase, tenant_id)


# --------------------------------------------------------------------- security
@pytest.mark.parametrize("token", [None, "tenant", "legacy"])
@pytest.mark.parametrize("method,path,body", [
    ("post", "/saas/restores/validate", {"backup_id": 1}),
    ("post", "/saas/restores", {"backup_id": 1, "confirmacion": "ACME"}),
    ("get", "/saas/restores", None),
    ("get", "/saas/restores/1", None),
])
def test_saas_auth_required(env, token, method, path, body):
    if token == "tenant":
        credential = crear_tenant_access_token(1, 1, 11, 1, "ACME")
    elif token == "legacy":
        credential = crear_access_token(1, 1)
    else:
        credential = None
    headers = {"Authorization": f"Bearer {credential}"} if credential else {"Authorization": ""}
    kwargs = {"json": body} if body is not None else {}
    response = getattr(env.client, method)(path, headers=headers, **kwargs)
    assert response.status_code in (401, 403)
    assert env.backend.calls == []


@pytest.mark.parametrize("path,payload", [
    ("/saas/restores/validate", {"backup_id": 1, "database_name": "tenant_beta"}),
    ("/saas/restores/validate", {"backup_id": 1, "storage_key": "secret"}),
    ("/saas/restores", {"backup_id": 1, "confirmacion": "ACME", "database_name": "tenant_beta"}),
    ("/saas/restores", {"backup_id": 1, "confirmacion": "ACME", "storage_key": "secret"}),
    ("/saas/restores/validate", {"backup_id": 0}),
    ("/saas/restores", {"backup_id": 1}),
])
def test_request_rejects_untrusted_or_incomplete_fields(env, path, payload):
    assert env.client.post(path, json=payload).status_code == 422
    assert env.backend.calls == []


# ----------------------------------------------------------------- backup gating
def test_backup_not_found(env):
    assert env.client.post("/saas/restores/validate", json={"backup_id": 999}).status_code == 404
    response = env.client.post("/saas/restores", json={"backup_id": 999, "confirmacion": "ACME"})
    assert response.status_code == 404


def test_backup_error_state_is_rejected(env):
    add_backup(env.factory, env.storage, backup_id=5, estado="ERROR")
    assert env.client.post("/saas/restores/validate", json={"backup_id": 5}).status_code == 409
    response = env.client.post("/saas/restores", json={"backup_id": 5, "confirmacion": "ACME"})
    assert response.status_code == 409
    with env.factory() as db:
        assert db.query(RestoreTenant).count() == 0
    assert env.backend.calls == []


def test_backup_of_other_company_confirmation_rejected(env):
    add_backup(env.factory, env.storage, backup_id=6, empresa_id=2, tenant_id=12, version="v2")
    response = env.client.post("/saas/restores", json={"backup_id": 6, "confirmacion": "ACME"})
    assert response.status_code == 409
    with env.factory() as db:
        assert db.query(RestoreTenant).count() == 0
    assert env.disposed == []  # never touched the tenant


def test_incompatible_schema_version_rejected(env):
    add_backup(env.factory, env.storage, backup_id=7, version="v2")
    assert env.client.post("/saas/restores/validate", json={"backup_id": 7}).status_code == 409


def test_storage_object_missing_rejected(env):
    key = add_backup(env.factory, env.storage, backup_id=8)
    del env.storage.files[key]
    assert env.client.post("/saas/restores/validate", json={"backup_id": 8}).status_code == 409
    response = env.client.post("/saas/restores", json={"backup_id": 8, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert _tenant(env.factory).estado == "ACTIVA"
    assert [call for call in env.backend.calls if call[0] == "restore"] == []


def test_size_mismatch_rejected(env):
    add_backup(env.factory, env.storage, backup_id=9)
    set_backup_field(env.factory, 9, size_bytes=999999)
    assert env.client.post("/saas/restores/validate", json={"backup_id": 9}).status_code == 409
    assert env.client.post("/saas/restores", json={"backup_id": 9, "confirmacion": "ACME"}).status_code == 503


def test_sha256_mismatch_rejected(env):
    add_backup(env.factory, env.storage, backup_id=10)
    set_backup_field(env.factory, 10, sha256="0" * 64)
    response = env.client.post("/saas/restores", json={"backup_id": 10, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert "checksum" in response.text
    assert _tenant(env.factory).estado == "ACTIVA"


def test_pg_restore_list_failure_rejected(env):
    add_backup(env.factory, env.storage, backup_id=11)
    env.backend.fail_list = True
    assert env.client.post("/saas/restores/validate", json={"backup_id": 11}).status_code == 409
    response = env.client.post("/saas/restores", json={"backup_id": 11, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert "secret" not in response.text
    assert _tenant(env.factory).estado == "ACTIVA"


# ------------------------------------------------------------------- validation
def test_validate_success_is_read_only_and_audited(env):
    add_backup(env.factory, env.storage, backup_id=3, tipo="AUTOMATICO")
    response = env.client.post("/saas/restores/validate", json={"backup_id": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valido"] is True and body["backup_id"] == 3 and body["empresa_id"] == 1
    assert env.backend.calls == [("list", "target.dump")]
    with env.factory() as db:
        assert db.query(RestoreTenant).count() == 0
    assert ("VALIDAR_RESTORE", "EXITO") in _audit(env.factory)
    for term in ("storage_key", "database_name", "password", "host_alias", "DATABASE_URL"):
        assert term not in response.text


# ----------------------------------------------------------------------- success
def test_create_success_in_place_full_flow(env):
    add_backup(env.factory, env.storage, backup_id=3, tipo="AUTOMATICO")
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["estado"] == "COMPLETADO" and body["etapa"] == "COMPLETADO"
    assert body["pre_restore_backup_id"] and body["fecha_fin"]

    with env.factory() as db:
        pre = db.get(BackupTenant, body["pre_restore_backup_id"])
        assert pre.tipo == "PRE_RESTORE" and pre.estado == "COMPLETADO"
        assert pre.sha256 and pre.size_bytes == len(PRE_BYTES)
    assert env.disposed == [11]
    assert ("restore", "tenant_acme", True, TARGET_BYTES) in env.backend.calls
    assert ("verify", "tenant_acme") in env.backend.calls
    assert ("health", "tenant_acme") in env.backend.calls
    assert _tenant(env.factory).estado == "ACTIVA"
    audit = _audit(env.factory)
    assert ("VALIDAR_RESTORE", "EXITO") in audit
    assert ("CREAR_BACKUP_PRE_RESTORE", "EXITO") in audit
    assert ("INICIAR_RESTORE", "EXITO") in audit
    assert ("COMPLETAR_RESTORE", "EXITO") in audit
    for term in ("storage_key", "database_name", "password", "host_alias", "DATABASE_URL"):
        assert term not in response.text


def test_create_success_temporal_validation_and_cutover(env):
    add_backup(env.factory, env.storage, backup_id=4)
    env.backend.supports_swap = True
    env.monkeypatch.setenv("SAAS_RESTORE_ALLOW_DATABASE_SWAP", "1")
    response = env.client.post("/saas/restores", json={"backup_id": 4, "confirmacion": "ACME"})
    assert response.status_code == 201, response.text
    calls = env.backend.calls
    created = [call for call in calls if call[0] == "create"]
    assert len(created) == 1 and created[0][1].startswith("restore_")
    temporary = created[0][1]
    assert ("restore", temporary, False, TARGET_BYTES) in calls
    assert ("verify", temporary) in calls
    assert ("health", temporary) in calls
    assert ("drop", "tenant_acme") in calls
    assert ("rename", temporary, "tenant_acme") in calls
    assert _tenant(env.factory).estado == "ACTIVA"


def test_swap_not_assumed_without_explicit_optin(env):
    add_backup(env.factory, env.storage, backup_id=5)
    env.backend.supports_swap = True  # capability present, but swap was never opted in
    response = env.client.post("/saas/restores", json={"backup_id": 5, "confirmacion": "ACME"})
    assert response.status_code == 201, response.text
    assert [call for call in env.backend.calls if call[0] in ("create", "rename", "drop")] == []
    assert ("restore", "tenant_acme", True, TARGET_BYTES) in env.backend.calls


def test_metadata_restore_row(env):
    add_backup(env.factory, env.storage, backup_id=3)
    body = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"}).json()
    row = _restore(env.factory, body["id"])
    assert row.empresa_id == 1 and row.tenant_database_id == 11 and row.backup_id == 3
    assert row.creado_por_saas_usuario_id == 1
    assert row.pre_restore_backup_id is not None
    assert row.fecha_inicio is not None and row.fecha_fin is not None


# ------------------------------------------------------------------ PRE_RESTORE
def test_pre_restore_mandatory_and_failure_leaves_tenant_unchanged(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backup_runner.fail_dump = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert "secret" not in response.text and "postgres" not in response.text
    with env.factory() as db:
        restore = db.scalars(select(RestoreTenant)).one()
        assert restore.estado == "ERROR" and restore.etapa == "PRE_RESTORE"
        assert restore.pre_restore_backup_id is None
    assert _tenant(env.factory).estado == "ACTIVA"
    assert env.disposed == []
    assert [call for call in env.backend.calls if call[0] == "restore"] == []
    assert ("ERROR_RESTORE", "ERROR") in _audit(env.factory)


# ------------------------------------------------------------------- lock/state
def test_one_restore_per_tenant_lock(env):
    add_backup(env.factory, env.storage, backup_id=3)
    add_restore(env.factory, estado="EN_PROCESO")
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 409
    assert env.backend.calls == []
    assert env.disposed == []


@pytest.mark.parametrize("restore_state,tenant_state", [
    ("PENDIENTE", "ACTIVA"), ("EN_PROCESO", "RESTAURANDO"),
])
def test_backup_of_same_tenant_blocked_during_restore(env, restore_state, tenant_state):
    add_restore(env.factory, estado=restore_state)
    if tenant_state != "ACTIVA":
        with env.factory() as db:
            db.get(TenantDatabase, 11).estado = tenant_state
            db.commit()
    response = env.client.post("/saas/backups", json={"empresa_id": 1})
    assert response.status_code == 409
    assert [call for call in env.backup_runner.calls if call[0] == "dump"] == []
    with env.factory() as db:
        assert db.query(BackupTenant).count() == 0


def test_restauring_state_blocks_resolver_and_registry(env):
    context = TenantContext(1, "ACME", "acme", "ACTIVA", 1, "PRO", "ACTIVA",
                            date(2026, 1, 1), date(2035, 1, 1), 11, "tenant_acme",
                            "RESTAURANDO", "v1")
    resolver = TenantResolver(SimpleNamespace())
    with pytest.raises(TenantDatabaseUnavailableError):
        resolver.require_active_database(context)
    registry = TenantEngineRegistry("postgresql+psycopg://u:p@host/control")
    with pytest.raises(TenantDatabaseUnavailableError):
        registry.get_or_create(context)


# ------------------------------------------------------------------ preparation
def test_preparation_failure_reverts_tenant_without_rollback(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.fail_dispose = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert "secret" not in response.text
    with env.factory() as db:
        restore = db.scalars(select(RestoreTenant)).one()
        assert restore.estado == "ERROR" and restore.etapa == "PREPARACION"
        assert restore.rollback_estado is None
    assert _tenant(env.factory).estado == "ACTIVA"
    assert [call for call in env.backend.calls if call[0] == "restore"] == []
    assert ("INICIAR_ROLLBACK_RESTORE", "EXITO") not in _audit(env.factory)


# --------------------------------------------------------------------- rollback
def test_automatic_rollback_after_mutation(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.fail_target = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert "rolled back" in response.text
    assert "secret" not in response.text and "postgres" not in response.text
    row = _restore(env.factory, 1)
    assert row.estado == "ERROR" and row.rollback_estado == "COMPLETADO"
    assert _tenant(env.factory).estado == "ACTIVA"
    assert ("restore", "tenant_acme", True, PRE_BYTES) in env.backend.calls
    audit = _audit(env.factory)
    assert ("INICIAR_ROLLBACK_RESTORE", "EXITO") in audit
    assert ("COMPLETAR_ROLLBACK_RESTORE", "EXITO") in audit
    assert ("ERROR_RESTORE", "ERROR") in audit


def test_verify_failure_after_mutation_rolls_back(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.fail_verify = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    row = _restore(env.factory, 1)
    assert row.rollback_estado == "COMPLETADO"
    assert _tenant(env.factory).estado == "ACTIVA"


def test_failed_rollback_never_activates_tenant(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.fail_target = True
    env.backend.fail_rollback = True
    env.backend.fail_rollback_verify = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert "manual intervention" in response.text
    assert "secret" not in response.text and "postgres" not in response.text
    row = _restore(env.factory, 1)
    assert row.estado == "ERROR" and row.rollback_estado == "ERROR"
    assert row.rollback_mensaje and "secret" not in row.rollback_mensaje
    assert _tenant(env.factory).estado != "ACTIVA"
    assert _tenant(env.factory).estado == "ERROR"
    assert ("ERROR_ROLLBACK_RESTORE", "ERROR") in _audit(env.factory)


# ---------------------------------------------------------------- list/detail
def test_list_and_detail_endpoints(env):
    add_backup(env.factory, env.storage, backup_id=3)
    created = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"}).json()
    listing = env.client.get("/saas/restores").json()
    assert listing[0]["id"] == created["id"]
    detail = env.client.get(f"/saas/restores/{created['id']}").json()
    assert detail["backup_id"] == 3
    assert env.client.get("/saas/restores?estado=INVALID").status_code == 422
    assert env.client.get("/saas/restores/999").status_code == 404
    assert env.client.get("/saas/restores/0").status_code == 404
    for term in ("storage_key", "database_name", "password", "DATABASE_URL"):
        assert term not in env.client.get("/saas/restores").text


# ---------------------------------------------------------------- migrations
def test_migration_007_and_orm_contract():
    base = Path(__file__).parents[1] / "database/saas_control"
    sql7 = (base / "007_create_restore_tenant.sql").read_text(encoding="utf-8")
    assert "saas_control.restore_tenant" in sql7
    assert "RESTAURANDO" in sql7 and "tenant_database_estado_chk" in sql7
    assert "uq_restore_tenant_activo" in sql7
    assert "REVOKE ALL ON saas_control.restore_tenant" in sql7
    for name in ("001_create_control_plane.sql", "005_create_backup_tenant.sql",
                 "006_create_backup_policy.sql"):
        assert "restore_tenant" not in (base / name).read_text(encoding="utf-8")
    assert any(index.unique and index.name == "uq_restore_tenant_activo"
               for index in RestoreTenant.__table__.indexes)


# ------------------------------------------------------------------- runner
def test_restore_runner_flags_and_secrets(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner, restore_runner
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:database-secret@private/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret")
    calls = []

    class Capture:
        def run(self, argv, env, timeout):
            calls.append((argv, env, timeout))
            joined = " ".join(argv)
            if "rolcreatedb" in joined:
                return "t"
            if "pg_database" in joined:
                return "1"
            if "pg_class" in joined:
                return "27|27|5"
            if "pg_terminate_backend" in joined:
                return "2"
            if joined.rstrip().endswith("SELECT 1;"):
                return "1"
            return ""

    runner = TenantRestoreRunner(PostgresConnection("private", 5432, "admin", "SECRET"),
                                 str(tmp_path), 30, Capture())
    assert runner.probe_capabilities().supports_validation_database is True
    runner.list_archive(tmp_path / "archive.dump")
    runner.create_database("restore_temp")
    runner.drop_database("restore_temp")
    runner.rename_database("restore_temp", "tenant_acme")
    assert runner.terminate_sessions("tenant_acme") == 2
    runner.restore_into("tenant_acme", tmp_path / "archive.dump", clean=True)
    assert runner.verify_structure("tenant_acme") is True
    assert runner.health_check("tenant_acme") is True

    restore_argv, _, _ = next(call for call in calls if "--dbname" in call[0])
    assert "--exit-on-error" in restore_argv and "--no-owner" in restore_argv
    assert "--no-privileges" in restore_argv
    assert "--clean" in restore_argv and "--if-exists" in restore_argv
    assert restore_argv[restore_argv.index("--dbname") + 1] == "tenant_acme"
    assert "SECRET" not in " ".join(restore_argv)
    for _, child_env, _ in calls:
        assert child_env["PGPASSWORD"] == "SECRET"
        assert not ({"DATABASE_URL", "JWT_SECRET_KEY"} & child_env.keys())
    assert "SECRET" not in " ".join(" ".join(argv) for argv, _, _ in calls)


def test_restore_runner_rejects_reserved_database(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner
    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    runner = TenantRestoreRunner(PostgresConnection("private", 5432, "admin", "SECRET"), str(tmp_path))
    for reserved in ("postgres", "template0", "saas_control"):
        with pytest.raises(ValueError):
            runner.drop_database(reserved)
        with pytest.raises(ValueError):
            runner.create_database(reserved)


def test_restore_runner_exposes_capabilities_contract(tmp_path, monkeypatch):
    """The production runner must expose the ``capabilities()`` the service calls."""
    from app.modules.administracion_saas import backup_runner

    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")

    class Capture:
        def run(self, argv, env, timeout):
            return "t"

    runner = TenantRestoreRunner(PostgresConnection("private", 5432, "admin", "SECRET"),
                                 str(tmp_path), 30, Capture())
    assert runner.capabilities().supports_validation_database is True


# ------------------------------------------------------------------- rehearsal
import json
import re

from app.modules.administracion_saas.restore_rehearsal import CRITICAL_TENANT_TABLES

# Fixed table set carried by a custom-format archive. `--clean` only removes
# objects present in this set; anything else survives an in-place restore.
ARCHIVE_TABLES = {"usuario", "rol", "paciente", "cita", "historial_clinico", "consulta_clinica"}
ARCHIVE_FULL_ROWS = {"usuario": 3, "rol": 2, "paciente": 5, "cita": 4,
                     "historial_clinico": 6, "consulta_clinica": 9}


class FakeRehearsalDatabase:
    """In-memory probe database with just enough SQL interpretation."""

    def __init__(self):
        self.tables = {}
        self.sequences = {}
        self.constraints = {}
        self.indexes = {}
        self.calls = []
        self.reset_targets = []
        self.foreign_schemas = {"audit_log": "preserved"}

    def load_full(self, name):
        self.tables[name] = dict(ARCHIVE_FULL_ROWS)
        self.sequences[name] = 6
        self.constraints[name] = 8
        self.indexes[name] = 7

    def load_empty(self, name):
        self.tables[name] = {table: 0 for table in ARCHIVE_TABLES}
        self.sequences[name] = 5
        self.constraints[name] = 5
        self.indexes[name] = 5

    def snapshot(self, name):
        return {
            "tables": dict(self.tables.get(name, {})),
            "sequences": self.sequences.get(name, 0),
            "constraints": self.constraints.get(name, 0),
            "indexes": self.indexes.get(name, 0),
        }

    def restore(self, name, snapshot):
        self.tables[name] = dict(snapshot["tables"])
        self.sequences[name] = snapshot["sequences"]
        self.constraints[name] = snapshot["constraints"]
        self.indexes[name] = snapshot["indexes"]

    def remove_archive_tables(self, name):
        """Delete only the tables the archive carries, like ``--clean`` does."""
        state = self.tables.setdefault(name, {})
        for table in ARCHIVE_TABLES:
            state.pop(table, None)

    def restore_archive_tables(self, name, snapshot):
        """Recreate the archive's base tables while keeping foreign objects."""
        state = self.tables.setdefault(name, {})
        for table in ARCHIVE_TABLES:
            state[table] = snapshot["tables"].get(table, 0)
        self.sequences[name] = snapshot["sequences"]
        self.constraints[name] = snapshot["constraints"]
        self.indexes[name] = snapshot["indexes"]

    def clear_public_tables(self, name):
        """Simulate ``DROP SCHEMA public CASCADE``: only public is reset."""
        self.reset_targets.append("public")
        self.tables[name] = {}

    def public_tables(self, database):
        return sorted(self.tables.get(database, {}).keys())

    def count_rows(self, database, table):
        return self.tables.get(database, {}).get(table, 0)

    def sequence_count(self, database):
        return self.sequences.get(database, 0)

    def constraint_count(self, database):
        return self.constraints.get(database, 0)

    def index_count(self, database):
        return self.indexes.get(database, 0)

    def execute(self, database, sql):
        self.calls.append((database, sql))
        state = self.tables.setdefault(database, {})
        statement = sql.strip().upper()
        if statement.startswith("CREATE TABLE"):
            match = re.search(r'public\."([a-z0-9_]+)"', sql)
            if match:
                state.setdefault(match.group(1), 0)
        elif statement.startswith("INSERT"):
            state["rehearsal_probe_mutation"] = state.get("rehearsal_probe_mutation", 0) + 1
        elif statement.startswith("DELETE"):
            match = re.search(r'public\."([a-z0-9_]+)"', sql)
            if match:
                state[match.group(1)] = 0


class RehearsalBackend:
    """TenantRestoreRunner stand-in wired to a FakeRehearsalDatabase."""

    def __init__(self, database, *, fail_target=False, fail_rollback=False,
                 fail_verify=False, empty_restore=False, fail_reset=False,
                 fail_preflight=False, fail_message=None):
        self.database = database
        self.fail_target = fail_target
        self.fail_rollback = fail_rollback
        self.fail_verify = fail_verify
        self.empty_restore = empty_restore
        self.fail_reset = fail_reset
        self.fail_preflight = fail_preflight
        self.fail_message = fail_message or "rehearsal command failed password=secret"
        self.calls = []
        self.saved_state = None

    def capabilities(self):
        return SimpleNamespace(supports_validation_database=False)

    def database_exists(self, name):
        self.calls.append(("exists", name))
        return name in self.database.tables

    def list_archive(self, archive):
        self.calls.append(("list", Path(archive).name))

    def create_database(self, name):
        self.calls.append(("create", name))
        self.database.tables.setdefault(name, {})

    def inspect_public_schema(self, name):
        self.calls.append(("inspect", name))
        if self.fail_preflight:
            raise RuntimeError(self.fail_message)
        return SAFE_PUBLIC_SCHEMA

    def assert_reset_safe(self, state):
        self.calls.append(("assert_reset_safe",))

    def reset_public_schema(self, name):
        self.calls.append(("reset", name))
        self.database.clear_public_tables(name)

    def restore_into(self, name, archive, *, clean=False):
        data = Path(archive).read_bytes()
        self.calls.append(("restore", name, clean, data))
        if data == TARGET_BYTES and self.fail_target:
            raise RuntimeError(self.fail_message)
        if data == PRE_BYTES and self.fail_rollback:
            raise RuntimeError(self.fail_message)
        if data == TARGET_BYTES:
            if self.empty_restore:
                self.database.load_empty(name)
            else:
                self.database.load_full(name)
            self.saved_state = self.database.snapshot(name)
        elif data == PRE_BYTES and self.saved_state is not None:
            # Real ``--clean`` removes only objects present in the archive TOC;
            # foreign objects (e.g. rehearsal_probe_mutation) survive.
            if clean:
                self.database.remove_archive_tables(name)
            self.database.restore_archive_tables(name, self.saved_state)

    def restore_exact(self, name, archive):
        data = Path(archive).read_bytes()
        self.calls.append(("restore_exact", name, data))
        # Mirror the real runner's order: inspect -> assert -> reset -> restore.
        state = self.inspect_public_schema(name)
        self.assert_reset_safe(state)
        if self.fail_reset:
            raise RuntimeError(self.fail_message)
        self.reset_public_schema(name)
        self.restore_into(name, archive, clean=False)

    def health_check(self, name):
        self.calls.append(("health", name))
        return True

    def verify_structure(self, name):
        self.calls.append(("verify", name))
        if self.fail_verify:
            return False
        tables = set(self.database.public_tables(name))
        return all(table in tables for table in CRITICAL_TENANT_TABLES)

    def terminate_sessions(self, name):
        self.calls.append(("terminate", name))
        return 0

    def drop_database(self, name):
        self.calls.append(("drop", name))


def _rehearsal_deps(database=None, backend=None, backup_runner=None, storage=None):
    from app.modules.administracion_saas.restore_rehearsal import RehearsalDependencies

    database = database if database is not None else FakeRehearsalDatabase()
    backup_runner = backup_runner if backup_runner is not None else FakeBackupRunner()
    storage = storage if storage is not None else FakeStorage()
    backend = backend if backend is not None else RehearsalBackend(database)
    return RehearsalDependencies(backend=backend, backup_runner=backup_runner,
                                 database=database, storage=storage)


def _seed_rehearsal_storage(storage, storage_key, data=TARGET_BYTES):
    storage.files[storage_key] = data
    return storage_key


def _probe_name():
    return "restore_probe_medico_ocular_0123abcd"


def _run_rehearsal(deps, tmp_path, *, backup_id=1, probe=None, confirmation=None):
    from app.modules.administracion_saas.restore_rehearsal import rehearse_restore

    probe = probe if probe is not None else _probe_name()
    return rehearse_restore(
        deps,
        backup_id=backup_id,
        storage_key=_key(backup_id),
        size_bytes=len(TARGET_BYTES),
        sha256=hashlib.sha256(TARGET_BYTES).hexdigest(),
        probe_database=probe,
        tenant_databases=set(),
        confirmation=probe if confirmation is None else confirmation,
        workdir=Path(tmp_path),
    )


def test_rehearsal_name_is_unique_and_probe_prefixed():
    from app.modules.administracion_saas.restore_rehearsal import (
        IDENTIFIER_PATTERN, build_probe_database_name)

    first = build_probe_database_name("tenant_medico_ocular")
    second = build_probe_database_name("tenant_medico_ocular")
    assert first.startswith("restore_probe_medico_ocular_")
    assert IDENTIFIER_PATTERN.fullmatch(first)
    assert IDENTIFIER_PATTERN.fullmatch(second)
    assert first != second
    assert len(first) <= 63 and len(second) <= 63


def test_rehearsal_rejects_real_tenant_databases():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, assert_disposable_probe)

    tenant_names = (
        "tenant_medico_ocular", "tenant_vision_clara", "tenant_oftalmo_norte",
        "tenant_visual_oriental", "tenant_instituto_vision", "tenant_oftalmocare",
        "tenant_vista_sur",
    )
    assert len(tenant_names) == 7
    for name in tenant_names:
        with pytest.raises(RehearsalRefused):
            assert_disposable_probe(name, tenant_databases=set())
    probe = _probe_name()
    with pytest.raises(RehearsalRefused):
        assert_disposable_probe(probe, tenant_databases={probe})


def test_rehearsal_rejects_reserved_and_unexpected_identifier():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, assert_disposable_probe)

    for name in ("postgres", "template0", "template1", "saas_control",
                 "Restore_Probe_x", "restore probe", "", "1bad"):
        with pytest.raises(RehearsalRefused):
            assert_disposable_probe(name, tenant_databases=set())


def test_rehearsal_creation_guard_refuses_existing_probe():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, validate_probe_creation)

    with pytest.raises(RehearsalRefused):
        validate_probe_creation(_probe_name(), tenant_databases=set(),
                                exists=lambda name: True)


def test_rehearsal_requires_exact_confirmation():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, require_probe_confirmation)

    probe = _probe_name()
    for confirmation in ("", None, "other", "restore_probe_other_00000000"):
        with pytest.raises(RehearsalRefused):
            require_probe_confirmation(probe, confirmation)
    require_probe_confirmation(probe, probe)
    require_probe_confirmation(probe, f"  {probe}  ")


def test_rehearsal_never_restores_into_tenant(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, rehearse_restore)

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(1))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    with pytest.raises(RehearsalRefused):
        rehearse_restore(
            deps, backup_id=1, storage_key=_key(1), size_bytes=len(TARGET_BYTES),
            sha256=hashlib.sha256(TARGET_BYTES).hexdigest(),
            probe_database="tenant_medico_ocular",
            tenant_databases={"tenant_medico_ocular"},
            confirmation="tenant_medico_ocular", workdir=Path(tmp_path),
        )
    assert [call for call in backend.calls if call[0] in ("create", "restore")] == []


def test_rehearsal_full_flow_demonstrates_restored_data(tmp_path):
    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(1))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    report = _run_rehearsal(deps, tmp_path)
    probe = _probe_name()

    assert report.rows_before > 0
    assert report.rows_after_rollback == report.rows_before
    assert report.critical_tables == CRITICAL_TENANT_TABLES
    assert report.dropped is False
    assert "fingerprint_after_mutation" in report.steps
    assert ("restore_exact", probe, TARGET_BYTES) in backend.calls
    assert ("restore_exact", probe, PRE_BYTES) in backend.calls
    # The rollback rebuilt public exactly: no probe-created object survives.
    assert "rehearsal_probe_mutation" not in database.public_tables(probe)
    assert set(database.public_tables(probe)) == ARCHIVE_TABLES


def test_rehearsal_rejects_empty_restored_database(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import RehearsalExecutionError

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database, empty_restore=True)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(2))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    with pytest.raises(RehearsalExecutionError):
        _run_rehearsal(deps, tmp_path, backup_id=2)
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_restore_failure_preserves_probe(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import RehearsalExecutionError

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database, fail_target=True)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(3))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    with pytest.raises(RehearsalExecutionError) as error:
        _run_rehearsal(deps, tmp_path, backup_id=3)
    message = str(error.value)
    assert "secret" not in message
    assert "postgres" not in message
    assert "preserved" in message
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_rollback_restores_fingerprint(tmp_path):
    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(4))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    report = _run_rehearsal(deps, tmp_path, backup_id=4)

    assert report.rows_mutated > 0
    assert report.rows_after_rollback == report.rows_before
    assert ("restore_exact", _probe_name(), PRE_BYTES) in backend.calls
    assert "rehearsal_probe_mutation" not in database.public_tables(_probe_name())
    assert set(database.public_tables(_probe_name())) == ARCHIVE_TABLES


def test_rehearsal_failed_rollback_is_surfaced(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import RehearsalExecutionError

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database, fail_rollback=True)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(5))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    with pytest.raises(RehearsalExecutionError) as error:
        _run_rehearsal(deps, tmp_path, backup_id=5)
    message = str(error.value)
    assert "secret" not in message
    assert "postgres" not in message
    assert "preserved" in message
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_cleanup_requires_exact_confirmation():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, cleanup_probe)

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    deps = _rehearsal_deps(database=database, backend=backend)
    probe = _probe_name()
    with pytest.raises(RehearsalRefused):
        cleanup_probe(deps, probe_database=probe, tenant_databases=set(),
                      confirmation="wrong-confirmation")
    assert [call for call in backend.calls if call[0] == "drop"] == []
    assert cleanup_probe(deps, probe_database=probe, tenant_databases=set(),
                         confirmation=probe) is True
    assert ("drop", probe) in backend.calls


def test_rehearsal_cleanup_refuses_tenant_database():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, cleanup_probe)

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    deps = _rehearsal_deps(database=database, backend=backend)
    with pytest.raises(RehearsalRefused):
        cleanup_probe(deps, probe_database="tenant_medico_ocular",
                      tenant_databases=set(), confirmation="tenant_medico_ocular")
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_cleanup_refuses_reserved():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalRefused, cleanup_probe)

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    deps = _rehearsal_deps(database=database, backend=backend)
    with pytest.raises(RehearsalRefused):
        cleanup_probe(deps, probe_database="postgres", tenant_databases=set(),
                      confirmation="postgres")
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_errors_have_no_secrets(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import RehearsalExecutionError

    secret_message = (
        "boom DATABASE_URL=postgresql://x:secret@h/db password=secret"
    )
    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database, fail_target=True, fail_message=secret_message)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(6))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    with pytest.raises(RehearsalExecutionError) as error:
        _run_rehearsal(deps, tmp_path, backup_id=6)
    message = str(error.value)
    for term in ("secret", "DATABASE_URL", "postgres://", "postgresql://"):
        assert term not in message

    clean_database = FakeRehearsalDatabase()
    clean_backend = RehearsalBackend(clean_database)
    clean_storage = FakeStorage()
    _seed_rehearsal_storage(clean_storage, _key(7))
    clean_deps = _rehearsal_deps(database=clean_database, backend=clean_backend,
                                 storage=clean_storage)
    report = _run_rehearsal(clean_deps, tmp_path, backup_id=7)
    serialized = json.dumps(report.to_dict(), sort_keys=True)
    for term in ("secret", "DATABASE_URL", "postgres://", "postgresql://", "password"):
        assert term not in serialized


# ------------------------------------------------- exact-restore regressions
def _write_archives(tmp_path):
    target = tmp_path / "target.dump"
    pre = tmp_path / "pre.dump"
    target.write_bytes(TARGET_BYTES)
    pre.write_bytes(PRE_BYTES)
    return target, pre


def test_pg_restore_clean_alone_preserves_foreign_objects(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import (
        _simulate_mutation, fingerprint)

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    probe = _probe_name()
    target, pre = _write_archives(tmp_path)

    backend.restore_into(probe, target, clean=True)
    before = fingerprint(database, probe)
    _simulate_mutation(database, probe, before)
    assert "rehearsal_probe_mutation" in database.public_tables(probe)

    backend.restore_into(probe, pre, clean=True)
    after = fingerprint(database, probe)
    assert "rehearsal_probe_mutation" in database.public_tables(probe)
    assert after != before


def test_exact_restore_removes_extra_objects(tmp_path):
    from app.modules.administracion_saas.restore_rehearsal import (
        _simulate_mutation, fingerprint)

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    probe = _probe_name()
    target, pre = _write_archives(tmp_path)

    backend.restore_into(probe, target, clean=True)
    before = fingerprint(database, probe)
    _simulate_mutation(database, probe, before)
    assert "rehearsal_probe_mutation" in database.public_tables(probe)

    backend.restore_exact(probe, pre)
    after = fingerprint(database, probe)
    assert "rehearsal_probe_mutation" not in database.public_tables(probe)
    assert after == before


def test_exact_restore_does_not_remove_other_schemas(tmp_path):
    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    probe = _probe_name()
    target, pre = _write_archives(tmp_path)

    backend.restore_into(probe, target, clean=True)
    database.reset_targets.clear()
    backend.restore_exact(probe, pre)

    assert database.reset_targets == ["public"]
    assert database.foreign_schemas == {"audit_log": "preserved"}


def test_unsafe_external_dependency_blocks_restore(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.public_schema_state = PublicSchemaState(
        owner="", grants=(), extensions=(), foreign_schemas=(),
        cross_schema_dependencies=1)
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code in (409, 503)
    assert "tenant unchanged" in response.text
    assert _tenant(env.factory).estado == "ACTIVA"
    assert [] == [call for call in env.backend.calls
                  if call[0] in ("restore", "restore_exact", "reset")]
    assert env.disposed == []


def test_pre_restore_required_before_exact_restore(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backup_runner.fail_dump = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert [call for call in env.backend.calls
            if call[0] in ("restore_exact", "reset")] == []
    assert _tenant(env.factory).estado == "ACTIVA"


def test_rollback_uses_same_exact_strategy(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.fail_target = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    assert ("restore_exact", "tenant_acme", PRE_BYTES) in env.backend.calls


def test_exact_restore_fingerprint_matches_after_rollback(tmp_path, monkeypatch):
    from app.modules.administracion_saas import restore_rehearsal

    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(8))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)

    captured = []
    real = restore_rehearsal.fingerprint

    def spy(db, name):
        result = real(db, name)
        captured.append(result)
        return result

    monkeypatch.setattr(restore_rehearsal, "fingerprint", spy)
    _run_rehearsal(deps, tmp_path, backup_id=8)

    assert captured[0] == captured[-1]
    assert captured[0] != captured[1]


def test_cleanup_failure_does_not_activate_tenant(env):
    add_backup(env.factory, env.storage, backup_id=3)
    env.backend.fail_target = True
    env.backend.fail_reset = True
    response = env.client.post("/saas/restores", json={"backup_id": 3, "confirmacion": "ACME"})
    assert response.status_code == 503
    row = _restore(env.factory, 1)
    assert row.estado == "ERROR" and row.rollback_estado == "ERROR"
    assert _tenant(env.factory).estado == "ERROR"


def test_exact_restore_refuses_unauthorized_database(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner

    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    calls = []

    class Capture:
        def run(self, argv, env, timeout):
            calls.append(argv)
            return ""

    runner = TenantRestoreRunner(PostgresConnection("private", 5432, "admin", "SECRET"),
                                 str(tmp_path), 30, Capture())
    archive = tmp_path / "archive.dump"
    for reserved in ("postgres", "saas_control"):
        with pytest.raises(ValueError):
            runner.restore_exact(reserved, archive)
    with pytest.raises(RuntimeError):
        runner.restore_exact("Bad-Name", archive)
    assert calls == []


def test_restore_runner_exact_reset_sequence(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner

    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:database-secret@private/db")
    monkeypatch.setenv("JWT_SECRET_KEY", "jwt-secret")
    calls = []

    class Capture:
        def run(self, argv, env, timeout):
            calls.append((argv, env, timeout))
            joined = " ".join(argv)
            if "pg_get_userbyid" in joined:
                return "admin"
            if "array_to_string" in joined:
                return "=UC/admin"
            if "extnamespace" in joined:
                return ""
            if "left(nspname,3)" in joined:
                return ""
            if "con.confrelid" in joined:
                return "0"
            if "pg_rewrite" in joined:
                return "0"
            return ""

    runner = TenantRestoreRunner(PostgresConnection("private", 5432, "admin", "SECRET"),
                                 str(tmp_path), 30, Capture())
    archive = tmp_path / "archive.dump"
    runner.restore_exact("tenant_acme", archive)

    joined = [" ".join(argv) for argv, _, _ in calls]
    drop_index = next(i for i, text in enumerate(joined)
                      if "DROP SCHEMA IF EXISTS public CASCADE;" in text)
    restore_index = next(i for i, text in enumerate(joined) if "--dbname" in text)
    assert drop_index < restore_index
    assert any("CREATE SCHEMA public;" in text for text in joined)
    restore_argv = calls[restore_index][0]
    assert "--clean" in restore_argv and "--if-exists" in restore_argv
    assert restore_argv[restore_argv.index("--dbname") + 1] == "tenant_acme"
    assert any("GRANT USAGE, CREATE ON SCHEMA public TO PUBLIC;" in text for text in joined)
    assert "SECRET" not in " ".join(joined)
    for _, child_env, _ in calls:
        assert child_env["PGPASSWORD"] == "SECRET"
        assert not ({"DATABASE_URL", "JWT_SECRET_KEY"} & child_env.keys())


def test_restore_runner_exact_refuses_unsafe_preflight(tmp_path, monkeypatch):
    from app.modules.administracion_saas import backup_runner

    monkeypatch.setattr(backup_runner, "_is_windows", lambda: True)
    for name in ("pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_bytes(b"not executed")
    calls = []

    class Capture:
        def run(self, argv, env, timeout):
            calls.append(argv)
            joined = " ".join(argv)
            if "pg_get_userbyid" in joined:
                return "admin"
            if "array_to_string" in joined:
                return ""
            if "extnamespace" in joined:
                return "plpgsql"
            if "left(nspname,3)" in joined:
                return ""
            if "con.confrelid" in joined:
                return "0"
            if "pg_rewrite" in joined:
                return "0"
            return ""

    runner = TenantRestoreRunner(PostgresConnection("private", 5432, "admin", "SECRET"),
                                 str(tmp_path), 30, Capture())
    with pytest.raises(SchemaResetRefused):
        runner.restore_exact("tenant_acme", tmp_path / "archive.dump")
    joined = [" ".join(argv) for argv in calls]
    assert not any("DROP SCHEMA" in text for text in joined)
    assert not any("--dbname" in text for text in joined)


def test_prepared_probe_name_is_deterministic_and_valid():
    from app.modules.administracion_saas.restore_rehearsal import (
        IDENTIFIER_PATTERN, RehearsalRefused, build_prepared_probe_name)

    first = build_prepared_probe_name("tenant_medico_ocular", 3)
    second = build_prepared_probe_name("tenant_medico_ocular", 3)
    assert first == second
    assert first.startswith("restore_probe_medico_ocular_")
    assert IDENTIFIER_PATTERN.fullmatch(first)
    assert len(first) <= 63
    assert build_prepared_probe_name("tenant_medico_ocular", 4) != first
    for bad_tenant in ("medico_ocular", "tenant_", "Tenant_x", ""):
        with pytest.raises(RehearsalRefused):
            build_prepared_probe_name(bad_tenant, 3)
    for bad_id in (0, -1, "3", None):
        with pytest.raises(RehearsalRefused):
            build_prepared_probe_name("tenant_medico_ocular", bad_id)


# ------------------------------------------------------------------ CLI tests
class _CliBackend:
    def __init__(self, existing=False):
        self.existing = existing
        self.calls = []

    def database_exists(self, name):
        self.calls.append(("exists", name))
        return self.existing


def _cli_spy(monkeypatch, cli, *, tenant_database="tenant_medico_ocular", existing=False):
    backend = _CliBackend(existing=existing)
    deps = SimpleNamespace(backend=backend, backup_runner=None, database=None, storage=None)
    context = {
        "empresa_codigo": "ACME",
        "tenant_database": tenant_database,
        "storage_key": _key(3),
        "size_bytes": len(TARGET_BYTES),
        "sha256": hashlib.sha256(TARGET_BYTES).hexdigest(),
        "version_schema": "v1",
        "tenant_databases": frozenset({"tenant_acme"}),
    }
    monkeypatch.setattr(cli, "_read_context", lambda backup_id: context)
    monkeypatch.setattr(cli, "_build_dependencies", lambda: deps)
    monkeypatch.setattr(cli, "_tenant_databases", lambda: context["tenant_databases"])
    return backend, context


def test_cli_execute_requires_probe_and_confirm(monkeypatch, capsys):
    from scripts.saas import rehearse_restore as cli

    backend, _ = _cli_spy(monkeypatch, cli)
    monkeypatch.setenv(cli.ALLOW_ENV, "1")
    assert cli.main(["--execute", "--backup-id", "3"]) == cli.EXIT_REFUSED
    assert backend.calls == []


def test_cli_execute_requires_matching_probe_and_confirm(monkeypatch, capsys):
    from scripts.saas import rehearse_restore as cli

    backend, _ = _cli_spy(monkeypatch, cli)
    monkeypatch.setenv(cli.ALLOW_ENV, "1")
    code = cli.main(["--execute", "--backup-id", "3",
                     "--probe", "restore_probe_x_00000000",
                     "--confirm", "restore_probe_y_00000000"])
    assert code == cli.EXIT_REFUSED
    assert backend.calls == []


def test_cli_execute_requires_prepared_name(monkeypatch, capsys):
    from app.modules.administracion_saas.restore_rehearsal import build_prepared_probe_name
    from scripts.saas import rehearse_restore as cli

    backend, context = _cli_spy(monkeypatch, cli)
    monkeypatch.setenv(cli.ALLOW_ENV, "1")
    prepared = build_prepared_probe_name(context["tenant_database"], 3)
    wrong = "restore_probe_medico_ocular_00000000"
    assert wrong != prepared
    code = cli.main(["--execute", "--backup-id", "3", "--probe", wrong, "--confirm", wrong])
    assert code == cli.EXIT_REFUSED


def test_cli_execute_requires_allow_env(monkeypatch, capsys):
    from app.modules.administracion_saas.restore_rehearsal import build_prepared_probe_name
    from scripts.saas import rehearse_restore as cli

    backend, context = _cli_spy(monkeypatch, cli)
    monkeypatch.delenv(cli.ALLOW_ENV, raising=False)
    prepared = build_prepared_probe_name(context["tenant_database"], 3)
    code = cli.main(["--execute", "--backup-id", "3",
                     "--probe", prepared, "--confirm", prepared])
    assert code == cli.EXIT_REFUSED
    assert backend.calls == []


def test_cli_prepare_prints_deterministic_probe(monkeypatch, capsys):
    from app.modules.administracion_saas.restore_rehearsal import build_prepared_probe_name
    from scripts.saas import rehearse_restore as cli

    backend, context = _cli_spy(monkeypatch, cli)
    code = cli.main(["--prepare", "--backup-id", "3"])
    assert code == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    prepared = build_prepared_probe_name(context["tenant_database"], 3)
    assert payload["probe_database"] == prepared
    assert payload["mode"] == "prepare"
    assert payload["allow_env"] == cli.ALLOW_ENV


# ------------------------------------------- instrumented failure diagnostics
def _run_expecting_failure(deps, tmp_path, *, backup_id=1):
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalExecutionError, rehearse_restore)

    with pytest.raises(RehearsalExecutionError) as error:
        rehearse_restore(
            deps, backup_id=backup_id, storage_key=_key(backup_id),
            size_bytes=len(TARGET_BYTES), sha256=hashlib.sha256(TARGET_BYTES).hexdigest(),
            probe_database=_probe_name(), tenant_databases=set(),
            confirmation=_probe_name(), workdir=Path(tmp_path),
        )
    return error.value


def _diag_deps(tmp_path, *, backup_id=1, **backend_kwargs):
    database = FakeRehearsalDatabase()
    backend = RehearsalBackend(database, **backend_kwargs)
    storage = FakeStorage()
    _seed_rehearsal_storage(storage, _key(backup_id))
    deps = _rehearsal_deps(database=database, backend=backend, storage=storage)
    return database, backend, deps


def test_rehearsal_reports_restore_target_stage_on_restore_failure(tmp_path):
    database, backend, deps = _diag_deps(tmp_path, fail_target=True)
    error = _run_expecting_failure(deps, tmp_path)

    assert error.stage == "restore_target"
    assert error.diagnostic is not None and error.diagnostic.stage == "restore_target"
    assert error.diagnostic.exception_class == "RuntimeError"
    assert "preserved" in error.diagnostic.message
    assert _probe_name() in database.tables
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_reports_restore_target_stage_on_reset_failure(tmp_path):
    database, backend, deps = _diag_deps(tmp_path, fail_reset=True)
    error = _run_expecting_failure(deps, tmp_path)

    assert error.stage == "restore_target"
    assert "probe restore failed" in error.diagnostic.message
    assert _probe_name() in database.tables
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_reports_restore_target_stage_on_preflight_failure(tmp_path):
    database, backend, deps = _diag_deps(tmp_path, fail_preflight=True)
    error = _run_expecting_failure(deps, tmp_path)

    assert error.stage == "restore_target"
    assert error.diagnostic is not None
    assert "preserved" in error.diagnostic.message


def test_rehearsal_reports_fingerprint_before_stage_on_empty_restore(tmp_path):
    database, backend, deps = _diag_deps(tmp_path, empty_restore=True)
    error = _run_expecting_failure(deps, tmp_path)

    assert error.stage == "fingerprint_before"
    assert "no data rows" in error.diagnostic.message


def test_rehearsal_reports_fingerprint_after_mutation_stage(tmp_path, monkeypatch):
    from app.modules.administracion_saas import restore_rehearsal

    database, backend, deps = _diag_deps(tmp_path)
    monkeypatch.setattr(restore_rehearsal, "_simulate_mutation", lambda *a, **k: 0)
    error = _run_expecting_failure(deps, tmp_path)

    assert error.stage == "fingerprint_after_mutation"
    assert "not observable" in error.diagnostic.message


def test_rehearsal_reports_rollback_stage_on_rollback_failure(tmp_path):
    database, backend, deps = _diag_deps(tmp_path, fail_rollback=True)
    error = _run_expecting_failure(deps, tmp_path)

    assert error.stage == "rollback"
    assert "rollback failed" in error.diagnostic.message
    assert _probe_name() in database.tables
    assert [call for call in backend.calls if call[0] == "drop"] == []


def test_rehearsal_diagnostic_sanitizes_secrets(tmp_path):
    secret = ("DATABASE_URL=postgresql://user:secret@private/db password=secret "
              "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef "
              "sha=0123456789abcdef0123456789abcdef")
    database, backend, deps = _diag_deps(tmp_path, fail_target=True, fail_message=secret)
    error = _run_expecting_failure(deps, tmp_path)

    rendered = json.dumps(error.diagnostic.to_dict(), sort_keys=True)
    for term in ("secret", "postgresql://", "DATABASE_URL", "eyJhbGci", "0123456789abcdef"):
        assert term not in rendered
    assert "[REDACTED" in rendered


def test_rehearsal_diagnostic_extracts_returncode_from_cause_chain():
    from subprocess import CalledProcessError

    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalExecutionError, build_rehearsal_diagnostic)

    inner = CalledProcessError(returncode=1, cmd=["pg_restore"], stderr="ERROR: boom")
    outer = RehearsalExecutionError("probe restore failed; probe x preserved",
                                    stage="restore_target")
    outer.__cause__ = inner

    diagnostic = build_rehearsal_diagnostic(outer, "restore_target")
    assert diagnostic.returncode == 1
    assert diagnostic.stage == "restore_target"
    assert diagnostic.exception_class == "CalledProcessError"


def test_rehearsal_diagnostic_extracts_sqlstate_from_message():
    from app.modules.administracion_saas.restore_rehearsal import (
        RehearsalExecutionError, build_rehearsal_diagnostic)

    error = RehearsalExecutionError("probe restore failed: ERROR: boom SQLSTATE 42501")
    diagnostic = build_rehearsal_diagnostic(error, "restore_target")
    assert diagnostic.sqlstate == "42501"


def test_rehearsal_diagnostic_sanitize_helper_redacts_paths_and_jwt():
    from app.modules.administracion_saas.restore_rehearsal import sanitize_diagnostic

    text = sanitize_diagnostic(
        r"failed at C:\Users\secret\app and /tmp/private with "
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig and hash "
        "0123456789abcdef0123456789abcdef")
    assert "C:\\Users" not in text and "/tmp/private" not in text
    assert "eyJhbGci" not in text and "0123456789abcdef" not in text


def test_cli_execute_failure_reports_sanitized_stage(monkeypatch, capsys):
    from app.modules.administracion_saas import restore_rehearsal
    from app.modules.administracion_saas.restore_rehearsal import build_prepared_probe_name
    from scripts.saas import rehearse_restore as cli

    backend, context = _cli_spy(monkeypatch, cli)
    monkeypatch.setenv(cli.ALLOW_ENV, "1")
    prepared = build_prepared_probe_name(context["tenant_database"], 3)

    def boom(*args, **kwargs):
        raise restore_rehearsal.RehearsalExecutionError(
            "probe restore failed: DATABASE_URL=postgresql://u:p@h/db password=secret; "
            "probe preserved",
            stage="restore_target",
        )

    monkeypatch.setattr(restore_rehearsal, "rehearse_restore", boom)
    code = cli.main(["--execute", "--backup-id", "3",
                     "--probe", prepared, "--confirm", prepared])
    assert code == cli.EXIT_INTERNAL

    captured = capsys.readouterr()
    payload = json.loads(captured.err.strip().splitlines()[-1])
    assert payload["error"] == "restore_rehearsal_failed"
    assert payload["stage"] == "restore_target"
    for term in ("secret", "postgresql://", "DATABASE_URL"):
        assert term not in captured.err


# Regression: PostgreSQL 17 public schema ACL with multiple privileges.
def test_schema_grants_from_pg17_public_acl_are_valid_sql():
    from app.modules.administracion_saas.restore_runner import _schema_grants_from_acl

    acl = "pg_database_owner=UC/pg_database_owner|=U/pg_database_owner"
    assert _schema_grants_from_acl(acl) == (
        'GRANT USAGE, CREATE ON SCHEMA public TO "pg_database_owner";',
        'GRANT USAGE ON SCHEMA public TO PUBLIC;',
    )
    assert _schema_grants_from_acl("") == ()


def test_schema_grants_multiple_privileges_never_use_space_separator():
    from app.modules.administracion_saas.restore_runner import _schema_grants_from_acl

    for acl in ("=UC/postgres", "postgres=CU/postgres"):
        statements = _schema_grants_from_acl(acl)
        assert len(statements) == 1
        privileges = statements[0].split("GRANT ", 1)[1].split(" ON SCHEMA", 1)[0]
        assert set(privileges.split(", ")) == {"USAGE", "CREATE"}
        assert "GRANT USAGE CREATE" not in statements[0]
