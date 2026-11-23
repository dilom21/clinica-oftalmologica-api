from datetime import date, datetime, timedelta, timezone
from ipaddress import ip_address

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import (
    crear_access_token,
    crear_saas_access_token,
    crear_tenant_access_token,
    hash_password,
)
from app.core.time import fecha_hora_utc
from app.core.tenancy.dependencies import get_control_db
from app.core.tenancy.exceptions import SubscriptionInactiveError, TenantInactiveError
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.main import app
from app.modules.administracion_saas.models import (
    BackupTenant,
    ProvisionamientoTenant,
    RestoreTenant,
    SaasBitacora,
    SaasUsuario,
)
from scripts.saas import bootstrap_saas_admin


@compiles(INET, "sqlite")
def compile_inet_for_sqlite(_type, _compiler, **_kwargs):
    return "VARCHAR(45)"


FORBIDDEN_RESPONSE_TERMS = (
    "DATABASE_URL",
    "host_alias",
    "password",
    "postgres://",
    "postgresql://",
    "mysql://",
    "connection string",
)

# Owner policy M-4: physical database names and correlation suffix used by the
# retained-restore fixture. Only SUPERADMIN may receive them.
RETAINED_TEMPORARY_SUFFIX = "0123456789abcdef0123456789abcdef"
RETAINED_BACKUP_SUFFIX = "fedcba9876543210fedcba9876543210"
PHYSICAL_NAMES = (
    "tenant_acme",
    f"restore_{RETAINED_TEMPORARY_SUFFIX}",
    f"restore_backup_{RETAINED_BACKUP_SUFFIX}",
)
OPERADOR_EMAIL = "operador@example.com"
OPERADOR_PASSWORD = "operator-password"


