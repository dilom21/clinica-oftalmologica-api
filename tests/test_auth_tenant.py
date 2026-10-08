from datetime import date
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.core import dependencies
from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import crear_access_token, crear_tenant_access_token, hash_password
from app.core.tenancy.context import TenantContext
from app.core.tenancy.dependencies import revalidate_tenant_context
from app.core.tenancy.exceptions import TenantDatabaseUnavailableError
from app.core.tenancy.resolver import TenantResolver
from app.main import app
from app.modules.gestion_usuarios_seguridad.api.router import router
from app.modules.gestion_usuarios_seguridad.schemas.schemas import TenantLoginRequest
from app.modules.gestion_usuarios_seguridad.services.tenant_auth_service import (
    TenantUserRepository,
    autenticar_usuario_tenant,
)


class FakeRow:
    def __init__(self, **values):
        self.__dict__.update(values)


def context(state="ACTIVA"):
    return TenantContext(
        4, "VISION-CLARA", "vision-clara", "ACTIVA", 8, "PRO", "ACTIVA",
        date(2026, 1, 1), date(2026, 12, 31), 17, "tenant_vision_clara", state, "v1",
    )


class FakeControlPlane:
    def __init__(self, company="ACTIVA", subscription="ACTIVA", database="ACTIVA"):
        self.company = FakeRow(id=4, codigo="VISION-CLARA", slug="vision-clara", estado=company)
        self.subscription = FakeRow(
            id=8, empresa_id=4, plan_id=9, estado=subscription,
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31),
        )
        self.plan = FakeRow(id=9, codigo="PRO")
        self.database = FakeRow(
            id=17, empresa_id=4, database_name="tenant_vision_clara",
            estado=database, version_schema="v1",
        )

    def get_empresa_by_codigo(self, codigo):
        return self.company if codigo == "VISION-CLARA" else None

    def get_suscripciones(self, empresa_id):
        return [self.subscription]

    def get_plan(self, plan_id):
        return self.plan

    def get_tenant_database(self, empresa_id):
        return self.database


class FakeSession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeRegistry:
    def __init__(self, session=None):
        self.session = session or FakeSession()
        self.calls = 0

    def get_session(self, tenant_context):
        self.calls += 1
        return self.session


def request(**overrides):
    values = {"empresa_codigo": " vision-clara ", "correo": " Admin@Example.Test ", "password": "Secret123"}
    values.update(overrides)
    return TenantLoginRequest(**values)


def fake_user(password="Secret123", active=True):
    return FakeRow(id=21, rol_id=3, estado=active, password_hash=hash_password(password))


class FakeUserRepository:
    user = None

    def __init__(self, db):
        self.db = db

    def get_usuario_by_correo(self, correo):
        return self.user if correo == "admin@example.test" else None


def authenticate(user, registry=None, control=None):
    FakeUserRepository.user = user
    return autenticar_usuario_tenant(
        request(), control or FakeControlPlane(), registry or FakeRegistry(), FakeUserRepository,
        resolver=TenantResolver(control or FakeControlPlane(), today=lambda: date(2026, 6, 1)),
    )


def test_schema_requires_empresa_codigo():
    with pytest.raises(ValidationError):
        TenantLoginRequest(correo="a@example.test", password="x")


@pytest.mark.parametrize("field", ["database_name", "tenant_id", "empresa_id"])
def test_schema_forbids_database_selection_fields(field):
    with pytest.raises(ValidationError):
        request(**{field: "client-value"})


def test_schema_normalizes_empresa_codigo_and_email():
    datos = request()
    assert datos.empresa_codigo == "VISION-CLARA"
    assert datos.correo == "admin@example.test"


def test_schema_rejects_empty_password():
    with pytest.raises(ValidationError):
        request(password="  ")


def test_unknown_tenant_is_generic_unauthorized():
    control = FakeControlPlane()
    control.get_empresa_by_codigo = lambda codigo: None
    with pytest.raises(HTTPException) as error:
        authenticate(fake_user(), control=control)
    assert error.value.status_code == 401


@pytest.mark.parametrize("company", ["SUSPENDIDA", "PENDIENTE"])
def test_suspended_company_is_rejected(company):
    with pytest.raises(HTTPException) as error:
        authenticate(fake_user(), control=FakeControlPlane(company=company))
    assert error.value.status_code == 401


