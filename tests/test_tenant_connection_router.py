from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy.engine import make_url

from app.core.tenancy.context import TenantContext
from app.core.tenancy.dependencies import get_control_db
from app.core.tenancy.engine_registry import TenantEngineRegistry, check_tenant_connection
from app.core.tenancy.exceptions import (
    ControlPlaneConsistencyError,
    InvalidTenantDatabaseNameError,
    SubscriptionInactiveError,
    TenantConnectionError,
    TenantDatabaseUnavailableError,
    TenantInactiveError,
    TenantNotFoundError,
)
from app.core.tenancy.resolver import TenantResolver


class Row:
    def __init__(self, **values):
        self.__dict__.update(values)


def make_repository(
    *, company_state="ACTIVA", subscription_state="ACTIVA",
    start=date(2026, 1, 1), end=date(2026, 12, 31), db_state="ACTIVA",
    db_name="tenant_vision_clara", subscriptions=1, database=True,
):
    company = Row(id=1, codigo="VISION-CLARA", slug="vision-clara", estado=company_state)
    subs = [Row(id=i + 10, empresa_id=1, plan_id=5, estado=subscription_state,
                fecha_inicio=start, fecha_fin=end) for i in range(subscriptions)]
    plan = Row(id=5, codigo="PRO")
    tenant = Row(id=20, empresa_id=1, database_name=db_name, estado=db_state,
                 version_schema="v1") if database else None

    class Repo:
        def get_empresa_by_codigo(self, codigo):
            return company if codigo == company.codigo else None
        def get_suscripciones(self, empresa_id):
            return subs
        def get_plan(self, plan_id):
            return plan
        def get_tenant_database(self, empresa_id):
            return tenant

    return Repo()


def resolve(repo, today=date(2026, 6, 1)):
    return TenantResolver(repo, today=lambda: today)


def test_valid_company_and_metadata():
    context = resolve(make_repository()).resolver_por_codigo_empresa("VISION-CLARA")
    assert context.empresa_codigo == "VISION-CLARA"
    assert context.database_name == "tenant_vision_clara"


@pytest.mark.parametrize("repo, error", [
    (make_repository(company_state="SUSPENDIDA"), TenantInactiveError),
    (make_repository(company_state="PENDIENTE"), TenantInactiveError),
    (make_repository(subscription_state="CANCELADA"), SubscriptionInactiveError),
    (make_repository(end=date(2026, 5, 31)), SubscriptionInactiveError),
    (make_repository(start=date(2026, 7, 1)), SubscriptionInactiveError),
    (make_repository(subscriptions=2), ControlPlaneConsistencyError),
    (make_repository(database=False), TenantNotFoundError),
    (make_repository(db_name="Tenant-Invalid"), InvalidTenantDatabaseNameError),
])
def test_resolver_fail_closed(repo, error):
    with pytest.raises(error):
        resolve(repo).resolver_por_codigo_empresa("VISION-CLARA")


def test_unknown_company():
    with pytest.raises(TenantNotFoundError):
        resolve(make_repository()).resolver_por_codigo_empresa("UNKNOWN")


def test_duplicate_active_subscriptions_rejected_before_validity_selection():
    repo = make_repository()
    repo.get_suscripciones = lambda empresa_id: [
        Row(id=10, empresa_id=1, plan_id=5, estado="ACTIVA",
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 5, 31)),
        Row(id=11, empresa_id=1, plan_id=5, estado="ACTIVA",
            fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31)),
    ]

    with pytest.raises(ControlPlaneConsistencyError):
        resolve(repo).resolver_por_codigo_empresa("VISION-CLARA")


@pytest.mark.parametrize("state", ["PENDIENTE", "SUSPENDIDA", "ERROR"])
def test_non_active_database_is_metadata_only(state):
    context = resolve(make_repository(db_state=state)).resolver_por_codigo_empresa("VISION-CLARA")
    with pytest.raises(TenantDatabaseUnavailableError):
        TenantEngineRegistry("postgresql+psycopg://u:p@host/control").get_or_create(context)


@pytest.mark.parametrize("name", ["a", "tenant_1", "a" + "x" * 62])
def test_valid_database_names(name):
    assert resolve(make_repository(db_name=name)).resolver_por_codigo_empresa("VISION-CLARA").database_name == name