@pytest.fixture
def saas_client(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def attach_control_plane(dbapi_connection, _):
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    tables = [
        Empresa,
        PlanSaas,
        Suscripcion,
        TenantDatabase,
        SaasUsuario,
        SaasBitacora,
        ProvisionamientoTenant,
        BackupTenant,
        RestoreTenant,
    ]
    with engine.begin() as conn:
        for model in tables:
            model.__table__.create(conn)

    now = datetime.now(timezone.utc)
    companies = [
        Empresa(
            id=10 + index,
            codigo=code,
            slug=code.casefold(),
            nombre_comercial=name,
            estado="ACTIVA",
        )
        for index, (code, name) in enumerate(
            [
                ("ACME", "Acme"),
                ("BETA", "Beta"),
                ("CIMA", "Cima"),
                ("DELTA", "Delta"),
                ("ESTRELLA", "Estrella"),
                ("FOCO", "Foco"),
                ("GAMA", "Gama"),
            ]
        )
    ]
    plans = [
        PlanSaas(id=20, codigo="PRO", nombre="Pro", estado=True),
        PlanSaas(id=21, codigo="BASIC", nombre="Basic", estado=True),
    ]
    subscriptions = [
        Suscripcion(
            id=30 + index,
            empresa_id=10 + index,
            plan_id=20 if index % 2 == 0 else 21,
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2027, 1, 1),
            estado="ACTIVA",
        )
        for index in range(7)
    ]
    tenants = [
        TenantDatabase(
            id=40 + index,
            empresa_id=10 + index,
            database_name=f"tenant_{code.casefold()}",
            estado="ACTIVA",
            version_schema="v1",
            fecha_provisionamiento=now,
            ultima_verificacion=now,
            host_alias=f"private-{index + 1}",
        )
        for index, code in enumerate(
            ["ACME", "BETA", "CIMA", "DELTA", "ESTRELLA", "FOCO", "GAMA"]
        )
    ]
    provisionamientos = [
        ProvisionamientoTenant(
            id=50 + index,
            empresa_id=10 + index,
            tenant_database_id=40 + index,
            estado="ERROR" if index == 0 else "COMPLETADO",
            paso_actual="conexion" if index == 0 else "verificacion",
            intentos=2 if index == 0 else 1,
            fecha_inicio=now,
            fecha_fin=now,
            mensaje_error=(
                "DATABASE_URL=postgresql://user:secret@private/db "
                "host_alias=private password=do-not-return"
                if index == 0
                else None
            ),
        )
        for index in range(7)
    ]
    backups = [
        BackupTenant(
            id=60,
            empresa_id=10,
            tenant_database_id=40,
            tipo="MANUAL",
            estado="COMPLETADO",
            storage_key=f"{60:032x}.dump",
            nombre_archivo="tenant_10_20261116T010101Z_60.dump",
            formato="CUSTOM",
            size_bytes=1024,
            sha256="a" * 64,
            version_schema="v1",
            fecha_inicio=now,
            fecha_fin=now,
        )
    ]
    # Owner policy M-4 fixture: a retained restore that exposes both physical
    # names in ``mensaje_error`` and the correlated audit row in ``descripcion``.
    retained_note = (
        "restore_id=70; tenant_database_id=40; base_canonica=tenant_acme; "
        f"base_temporal=restore_{RETAINED_TEMPORARY_SUFFIX}; "
        f"base_respaldo=restore_backup_{RETAINED_BACKUP_SUFFIX}; "
        "etapa=RENOMBRE_TEMPORAL; motivo=renombre_temporal_fallido_"
        "original_recuperado_temporal_retenida"
    )
    restores = [
        RestoreTenant(
            id=70,
            empresa_id=10,
            tenant_database_id=40,
            backup_id=60,
            pre_restore_backup_id=60,
            estado="ERROR",
            etapa="RENOMBRE_TEMPORAL",
            fecha_inicio=now,
            fecha_fin=now,
            mensaje_error=retained_note,
            creado_por_saas_usuario_id=1,
        )
    ]
    with Session(engine) as db:
        db.add_all(
            [
                SaasUsuario(
                    id=1,
                    correo="admin@example.com",
                    password_hash=hash_password("correct-password"),
                    nombres="SaaS",
                    apellidos="Admin",
                    rol="SUPERADMIN",
                    estado=True,
                ),
                SaasUsuario(
                    id=2,
                    correo="inactive@example.com",
                    password_hash=hash_password("inactive-password"),
                    nombres="Inactive",
                    apellidos="Admin",
                    rol="SUPERADMIN",
                    estado=False,
                ),
                SaasUsuario(
                    id=4,
                    correo=OPERADOR_EMAIL,
                    password_hash=hash_password(OPERADOR_PASSWORD),
                    nombres="Operador",
                    apellidos="SaaS",
                    rol="OPERADOR",
                    estado=True,
                ),
                *companies,
                *plans,
                *subscriptions,
                *tenants,
                *provisionamientos,
                *backups,
                *restores,
            ]
        )
        db.commit()

    def local_db():
        with Session(engine) as db:
            yield db

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_control_db] = local_db
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        engine.dispose()