def test_invalid_subscription_is_rejected():
    with pytest.raises(HTTPException) as error:
        authenticate(fake_user(), control=FakeControlPlane(subscription="VENCIDA"))
    assert error.value.status_code == 403


def test_pending_database_fails_before_registry():
    registry = FakeRegistry()
    with pytest.raises(HTTPException) as error:
        authenticate(fake_user(), registry=registry, control=FakeControlPlane(database="PENDIENTE"))
    assert error.value.status_code == 503
    assert registry.calls == 0


def test_unknown_user_has_generic_credentials_error():
    with pytest.raises(HTTPException) as error:
        authenticate(None)
    assert error.value.status_code == 401
    assert error.value.detail == "Credenciales inválidas"


def test_wrong_password_has_same_generic_error():
    with pytest.raises(HTTPException) as error:
        authenticate(fake_user(password="Other123"))
    assert error.value.status_code == 401
    assert error.value.detail == "Credenciales inválidas"


def test_inactive_user_is_forbidden():
    with pytest.raises(HTTPException) as error:
        authenticate(fake_user(active=False))
    assert error.value.status_code == 403


def test_valid_tenant_login_uses_fake_repository_and_registry():
    result = authenticate(fake_user())
    assert result.token_type == "bearer"
    assert isinstance(result.access_token, str)


def decoded_tenant_token():
    return jwt.decode(
        crear_tenant_access_token(21, 3, 17, 4, "VISION-CLARA"),
        JWT_SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
    )


def test_tenant_jwt_has_required_claims():
    claims = decoded_tenant_token()
    assert {"sub", "rol_id", "tenant_id", "empresa_id", "empresa_codigo", "token_type", "exp"} <= claims.keys()
    assert claims["token_type"] == "tenant"


def test_tenant_jwt_has_no_database_or_secret_material():
    claims = decoded_tenant_token()
    forbidden = {"database_name", "DATABASE_URL", "host", "port", "password", "connection_string", "secret"}
    assert forbidden.isdisjoint(claims)


def credentials(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_tenant_dependency_accepts_tenant_token():
    claims = dependencies.get_tenant_claims(credentials(crear_tenant_access_token(21, 3, 17, 4, "VISION-CLARA")))
    assert claims["tenant_id"] == 17


def test_tenant_dependency_rejects_legacy_token():
    with pytest.raises(HTTPException):
        dependencies.get_tenant_claims(credentials(crear_access_token(21, 3)))


def test_tenant_dependency_rejects_missing_claims():
    token = jwt.encode({"sub": "21", "token_type": "tenant"}, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    with pytest.raises(HTTPException):
        dependencies.get_tenant_claims(credentials(token))


def test_tenant_dependency_rejects_manipulated_signature():
    token = crear_tenant_access_token(21, 3, 17, 4, "VISION-CLARA") + "tampered"
    with pytest.raises(HTTPException):
        dependencies.get_tenant_claims(credentials(token))


@pytest.mark.parametrize("claim", ["tenant_id", "empresa_id"])
def test_tenant_claim_manipulation_fails_signature(claim):
    token = crear_tenant_access_token(21, 3, 17, 4, "VISION-CLARA")
    header, payload, signature = token.split(".")
    values = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    values[claim] = 999
    manipulated = jwt.encode(values, "wrong-secret", algorithm=JWT_ALGORITHM)
    assert manipulated.split(".")[2] != signature
    with pytest.raises(HTTPException):
        dependencies.get_tenant_claims(credentials(manipulated))


def test_control_plane_revalidation_can_invalidate_current_token():
    claims = decoded_tenant_token()
    resolver = TenantResolver(FakeControlPlane(database="SUSPENDIDA"), today=lambda: date(2026, 6, 1))
    with pytest.raises(TenantDatabaseUnavailableError):
        revalidate_tenant_context(claims, resolver)


def test_legacy_login_route_is_still_present():
    paths = set(app.openapi()["paths"])
    assert "/seguridad/login" in paths


def test_tenant_login_is_a_separate_route():
    paths = set(app.openapi()["paths"])
    assert "/seguridad/tenant/login" in paths
    assert "/pacientes" in paths


def test_tenant_user_repository_is_injectable_without_real_database():
    assert TenantUserRepository is not None
    registry = FakeRegistry()
    with pytest.raises(HTTPException):
        authenticate(None, registry=registry)
    assert registry.calls == 1
