"""Public company discovery against an isolated control plane; never open tenant DBs."""

from datetime import date

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import time
from app.core.security import hash_password
from app.core.tenancy.dependencies import get_control_db, revalidate_tenant_context
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.main import app
from app.modules.gestion_usuarios_seguridad.schemas.schemas import TenantLoginRequest
from app.modules.gestion_usuarios_seguridad.services.tenant_auth_service import autenticar_usuario_tenant


@pytest.fixture
def control_plane(monkeypatch):
    monkeypatch.setattr(time, "fecha_local_aplicacion", lambda: date(2026, 6, 1))
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def attach_control(dbapi_connection, _):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    with engine.begin() as connection:
        for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
            model.__table__.create(connection)

    with Session(engine) as db:
        db.add(PlanSaas(id=1, codigo="PRO", estado=True))
        for index, code in enumerate(("FOCO", "BETA", "GAMA", "ACME", "DELTA", "CIMA", "ESTRELLA"), 1):
            db.add_all([
                Empresa(id=index, codigo=code, slug=code.lower(),
                        nombre_comercial="Shared" if code in ("BETA", "CIMA") else code.title(),
                        razon_social=f"Internal {code}", estado="ACTIVA",
                        logo_url="https://private.invalid/logo"),
                Suscripcion(id=index, empresa_id=index, plan_id=1,
                             fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
                             estado="ACTIVA"),
                TenantDatabase(id=index, empresa_id=index, database_name=f"tenant_{code.lower()}",
                               host_alias="private-host", estado="ACTIVA"),
            ])
        db.commit()

    def local_control_db():
        with Session(engine) as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_control_db] = local_control_db
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def public_companies(client):
    response = client.get("/seguridad/tenant/empresas")
    assert response.status_code == 200
    return response.json()


def test_public_list_has_seven_sorted_minimal_records_without_auth(control_plane):
    client, _ = control_plane
    rows = public_companies(client)
    assert [row["codigo"] for row in rows] == [
        "ACME", "DELTA", "ESTRELLA", "FOCO", "GAMA", "BETA", "CIMA",
    ]
    assert rows[-2:] == [
        {"codigo": "BETA", "nombre": "Shared"},
        {"codigo": "CIMA", "nombre": "Shared"},
    ]
    assert all(set(row) == {"codigo", "nombre"} for row in rows)
    assert not any(term in client.get("/seguridad/tenant/empresas").text.lower()
                   for term in ("database_name", "tenant_database", "host_alias", "private-host",
                                "password", "connection", "plan", "suscripcion", "logo_url",
                                "razon_social", "empresa_id", "secret", "tenant_"))


@pytest.mark.parametrize("change", [
    "suspended_company", "pending_company", "suspended_db", "pending_db",
    "invalid_db_name", "cancelled_subscription", "expired_subscription",
    "future_subscription", "missing_subscription", "duplicate_active_subscription",
    "duplicate_with_expired_subscription", "missing_plan", "missing_database",
])
def test_list_excludes_every_login_ineligible_company(control_plane, change):
    client, engine = control_plane
    with Session(engine) as db:
        company = db.get(Empresa, 1)
        subscription = db.get(Suscripcion, 1)
        tenant = db.get(TenantDatabase, 1)
        if change in ("suspended_company", "pending_company"):
            company.estado = "SUSPENDIDA" if change == "suspended_company" else "PENDIENTE"
        elif change in ("suspended_db", "pending_db"):
            tenant.estado = "SUSPENDIDA" if change == "suspended_db" else "PENDIENTE"
        elif change == "invalid_db_name":
            tenant.database_name = "Invalid-Tenant"
        elif change == "cancelled_subscription":
            subscription.estado = "SUSPENDIDA"
        elif change == "expired_subscription":
            subscription.fecha_fin = date(2026, 5, 31)
        elif change == "future_subscription":
            subscription.fecha_inicio = date(2026, 6, 2)
        elif change == "missing_subscription":
            db.delete(subscription)
        elif change in ("duplicate_active_subscription", "duplicate_with_expired_subscription"):
            db.add(Suscripcion(id=99, empresa_id=1, plan_id=1, estado="ACTIVA",
                                fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31)))
            if change == "duplicate_with_expired_subscription":
                subscription.fecha_fin = date(2026, 5, 31)
        elif change == "missing_plan":
            subscription.plan_id = 999
        elif change == "missing_database":
            db.delete(tenant)
        db.commit()
        assert "FOCO" not in {row["codigo"] for row in public_companies(client)}
        assert len(public_companies(client)) == 6