def login(client):
    response = client.post(
        "/saas/auth/login",
        json={"correo": "admin@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def assert_no_sensitive_response(response):
    body = response.text.casefold()
    assert not any(term.casefold() in body for term in FORBIDDEN_RESPONSE_TERMS)


def test_login_rejects_invalid_credentials_and_inactive_saas_user(saas_client):
    client, _ = saas_client
    invalid = client.post(
        "/saas/auth/login",
        json={"correo": "admin@example.com", "password": "wrong-password"},
    )
    inactive = client.post(
        "/saas/auth/login",
        json={"correo": "inactive@example.com", "password": "inactive-password"},
    )
    assert invalid.status_code == inactive.status_code == 401
    assert invalid.json()["detail"] == "Credenciales SaaS inválidas"
    assert inactive.json()["detail"] == "Credenciales SaaS inválidas"
    assert_no_sensitive_response(invalid)
    assert_no_sensitive_response(inactive)


def test_saas_usuario_creation_defaults_are_timezone_aware_and_nullable_access(saas_client):
    _, engine = saas_client
    with Session(engine) as db:
        user = SaasUsuario(
            id=3,
            correo="new-admin@example.com",
            password_hash=hash_password("password"),
            nombres="New",
            apellidos="Admin",
            rol="SUPERADMIN",
        )
        assert user.fecha_creacion is None
        assert user.ultimo_acceso is None
        assert fecha_hora_utc().tzinfo is not None
        assert callable(SaasUsuario.__table__.c.fecha_creacion.default.arg)
        db.add(user)
        db.commit()
        db.refresh(user)
        assert user.fecha_creacion is not None
        assert user.fecha_creacion.tzinfo is None or user.fecha_creacion.utcoffset() is not None
        assert user.ultimo_acceso is None
        assert SaasUsuario.__table__.c.fecha_creacion.nullable is False


def test_bootstrap_creates_generic_email_and_does_not_duplicate(monkeypatch, capsys):
    class FakeQuery:
        def filter(self, _condition):
            return self

        def first(self):
            return None

    class FakeSession:
        def __init__(self):
            self.added = []
            self.committed = False
            self.closed = False

        def query(self, _model):
            return FakeQuery()

        def add(self, user):
            self.added.append(user)

        def commit(self):
            self.committed = True

        def rollback(self):
            raise AssertionError("rollback was not expected")

        def close(self):
            self.closed = True

    session = FakeSession()
    answers = iter(["operator@example.net", "Operator", "Example"])
    monkeypatch.setattr(bootstrap_saas_admin, "SessionLocal", lambda: session)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(bootstrap_saas_admin, "getpass", lambda _prompt: "safe-password")

    assert bootstrap_saas_admin.main() == 0
    assert session.committed is True
    assert session.closed is True
    assert session.added[0].correo == "operator@example.net"
    assert "password" not in capsys.readouterr().out.casefold()


@pytest.mark.parametrize("db_error", [IntegrityError("INSERT", {}, Exception("secret password_hash")), SQLAlchemyError("secret password")])
def test_bootstrap_rolls_back_and_sanitizes_database_errors(monkeypatch, capsys, db_error):
    class FakeQuery:
        def filter(self, _condition):
            return self

        def first(self):
            return None

    class FakeSession:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def query(self, _model):
            return FakeQuery()

        def add(self, _user):
            pass

        def commit(self):
            raise db_error

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    session = FakeSession()
    answers = iter(["safe@example.net", "Safe", "User"])
    monkeypatch.setattr(bootstrap_saas_admin, "SessionLocal", lambda: session)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(bootstrap_saas_admin, "getpass", lambda _prompt: "safe-password")

    assert bootstrap_saas_admin.main() == 1
    output = capsys.readouterr().out.casefold()
    assert session.rolled_back is True
    assert session.closed is True
    assert "traceback" not in output
    assert "password_hash" not in output
    assert "password" not in output


def test_bootstrap_rejects_duplicate_email_without_persisting(monkeypatch, capsys):
    class FakeQuery:
        def filter(self, _condition):
            return self

        def first(self):
            return object()

    class FakeSession:
        def __init__(self):
            self.closed = False
            self.committed = False

        def query(self, _model):
            return FakeQuery()

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    session = FakeSession()
    monkeypatch.setattr(bootstrap_saas_admin, "SessionLocal", lambda: session)
    monkeypatch.setattr("builtins.input", lambda _prompt: "any-user@example.net")
    monkeypatch.setattr(bootstrap_saas_admin, "getpass", lambda _prompt: "unused")

    assert bootstrap_saas_admin.main() == 1
    assert session.committed is False
    assert session.closed is True
    assert "ya existe" in capsys.readouterr().out


def test_saas_login_and_claims_without_connection_metadata(saas_client):
    client, _ = saas_client
    response = client.post(
        "/saas/auth/login",
        json={"correo": "admin@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    claims = jwt.decode(
        response.json()["access_token"], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM]
    )
    assert claims["token_type"] == "saas_admin"
    assert claims["saas_usuario_id"] == 1
    assert not any(
        name in claims
        for name in ("database_name", "DATABASE_URL", "host_alias", "password")
    )
    assert_no_sensitive_response(response)


def test_saas_lists_seven_companies_tenants_plans_and_subscriptions(saas_client):
    client, _ = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    responses = [
        client.get("/saas/empresas"),
        client.get("/saas/tenants"),
        client.get("/saas/planes"),
        client.get("/saas/suscripciones"),
        client.get("/saas/provisionamientos"),
        client.get("/saas/bitacora"),
    ]
    assert [response.status_code for response in responses] == [200] * len(responses)
    assert len(responses[0].json()) == 7
    assert len(responses[1].json()) == 7
    assert len(responses[2].json()) == 2
    assert len(responses[3].json()) == 7
    assert len(responses[4].json()) == 7
    assert len({row["database_name"] for row in responses[1].json()}) == 7
    assert "host_alias" not in responses[1].json()[0]
    assert all(
        "postgres" not in (row["mensaje_error"] or "").casefold()
        for row in responses[4].json()
    )
    for response in responses:
        assert_no_sensitive_response(response)


def test_saas_bitacora_records_login_and_state_changes_without_secrets(saas_client):
    client, engine = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    empresa = client.patch(
        "/saas/empresas/10/estado", json={"estado": "SUSPENDIDA"}
    )
    subscription = client.patch(
        "/saas/suscripciones/30/estado", json={"estado": "SUSPENDIDA"}
    )
    assert empresa.status_code == subscription.status_code == 200
    with Session(engine) as db:
        actions = {
            row.accion
            for row in db.query(SaasBitacora).all()
        }
        assert {"LOGIN_SAAS", "CAMBIAR_ESTADO_EMPRESA", "CAMBIAR_ESTADO_SUSCRIPCION"} <= actions
        assert all("password" not in (row.descripcion or "").casefold() for row in db.query(SaasBitacora).all())
    assert_no_sensitive_response(empresa)
    assert_no_sensitive_response(subscription)


@pytest.mark.parametrize("path", [
    "/saas/empresas/10/estado",
    "/saas/suscripciones/30/estado",
])
def test_only_superadmin_can_change_company_or_subscription_state(saas_client, path):
    client, engine = saas_client
    token = login(client)
    with Session(engine) as db:
        db.get(SaasUsuario, 1).rol = "OPERADOR"
        db.commit()

    denied = client.patch(
        path,
        json={"estado": "SUSPENDIDA"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert denied.status_code == 403
    with Session(engine) as db:
        model, record_id = ((Empresa, 10) if "/empresas/" in path else (Suscripcion, 30))
        assert db.get(model, record_id).estado == "ACTIVA"


@pytest.mark.parametrize("path", [
    "/saas/empresas/10/estado",
    "/saas/suscripciones/30/estado",
])
def test_state_mutations_reject_unauthenticated_and_invalid_tokens(saas_client, path):
    client, engine = saas_client

    unauthenticated = client.patch(path, json={"estado": "SUSPENDIDA"})
    invalid = client.patch(
        path,
        json={"estado": "SUSPENDIDA"},
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert unauthenticated.status_code == invalid.status_code == 401
    with Session(engine) as db:
        model, record_id = ((Empresa, 10) if "/empresas/" in path else (Suscripcion, 30))
        assert db.get(model, record_id).estado == "ACTIVA"


def test_saas_bitacora_uses_postgresql_inet_and_preserves_ip_values(saas_client):
    _, engine = saas_client
    assert isinstance(SaasBitacora.__table__.c.ip.type, INET)
    with Session(engine) as db:
        db.add_all(
            SaasBitacora(
                id=index,
                saas_usuario_id=1,
                accion="TEST_IP",
                ip=ip,
            )
            for index, ip in enumerate(("192.0.2.10", "2001:db8::10", None), start=100)
        )
        db.commit()
        values = [
            row.ip
            for row in db.query(SaasBitacora).filter(SaasBitacora.accion == "TEST_IP").order_by(SaasBitacora.id)
        ]
    assert values == ["192.0.2.10", "2001:db8::10", None]


def test_saas_bitacora_http_serializes_native_inet_values(saas_client, monkeypatch):
    client, engine = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    with Session(engine) as db:
        db.add_all([
            SaasBitacora(id=101, saas_usuario_id=None, accion="IPV4", ip="192.0.2.10",
                         descripcion="DATABASE_URL=postgresql://example:secret@internal/db password=hidden",
                         resultado="EXITO"),
            SaasBitacora(id=102, saas_usuario_id=1, accion="IPV6", ip="2001:db8::10", resultado="EXITO"),
            SaasBitacora(id=103, saas_usuario_id=None, accion="NO_IP", ip=None, resultado=None),
        ])
        db.commit()

    from app.modules.administracion_saas.repository import SaasRepository

    original = SaasRepository.bitacora

    def native_inet_rows(repo):
        rows = original(repo)
        for row in rows:
            if row.ip is not None:
                row.ip = ip_address(row.ip)
        return rows

    monkeypatch.setattr(SaasRepository, "bitacora", native_inet_rows)
    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.get(
            "/saas/bitacora",
            headers={"Authorization": client.headers["Authorization"], "Origin": "http://localhost:4200"},
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:4200"
    rows = response.json()
    assert [row["accion"] for row in rows] == ["NO_IP", "IPV6", "IPV4", "LOGIN_SAAS"]
    assert [row["ip"] for row in rows] == [None, "2001:db8::10", "192.0.2.10", "127.0.0.1"]
    assert rows[0]["saas_usuario_id"] is None
    assert rows[2]["saas_usuario_id"] is None
    assert rows[2]["descripcion"] == "[REDACTED] [REDACTED]"
    assert all(datetime.fromisoformat(row["fecha_hora"]) for row in rows)
    assert_no_sensitive_response(response)


def test_saas_login_persists_ipv4_audit_record(saas_client):
    client, engine = saas_client
    response = client.post(
        "/saas/auth/login",
        json={"correo": "admin@example.com", "password": "correct-password"},
    )
    assert response.status_code == 200
    with Session(engine) as db:
        record = db.query(SaasBitacora).filter_by(accion="LOGIN_SAAS").one()
        assert record.ip == "127.0.0.1"
        assert record.saas_usuario_id == 1
        assert record.resultado == "EXITO"


def test_saas_login_rolls_back_last_access_when_audit_fails(saas_client, monkeypatch):
    client, engine = saas_client

    def fail_audit(*_args, **_kwargs):
        raise SQLAlchemyError("database password=should-not-leak")

    monkeypatch.setattr(
        "app.modules.administracion_saas.repository.SaasRepository.log",
        fail_audit,
    )
    response = client.post(
        "/saas/auth/login",
        json={"correo": "admin@example.com", "password": "correct-password"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "No se pudo registrar el inicio de sesión SaaS"
    assert_no_sensitive_response(response)
    with Session(engine) as db:
        user = db.get(SaasUsuario, 1)
        assert user.ultimo_acceso is None
        assert db.query(SaasBitacora).count() == 0


def test_saas_bitacora_postgresql_insert_relies_on_identity_without_explicit_id(monkeypatch):
    """PostgreSQL must generate saas_bitacora.id; the ORM insert never sends it."""
    from sqlalchemy import insert
    from sqlalchemy.dialects import postgresql

    from app.modules.administracion_saas.repository import SaasRepository

    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def get_bind(self):
            return _Bind()

        def scalar(self, *_args, **_kwargs):
            raise AssertionError("PostgreSQL must not compute MAX(id) for saas_bitacora")

        def add(self, _record):
            pass

    provided = {}
    original_init = SaasBitacora.__init__

    def capture(self, **kwargs):
        provided.update(kwargs)
        original_init(self, **kwargs)

    monkeypatch.setattr(SaasBitacora, "__init__", capture)
    SaasRepository(_Session()).log(
        1, "LOGIN_SAAS", entity="saas_usuario", record_id=1,
        description="Inicio de sesión SaaS", ip="127.0.0.1",
    )

    # The identity/autoincrement column exists and the ORM does not supply it.
    assert SaasBitacora.__table__.c.id.primary_key is True
    assert SaasBitacora.__table__.c.id.autoincrement in (True, "auto")
    assert "id" not in provided
    statement = insert(SaasBitacora.__table__).values(**provided)
    assert "id" not in statement._values
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "RETURNING saas_control.saas_bitacora.id" in compiled


def test_provisionamiento_error_is_sanitized(saas_client):
    client, _ = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    response = client.get("/saas/provisionamientos")
    assert response.status_code == 200
    error = response.json()[0]["mensaje_error"]
    assert error == "[REDACTED] [REDACTED] [REDACTED]"
    assert_no_sensitive_response(response)


def test_legacy_and_tenant_tokens_are_rejected_by_saas_endpoints(saas_client):
    client, _ = saas_client
    tokens = [
        crear_access_token(1, 1),
        crear_tenant_access_token(1, 1, 40, 10, "ACME"),
    ]
    for token in tokens:
        for path in ("/saas/planes", "/saas/bitacora"):
            response = client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401
            assert_no_sensitive_response(response)


def test_saas_endpoints_reject_missing_tampered_expired_and_invalid_claims(saas_client):
    client, _ = saas_client
    valid = login(client)
    expired = jwt.encode(
        {
            "sub": "1",
            "saas_usuario_id": 1,
            "token_type": "saas_admin",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    invalid_claims = jwt.encode(
        {
            "sub": "999",
            "saas_usuario_id": 1,
            "token_type": "saas_admin",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )

    responses = [
        client.get("/saas/planes", headers={"Authorization": ""}),
        client.get("/saas/planes", headers={"Authorization": f"Bearer {valid}alterado"}),
        client.get("/saas/planes", headers={"Authorization": f"Bearer {expired}"}),
        client.get("/saas/planes", headers={"Authorization": f"Bearer {invalid_claims}"}),
        client.get(
            "/saas/planes",
            headers={"Authorization": f"Bearer {crear_saas_access_token(2)}"},
        ),
    ]
    assert [response.status_code for response in responses] == [401] * len(responses)
    for response in responses:
        assert_no_sensitive_response(response)


def test_saas_endpoint_rejects_unknown_platform_role(saas_client):
    client, engine = saas_client
    with Session(engine) as db:
        db.add(SaasUsuario(
            id=3,
            correo="viewer@example.com",
            password_hash=hash_password("password"),
            nombres="Read",
            apellidos="Only",
            rol="VISOR",
            estado=True,
        ))
        db.commit()

    response = client.get(
        "/saas/planes",
        headers={"Authorization": f"Bearer {crear_saas_access_token(3)}"},
    )
    assert response.status_code == 403


def test_empresa_detail_and_missing_company(saas_client):
    client, _ = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    found = client.get("/saas/empresas/10")
    missing = client.get("/saas/empresas/999")
    assert found.status_code == 200
    assert found.json()["codigo"] == "ACME"
    assert missing.status_code == 404


def test_phase_2c_backup_and_restore_routes_are_registered():
    paths = app.openapi()["paths"]
    expected = {
        ("post", "/saas/backups"),
        ("get", "/saas/backups"),
        ("get", "/saas/backups/{backup_id}"),
        ("get", "/saas/backup-policies"),
        ("put", "/saas/backup-policies/{empresa_id}"),
        ("post", "/saas/restores/validate"),
        ("post", "/saas/restores"),
        ("get", "/saas/restores"),
        ("get", "/saas/restores/{restore_id}"),
    }
    assert all(method in paths[path] for method, path in expected)


@pytest.mark.parametrize(
    "path,model,record_id",
    [
        ("/saas/empresas/10/estado", Empresa, 10),
        ("/saas/suscripciones/30/estado", Suscripcion, 30),
    ],
)
def test_state_change_rolls_back_when_audit_fails(
    saas_client, monkeypatch, path, model, record_id,
):
    client, engine = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"

    def fail_audit(*_args, **_kwargs):
        raise SQLAlchemyError("audit failed")

    monkeypatch.setattr(
        "app.modules.administracion_saas.repository.SaasRepository.log",
        fail_audit,
    )
    with pytest.raises(SQLAlchemyError):
        client.patch(path, json={"estado": "SUSPENDIDA"})

    with Session(engine) as db:
        assert db.get(model, record_id).estado == "ACTIVA"
        assert db.query(SaasBitacora).count() == 1


def test_saas_jwt_is_rejected_by_clinical_dependency(saas_client):
    client, _ = saas_client
    token = login(client)
    response = client.get("/pacientes/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Token SaaS no válido para rutas clínicas"


def test_suspension_and_reactivation_are_verified_by_tenant_resolver(saas_client):
    client, engine = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    suspend = client.patch("/saas/empresas/10/estado", json={"estado": "SUSPENDIDA"})
    assert suspend.status_code == 200
    with Session(engine) as db:
        resolver = TenantResolver(TenantControlPlaneRepository(db))
        with pytest.raises(TenantInactiveError):
            resolver.resolver_por_codigo_empresa("ACME")

    reactivate = client.patch("/saas/empresas/10/estado", json={"estado": "ACTIVA"})
    assert reactivate.status_code == 200
    with Session(engine) as db:
        context = TenantResolver(TenantControlPlaneRepository(db)).resolver_por_codigo_empresa("ACME")
        assert context.database_name == "tenant_acme"


def test_subscription_state_change_is_verified_by_tenant_resolver(saas_client):
    client, engine = saas_client
    client.headers["Authorization"] = f"Bearer {login(client)}"
    suspend = client.patch("/saas/suscripciones/30/estado", json={"estado": "SUSPENDIDA"})
    assert suspend.status_code == 200
    with Session(engine) as db:
        with pytest.raises(SubscriptionInactiveError):
            TenantResolver(TenantControlPlaneRepository(db)).resolver_por_codigo_empresa("ACME")

    reactivate = client.patch("/saas/suscripciones/30/estado", json={"estado": "ACTIVA"})
    assert reactivate.status_code == 200
    with Session(engine) as db:
        assert TenantResolver(TenantControlPlaneRepository(db)).resolver_por_codigo_empresa("ACME").empresa_codigo == "ACME"


# ---------------------------------------------------------------------------
# Owner policy M-4: physical database names are SUPERADMIN-only.
# ---------------------------------------------------------------------------
M4_PATHS = (
    "/saas/restores",
    "/saas/restores/70",
    "/saas/tenants",
    "/saas/empresas",
    "/saas/empresas/10",
    "/saas/bitacora",
)


def login_operador(client):
    response = client.post(
        "/saas/auth/login",
        json={"correo": OPERADOR_EMAIL, "password": OPERADOR_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def seed_retained_audit(engine):
    with Session(engine) as db:
        db.add(SaasBitacora(
            id=900,
            saas_usuario_id=1,
            fecha_hora=datetime.now(timezone.utc),
            accion="RETENER_RESPALDO_RESTORE",
            entidad_afectada="restore_tenant",
            id_registro_afectado=70,
            descripcion=(
                "empresa_id=10; restore_id=70; tenant_database_id=40; "
                "base_canonica=tenant_acme; "
                f"base_temporal=restore_{RETAINED_TEMPORARY_SUFFIX}; "
                f"base_respaldo=restore_backup_{RETAINED_BACKUP_SUFFIX}; "
                "etapa=RENOMBRE_TEMPORAL; motivo=respaldo_retenido"
            ),
            resultado="ERROR",
        ))
        db.commit()


@pytest.mark.parametrize("path", M4_PATHS)
def test_superadmin_receives_every_physical_name(saas_client, path):
    client, engine = saas_client
    seed_retained_audit(engine)
    client.headers["Authorization"] = f"Bearer {login(client)}"

    response = client.get(path)

    assert response.status_code == 200, response.text
    body = response.text
    if path in {"/saas/tenants", "/saas/empresas", "/saas/empresas/10"}:
        assert "tenant_acme" in body
    if path in {"/saas/restores", "/saas/restores/70", "/saas/bitacora"}:
        assert "tenant_acme" in body
        assert f"restore_{RETAINED_TEMPORARY_SUFFIX}" in body
        assert f"restore_backup_{RETAINED_BACKUP_SUFFIX}" in body


@pytest.mark.parametrize("path", M4_PATHS)
def test_operador_keeps_saas_read_but_never_receives_physical_names(saas_client, path):
    client, engine = saas_client
    seed_retained_audit(engine)
    client.headers["Authorization"] = f"Bearer {login_operador(client)}"

    response = client.get(path)

    assert response.status_code == 200, response.text
    body = response.text
    for name in PHYSICAL_NAMES:
        assert name not in body
    assert "base_canonica" not in body and "base_respaldo" not in body
    assert_no_sensitive_response(response)


def test_operador_keeps_non_sensitive_saas_fields_and_correlation(saas_client):
    client, engine = saas_client
    seed_retained_audit(engine)
    client.headers["Authorization"] = f"Bearer {login_operador(client)}"

    restores = client.get("/saas/restores").json()
    assert len(restores) == 1
    assert restores[0]["id"] == 70
    assert restores[0]["estado"] == "ERROR"
    assert restores[0]["etapa"] == "RENOMBRE_TEMPORAL"
    # Correlation keys survive redaction: the operator can still audit.
    assert "restore_id=70" in restores[0]["mensaje_error"]
    assert "tenant_database_id=40" in restores[0]["mensaje_error"]
    assert "etapa=RENOMBRE_TEMPORAL" in restores[0]["mensaje_error"]

    detail = client.get("/saas/restores/70").json()
    assert detail["backup_id"] == 60 and detail["empresa_id"] == 10

    tenants = client.get("/saas/tenants").json()
    assert len(tenants) == 7
    assert all(row["database_name"] is None for row in tenants)
    assert {row["estado"] for row in tenants} == {"ACTIVA"}

    empresas = client.get("/saas/empresas").json()
    assert len(empresas) == 7
    assert all(row["database_name"] is None for row in empresas)
    assert empresas[0]["codigo"] == "ACME"

    bitacora = client.get("/saas/bitacora").json()
    assert any(row["accion"] == "RETENER_RESPALDO_RESTORE" for row in bitacora)
    correlated = next(row for row in bitacora if row["accion"] == "RETENER_RESPALDO_RESTORE")
    assert correlated["id_registro_afectado"] == 70
    assert "restore_id=70" in correlated["descripcion"]


@pytest.mark.parametrize("path", (
    "/saas/tenants",
    "/saas/empresas",
    "/saas/empresas/10",
    "/saas/restores",
    "/saas/restores/70",
    "/saas/bitacora",
))
def test_tenant_jwt_never_reaches_saas_physical_name_endpoints(saas_client, path):
    client, _ = saas_client
    response = client.get(
        path,
        headers={"Authorization": f"Bearer {crear_tenant_access_token(1, 1, 40, 10, 'ACME')}"},
    )
    assert response.status_code in (401, 403)
    body = response.text
    for name in PHYSICAL_NAMES:
        assert name not in body


def test_m4_has_no_indirect_leak_in_errors_or_legacy_tokens(saas_client):
    client, engine = saas_client
    seed_retained_audit(engine)
    operador = login_operador(client)

    missing = client.get("/saas/restores/999", headers={"Authorization": f"Bearer {operador}"})
    invalid_filter = client.get(
        "/saas/restores?estado=PENDIENTE", headers={"Authorization": f"Bearer {operador}"},
    )
    legacy = client.get(
        "/saas/restores",
        headers={"Authorization": f"Bearer {crear_access_token(1, 1)}"},
    )

    assert missing.status_code == 404
    assert invalid_filter.status_code == 200
    assert legacy.status_code == 401
    for response in (missing, invalid_filter, legacy):
        for name in PHYSICAL_NAMES:
            assert name not in response.text
        assert "base_respaldo" not in response.text


def test_physical_names_remain_stored_for_audit_and_superadmin(saas_client):
    client, engine = saas_client
    seed_retained_audit(engine)
    with Session(engine) as db:
        restore = db.get(RestoreTenant, 70)
        assert f"base_temporal=restore_{RETAINED_TEMPORARY_SUFFIX}" in restore.mensaje_error
        assert f"base_respaldo=restore_backup_{RETAINED_BACKUP_SUFFIX}" in restore.mensaje_error
        audit = db.get(SaasBitacora, 900)
        assert "base_canonica=tenant_acme" in audit.descripcion

    client.headers["Authorization"] = f"Bearer {login(client)}"
    assert "/saas/bitacora" in M4_PATHS
    assert "tenant_acme" in client.get("/saas/bitacora").text
