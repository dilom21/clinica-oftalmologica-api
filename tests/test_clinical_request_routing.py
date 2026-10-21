"""Exercise real clinical dependencies against three disjoint local databases."""

from datetime import date

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import time
from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import crear_access_token, crear_saas_access_token, crear_tenant_access_token
from app.core.tenancy.dependencies import get_tenant_engine_registry
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.database import session as clinical_session
from app.main import app
from app.modules.gestion_usuarios_seguridad.services import auth_service


def local_engine():
    return create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                         connect_args={"check_same_thread": False})


@pytest.fixture
def routed_client(monkeypatch):
    monkeypatch.setattr(time, "fecha_local_aplicacion", lambda: date(2026, 6, 1))
    control = local_engine()

    @event.listens_for(control, "connect")
    def attach_control(connection, _):
        connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    with control.begin() as connection:
        for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
            model.__table__.create(connection)
    with Session(control) as db:
        db.add(PlanSaas(id=1, codigo="PRO", estado=True))
        for index, code in ((1, "ALPHA"), (2, "BETA")):
            db.add_all([
                Empresa(id=index, codigo=code, slug=code.lower(), estado="ACTIVA"),
                Suscripcion(id=index, empresa_id=index, plan_id=1, estado="ACTIVA",
                             fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31)),
                TenantDatabase(id=index, empresa_id=index, database_name=f"tenant_{code.lower()}",
                               estado="ACTIVA"),
            ])
        db.commit()

    engines = {name: local_engine() for name in ("ALPHA", "BETA", "LEGACY")}
    for name, engine in engines.items():
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, estado BOOLEAN, "
                "protegido BOOLEAN, fecha_creacion TIMESTAMP)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE usuario (id INTEGER PRIMARY KEY, correo TEXT, password_hash TEXT, "
                "estado BOOLEAN, fecha_creacion TIMESTAMP, rol_id INTEGER)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE funcion (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE accion (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE rol_funcion (id INTEGER PRIMARY KEY, rol_id INTEGER, "
                "funcion_id INTEGER, accion_id INTEGER)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE bitacora (id INTEGER PRIMARY KEY, usuario_id INTEGER, "
                "fecha_hora TIMESTAMP, ip TEXT, accion TEXT, entidad_afectada TEXT, "
                "id_registro_afectado INTEGER, descripcion TEXT)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE paciente (id INTEGER PRIMARY KEY, usuario_id INTEGER, "
                "nombres TEXT, apellidos TEXT, ci TEXT, fecha_nacimiento DATE, sexo TEXT, "
                "telefono TEXT, contacto_emergencia TEXT, fecha_registro TIMESTAMP, "
                "direccion TEXT, estado BOOLEAN)"
            )
            connection.exec_driver_sql(
                "INSERT INTO rol VALUES (1, 'Administrador', NULL, 1, 0, '2026-01-01')"
            )
            connection.exec_driver_sql(
                "INSERT INTO usuario VALUES (7, ?, 'unused', 1, '2026-01-01', 1)",
                (f"{name.lower()}@example.test",))
            connection.exec_driver_sql(
                "INSERT INTO funcion VALUES (1, 'Gestionar roles y permisos', 1)"
            )
            connection.exec_driver_sql("INSERT INTO accion VALUES (1, 'AMBAS', 1)")
            if name == "ALPHA":
                connection.exec_driver_sql("INSERT INTO rol_funcion VALUES (1, 1, 1, 1)")
            connection.exec_driver_sql(
                "INSERT INTO paciente VALUES (1, NULL, ?, 'Test', NULL, NULL, NULL, "
                "NULL, NULL, '2026-01-01', NULL, 1)", (name,)
            )

    class Registry:
        calls = []

        def get_session(self, context):
            self.calls.append(context.empresa_codigo)
            return Session(engines[context.empresa_codigo])

    registry = Registry()
    monkeypatch.setattr(clinical_session, "SessionLocal", lambda: Session(engines["LEGACY"]))
    # Control-plane sessions are separate; no clinical get_db override is used.
    monkeypatch.setattr(clinical_session, "open_control_session", lambda: Session(control))
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_tenant_engine_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            yield client, engines, control, registry
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        for engine in (*engines.values(), control):
            engine.dispose()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def tenant(code, **changes):
    values = {"sub": "7", "rol_id": 1, "tenant_id": 1 if code == "ALPHA" else 2,
              "empresa_id": 1 if code == "ALPHA" else 2, "empresa_codigo": code,
              "token_type": "tenant"}
    values.update(changes)
    return jwt.encode(values, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def test_read_write_are_tenant_bound_even_on_unprotected_route(routed_client):
    client, engines, _, registry = routed_client
    for code in ("ALPHA", "BETA"):
        headers = auth(crear_tenant_access_token(7, 1, 1 if code == "ALPHA" else 2,
                                                  1 if code == "ALPHA" else 2, code))
        assert client.get("/pacientes/1", headers=headers).json()["nombres"] == code
        assert client.get("/seguridad/usuarios", headers=headers).status_code == 200
        assert client.get("/seguridad/roles", headers=headers).status_code == (200 if code == "ALPHA" else 403)
        response = client.put("/pacientes/1", json={"nombres": f"{code}-updated"}, headers=headers)
        assert response.status_code == 200, response.text
    with Session(engines["ALPHA"]) as db:
        assert db.scalar(text("SELECT nombres FROM paciente WHERE id=1")) == "ALPHA-updated"
    with Session(engines["BETA"]) as db:
        assert db.scalar(text("SELECT nombres FROM paciente WHERE id=1")) == "BETA-updated"
    with Session(engines["LEGACY"]) as db:
        assert db.scalar(text("SELECT nombres FROM paciente WHERE id=1")) == "LEGACY"
    assert registry.calls == ["ALPHA"] * 4 + ["BETA"] * 4


@pytest.mark.parametrize("token", [
    tenant("BETA", empresa_id=1), tenant("BETA", tenant_id=1),
    tenant("ALPHA", rol_id=2), tenant("ALPHA", empresa_codigo="UNKNOWN"),
    crear_saas_access_token(7), tenant("BETA") + "tampered",
])
def test_bad_tokens_cannot_read_even_public_clinical_handlers(routed_client, token):
    client, _, _, registry = routed_client
    assert client.get("/pacientes/1", headers=auth(token)).status_code in (401, 503)
    assert client.get("/seguridad/usuarios", headers=auth(token)).status_code in (401, 503)
    if token == tenant("ALPHA", rol_id=2):
        assert registry.calls == ["ALPHA", "ALPHA"]
    else:
        assert registry.calls == []


def test_suspension_is_rechecked_on_every_request(routed_client):
    client, _, control, registry = routed_client
    token = crear_tenant_access_token(7, 1, 1, 1, "ALPHA")
    assert client.get("/pacientes/1", headers=auth(token)).status_code == 200
    with Session(control) as db:
        db.get(Empresa, 1).estado = "SUSPENDIDA"
        db.commit()
    assert client.get("/pacientes/1", headers=auth(token)).status_code in (401, 503)
    assert registry.calls == ["ALPHA"]


def test_legacy_and_absent_auth_still_use_legacy(routed_client):
    client, _, _, registry = routed_client
    assert client.get("/pacientes/1").json()["nombres"] == "LEGACY"
    assert client.get("/pacientes/1", headers=auth(crear_access_token(7, 1))).json()["nombres"] == "LEGACY"
    assert client.get("/seguridad/usuarios", headers=auth(crear_access_token(7, 1))).status_code == 200
    assert registry.calls == []


@pytest.mark.parametrize("path", ["/seguridad/login", "/seguridad/login/paciente"])
def test_legacy_login_never_authenticates_tenant_user_with_bearer(routed_client, monkeypatch, path):
    client, engines, _, registry = routed_client
    monkeypatch.setattr(auth_service, "verificar_password", lambda password, stored: password == stored)
    for name in ("BETA", "LEGACY"):
        with engines[name].begin() as connection:
            connection.exec_driver_sql("UPDATE usuario SET password_hash = 'test-password' WHERE id = 7")
            if path.endswith("/paciente"):
                connection.exec_driver_sql("UPDATE rol SET nombre = 'Paciente' WHERE id = 1")

    bearer = auth(tenant("BETA"))
    tenant_credentials = {"correo": "beta@example.test", "password": "test-password"}
    for headers in ({}, bearer):
        response = client.post(path, json=tenant_credentials, headers=headers)
        assert response.status_code == 401, response.text
        assert "access_token" not in response.json()

    legacy_credentials = {"correo": "legacy@example.test", "password": "test-password"}
    for headers in ({}, bearer):
        response = client.post(path, json=legacy_credentials, headers=headers)
        assert response.status_code == 200, response.text
        claims = jwt.decode(response.json()["access_token"], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        assert claims["sub"] == "7"
        assert "tenant_id" not in claims
        assert client.get("/pacientes/1", headers=auth(response.json()["access_token"])).json()["nombres"] == "LEGACY"
    assert registry.calls == []
    with Session(engines["BETA"]) as db:
        assert db.scalar(text("SELECT count(*) FROM bitacora")) == 0
    with Session(engines["LEGACY"]) as db:
        assert db.scalar(text("SELECT count(*) FROM bitacora")) == 2


def test_control_plane_or_tenant_connection_failure_never_falls_back(routed_client, monkeypatch):
    client, _, _, registry = routed_client
    token = crear_tenant_access_token(7, 1, 1, 1, "ALPHA")
    monkeypatch.setattr(clinical_session, "open_control_session",
                        lambda: (_ for _ in ()).throw(RuntimeError("control unavailable")))
    # Unexpected control-plane failures must also be sanitized and fail closed.
    assert client.get("/pacientes/1", headers=auth(token)).status_code == 503
    assert registry.calls == []

    monkeypatch.setattr(clinical_session, "open_control_session", lambda: Session(routed_client[2]))
    monkeypatch.setattr(registry, "get_session",
                        lambda context: (_ for _ in ()).throw(RuntimeError("tenant unavailable")))
    response = client.get("/pacientes/1", headers=auth(token))
    assert response.status_code == 503
    assert "tenant unavailable" not in response.text