def test_selector_tracks_suspension_and_reactivation(control_plane):
    client, engine = control_plane
    with Session(engine) as db:
        db.get(Empresa, 1).estado = "SUSPENDIDA"
        db.commit()
    assert "FOCO" not in {row["codigo"] for row in public_companies(client)}
    with Session(engine) as db:
        db.get(Empresa, 1).estado = "ACTIVA"
        db.commit()
    assert "FOCO" in {row["codigo"] for row in public_companies(client)}


def test_login_still_requires_company_code_and_suspension_blocks_login(control_plane):
    client, engine = control_plane
    assert client.post("/seguridad/tenant/login", json={
        "correo": "user@example.test", "password": "password",
    }).status_code == 422

    class NoTenantConnection:
        def get_session(self, context):
            raise AssertionError("Must reject before opening a tenant database")

    with Session(engine) as db:
        db.get(Empresa, 1).estado = "SUSPENDIDA"
        db.commit()
        with pytest.raises(HTTPException) as error:
            autenticar_usuario_tenant(
                TenantLoginRequest(empresa_codigo="FOCO", correo="user@example.test", password="password"),
                TenantControlPlaneRepository(db), NoTenantConnection(),
            )
    assert error.value.status_code == 401


def test_cross_tenant_claims_cannot_revalidate_against_other_company(control_plane):
    _, engine = control_plane
    with Session(engine) as db:
        resolver = TenantResolver(TenantControlPlaneRepository(db))
        a = resolver.resolver_por_codigo_empresa("FOCO")
        b = resolver.resolver_por_codigo_empresa("BETA")
        claims = {"empresa_codigo": b.empresa_codigo,
                  "empresa_id": a.empresa_id, "tenant_id": a.tenant_database_id}
        with pytest.raises(ValueError, match="no coincide"):
            revalidate_tenant_context(claims, resolver)


def test_login_in_other_tenant_returns_401_without_reusing_first_tenant_user(control_plane):
    _, engine = control_plane

    class TenantSession:
        def __init__(self, code):
            self.code = code
            self.closed = False

        def close(self):
            self.closed = True

    class Registry:
        sessions = []

        def get_session(self, context):
            session = TenantSession(context.empresa_codigo)
            self.sessions.append(session)
            return session

    class Users:
        def __init__(self, tenant_db):
            self.tenant_db = tenant_db

        def get_usuario_by_correo(self, correo):
            if self.tenant_db.code == "FOCO" and correo == "user@example.test":
                return type("User", (), {"id": 1, "rol_id": 1, "estado": True,
                                         "password_hash": hash_password("correct-password")})()
            return None

    registry = Registry()
    with Session(engine) as db:
        repository = TenantControlPlaneRepository(db)
        first = autenticar_usuario_tenant(
            TenantLoginRequest(empresa_codigo="FOCO", correo="user@example.test", password="correct-password"),
            repository, registry, Users,
        )
        assert first.access_token
        with pytest.raises(HTTPException) as error:
            autenticar_usuario_tenant(
                TenantLoginRequest(empresa_codigo="BETA", correo="user@example.test", password="correct-password"),
                repository, registry, Users,
            )
    assert error.value.status_code == 401
    assert [session.code for session in registry.sessions] == ["FOCO", "BETA"]
    assert all(session.closed for session in registry.sessions)
