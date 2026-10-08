"""Focused control-plane tests for SaaS backup-policy administration (PASO 8D-A).

No real backup/restore, no scheduler and no tenant database is touched.
"""

from datetime import datetime, time, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.security import crear_access_token, crear_tenant_access_token, hash_password
from app.core.tenancy.dependencies import get_control_db
from app.main import app
from app.modules.administracion_saas import backup_automatic, backup_service
from app.modules.administracion_saas.models import (BackupPolicy, BackupTenant, Empresa,
                                                     SaasBitacora, SaasUsuario)

PAZ = "America/La_Paz"
SEEDED_NEXT = datetime(2026, 1, 20, 7, 0, tzinfo=timezone.utc)
SEEDED_LAST = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)

VALID = {"habilitado": True, "frecuencia": "DIARIA", "hora_local": "03:00:00",
         "timezone": PAZ, "retencion_cantidad": 7}


@compiles(INET, "sqlite")
def compile_inet_for_sqlite(_type, _compiler, **_kwargs):
    return "VARCHAR(45)"


@pytest.fixture
def setup_policies(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'control.sqlite'}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach(connection, _):
        connection.execute("ATTACH DATABASE ? AS saas_control", (str(tmp_path / "saas.sqlite"),))

    with engine.begin() as connection:
        for model in (Empresa, SaasUsuario, SaasBitacora, BackupPolicy, BackupTenant):
            model.__table__.create(connection)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([
            Empresa(id=1, codigo="ACME", slug="acme", estado="ACTIVA", nombre_comercial="Acme"),
            Empresa(id=2, codigo="BETA", slug="beta", estado="SUSPENDIDA", razon_social="Beta SRL"),
            SaasUsuario(id=1, correo="admin@example.com", password_hash=hash_password("safe-password"),
                        nombres="Test", apellidos="Admin", rol="SUPERADMIN", estado=True),
            BackupPolicy(id=1, empresa_id=1, habilitado=True, frecuencia="DIARIA",
                         hora_local=time(3, 0), timezone=PAZ, retencion_cantidad=7,
                         ultimo_backup_automatico=SEEDED_LAST, proximo_backup=SEEDED_NEXT,
                         fecha_creacion=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        ])
        db.commit()

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
        yield client, factory
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def _utc(value: str) -> datetime:
    """SQLite drops tzinfo; normalize API datetimes to aware UTC for comparisons."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def test_get_lists_all_companies_with_policy_state(setup_policies):
    client, _ = setup_policies
    response = client.get("/saas/backup-policies")
    assert response.status_code == 200, response.text
    rows = {row["empresa_id"]: row for row in response.json()}
    assert set(rows) == {1, 2}
    assert rows[1]["empresa_codigo"] == "ACME"
    assert rows[1]["configurada"] is True and rows[1]["habilitado"] is True
    assert rows[1]["frecuencia"] == "DIARIA" and rows[1]["timezone"] == PAZ
    assert rows[1]["retencion_cantidad"] == 7
    assert rows[2]["empresa_codigo"] == "BETA" and rows[2]["empresa_nombre"] == "Beta SRL"
    assert rows[2]["configurada"] is False and rows[2]["habilitado"] is None
    assert rows[2]["frecuencia"] is None and rows[2]["proximo_backup"] is None
    for term in ("database_name", "storage_key", "password", "host_alias", "DATABASE_URL"):
        assert term not in response.text


def test_put_creates_policy_computes_next_backup_and_audits(setup_policies):
    client, factory = setup_policies
    response = client.put("/saas/backup-policies/2", json=VALID)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["empresa_id"] == 2 and body["empresa_codigo"] == "BETA"
    assert body["configurada"] is True and body["habilitado"] is True
    assert body["frecuencia"] == "DIARIA" and body["retencion_cantidad"] == 7
    next_backup = _utc(body["proximo_backup"])
    assert next_backup > datetime.now(timezone.utc)
    assert next_backup.hour == 7  # 03:00 America/La_Paz (UTC-4)
    with factory() as db:
        policy = db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 2)).one()
        assert policy.hora_local == time(3, 0) and policy.timezone == PAZ
        assert policy.ultimo_backup_automatico is None
        assert db.query(BackupTenant).count() == 0
        audit = db.scalars(select(SaasBitacora).where(
            SaasBitacora.accion == "CREAR_POLICY_BACKUP")).one()
        assert audit.resultado == "EXITO" and "empresa_id=2" in audit.descripcion
    for term in ("database_name", "storage_key", "password", "host_alias", "DATABASE_URL"):
        assert term not in response.text


def test_put_is_idempotent_and_preserves_proximo_when_schedule_unchanged(setup_policies):
    client, factory = setup_policies
    first = client.put("/saas/backup-policies/2", json=VALID)
    second = client.put("/saas/backup-policies/2", json=VALID)
    assert first.status_code == second.status_code == 200
    assert first.json()["proximo_backup"] == second.json()["proximo_backup"]
    with factory() as db:
        assert len(db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 2)).all()) == 1
        actions = db.scalars(select(SaasBitacora.accion).order_by(SaasBitacora.id)).all()
        assert actions[-2:] == ["CREAR_POLICY_BACKUP", "ACTUALIZAR_POLICY_BACKUP"]


def test_put_retention_only_preserves_pending_window(setup_policies):
    client, factory = setup_policies
    payload = dict(VALID, retencion_cantidad=30)
    response = client.put("/saas/backup-policies/1", json=payload)
    assert response.status_code == 200, response.text
    with factory() as db:
        policy = db.get(BackupPolicy, 1)
        assert policy.retencion_cantidad == 30
        assert policy.proximo_backup.replace(tzinfo=timezone.utc) == SEEDED_NEXT
        assert policy.ultimo_backup_automatico.replace(tzinfo=timezone.utc) == SEEDED_LAST
        assert db.query(BackupTenant).count() == 0


def test_put_recomputes_proximo_when_schedule_changes(setup_policies):
    client, factory = setup_policies
    payload = dict(VALID, frecuencia="SEMANAL", hora_local="05:00:00")
    response = client.put("/saas/backup-policies/1", json=payload)
    assert response.status_code == 200, response.text
    next_backup = _utc(response.json()["proximo_backup"])
    assert next_backup > datetime.now(timezone.utc)
    assert next_backup.hour == 9  # 05:00 America/La_Paz (UTC-4)
    with factory() as db:
        assert db.scalars(select(BackupPolicy.frecuencia).where(BackupPolicy.empresa_id == 1)).one() == "SEMANAL"


@pytest.mark.parametrize("override", [
    {"frecuencia": "HOURLY"},
    {"frecuencia": ""},
    {"timezone": "Mars/Olympus"},
    {"timezone": "   "},
    {"retencion_cantidad": 0},
    {"retencion_cantidad": 366},
    {"hora_local": "03:00:00-04:00"},
])
def test_put_validates_inputs_without_writing(setup_policies, override):
    client, factory = setup_policies
    payload = dict(VALID, **override)
    assert client.put("/saas/backup-policies/2", json=payload).status_code == 422
    with factory() as db:
        assert db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 2)).first() is None


@pytest.mark.parametrize("field", [
    "database_name", "storage_key", "archivo", "ultimo_backup_automatico",
    "ventana", "proximo_backup",
])
def test_put_rejects_untrusted_fields(setup_policies, field):
    client, factory = setup_policies
    payload = dict(VALID, **{field: "malicious"})
    assert client.put("/saas/backup-policies/2", json=payload).status_code == 422
    with factory() as db:
        assert db.scalars(select(BackupPolicy).where(BackupPolicy.empresa_id == 2)).first() is None


@pytest.mark.parametrize("empresa_id", [0, -1, 999])
def test_put_unknown_company_returns_404(setup_policies, empresa_id):
    client, _ = setup_policies
    assert client.put(f"/saas/backup-policies/{empresa_id}", json=VALID).status_code == 404


def test_disabling_policy_keeps_history_and_pending_window(setup_policies):
    client, factory = setup_policies
    with factory() as db:
        db.add(BackupTenant(empresa_id=1, tenant_database_id=11, tipo="AUTOMATICO",
                            estado="COMPLETADO", storage_key="a" * 32 + ".dump",
                            ventana="2026-01-15T07:00:00Z"))
        db.commit()
    response = client.put("/saas/backup-policies/1", json=dict(VALID, habilitado=False))
    assert response.status_code == 200, response.text
    assert response.json()["habilitado"] is False
    with factory() as db:
        policy = db.get(BackupPolicy, 1)
        assert policy.habilitado is False
        assert policy.proximo_backup.replace(tzinfo=timezone.utc) == SEEDED_NEXT
        assert policy.ultimo_backup_automatico.replace(tzinfo=timezone.utc) == SEEDED_LAST
        assert db.query(BackupTenant).count() == 1


@pytest.mark.parametrize("token", [None, "tenant", "legacy"])
@pytest.mark.parametrize("method,path", [("get", "/saas/backup-policies"),
                                         ("put", "/saas/backup-policies/1")])
def test_auth_required(setup_policies, token, method, path):
    client, _ = setup_policies
    if token == "tenant":
        credential = crear_tenant_access_token(1, 1, 11, 1, "ACME")
    elif token == "legacy":
        credential = crear_access_token(1, 1)
    else:
        credential = None
    headers = {"Authorization": f"Bearer {credential}"} if credential else {"Authorization": ""}
    kwargs = {"json": VALID} if method == "put" else {}
    response = getattr(client, method)(path, headers=headers, **kwargs)
    assert response.status_code in (401, 403)


def test_saving_policy_never_runs_scheduler_or_dump(setup_policies, monkeypatch):
    client, factory = setup_policies

    def forbidden(*_args, **_kwargs):
        raise AssertionError("scheduler/backup engine must not run when saving a policy")

    monkeypatch.setattr(backup_service, "build_backup_dependencies", forbidden)
    monkeypatch.setattr(backup_service, "create_manual_backup", forbidden)
    monkeypatch.setattr(backup_automatic, "run_automatic_backups", forbidden)
    response = client.put("/saas/backup-policies/2", json=VALID)
    assert response.status_code == 200, response.text
    with factory() as db:
        assert db.query(BackupTenant).count() == 0