@pytest.mark.parametrize("name", ["", "1tenant", "Tenant", "a" * 64, "a/b", "a b", "a;drop"])
def test_invalid_database_names(name):
    with pytest.raises(InvalidTenantDatabaseNameError):
        resolve(make_repository(db_name=name)).resolver_por_codigo_empresa("VISION-CLARA")


def test_database_name_cannot_be_supplied_by_client():
    context = resolve(make_repository()).resolver_por_codigo_empresa("VISION-CLARA")
    assert context.database_name == "tenant_vision_clara"
    assert not hasattr(context, "client_database_name")


def test_make_url_set_preserves_safe_url_parts():
    base = make_url("postgresql+psycopg://user:p%40ss@db.example:5432/control?sslmode=require")
    derived = base.set(database="tenant_one")
    assert derived.database == "tenant_one"
    assert derived.username == base.username and derived.password == base.password
    assert derived.host == base.host and derived.port == base.port
    assert derived.query == base.query


def active_context(name="tenant_one", tenant_id=1):
    return TenantContext(1, "ACME", "acme", "ACTIVA", 2, "PRO", "ACTIVA",
                         date(2026, 1, 1), date(2026, 12, 31), tenant_id,
                         name, "ACTIVA", "v1")


class FakeEngine:
    def __init__(self):
        self.disposed = False
        self.executed = []
    def dispose(self):
        self.disposed = True
    @contextmanager
    def connect(self):
        yield self
    def execute(self, statement):
        self.executed.append(str(statement))


def test_engine_is_lazy_and_cached_and_separated():
    created = []
    def factory(url, **kwargs):
        engine = FakeEngine()
        created.append((url, kwargs, engine))
        return engine
    registry = TenantEngineRegistry("postgresql+psycopg://u:p@host/control", factory)
    first = registry.get_or_create(active_context())
    assert len(created) == 1
    assert registry.get_or_create(active_context()) is first
    registry.get_or_create(active_context("tenant_two", 2))
    assert len(created) == 2
    assert created[0][1]["pool_pre_ping"] is True


def test_dispose_tenant_and_all():
    engines = []
    registry = TenantEngineRegistry("postgresql+psycopg://u:p@host/control",
                                    lambda url, **kw: engines.append(FakeEngine()) or engines[-1])
    registry.get_or_create(active_context(tenant_id=1))
    registry.get_or_create(active_context("tenant_two", tenant_id=2))
    registry.dispose_tenant(1)
    assert engines[0].disposed and not engines[1].disposed
    registry.dispose_all()
    assert engines[1].disposed


def test_health_check_executes_select_one():
    engine = FakeEngine()
    registry = TenantEngineRegistry("postgresql+psycopg://u:p@host/control", lambda *a, **kw: engine)
    assert check_tenant_connection(active_context(), registry)
    assert engine.executed == ["SELECT 1"]


def test_connection_error_does_not_leak_secret():
    def fail(*args, **kwargs):
        raise RuntimeError("postgresql://user:SUPER-SECRET@host/control")
    registry = TenantEngineRegistry("postgresql+psycopg://u:p@host/control", fail)
    with pytest.raises(TenantConnectionError) as error:
        registry.get_or_create(active_context())
    assert "SUPER-SECRET" not in str(error.value)
    assert "postgresql" not in str(error.value)


def test_control_db_reuses_existing_sessionlocal(monkeypatch):
    sentinel = Row(closed=False)
    sentinel.close = lambda: setattr(sentinel, "closed", True)
    monkeypatch.setattr("app.core.tenancy.dependencies.control_session.SessionLocal", lambda: sentinel)
    assert next(get_control_db()) is sentinel


def test_pending_real_control_plane_is_not_active():
    context = resolve(make_repository(db_state="PENDIENTE")).resolver_por_codigo_empresa("VISION-CLARA")
    assert context.database_estado == "PENDIENTE"
    with pytest.raises(TenantDatabaseUnavailableError):
        TenantEngineRegistry("postgresql+psycopg://u:p@host/control").get_or_create(context)


def test_subscription_uses_application_local_date(monkeypatch):
    from app.core import time
    monkeypatch.setattr(time, "fecha_local_aplicacion", lambda: date(2026, 12, 31))
    context = TenantResolver(make_repository(end=date(2026, 12, 31))).resolver_por_codigo_empresa("VISION-CLARA")
    assert context.suscripcion_fecha_fin == date(2026, 12, 31)
