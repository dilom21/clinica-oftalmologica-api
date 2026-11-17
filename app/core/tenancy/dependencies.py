"""Control-plane and tenant session helpers."""

from collections.abc import Generator

from app.database import session as control_session
from app.core.tenancy.context import TenantContext
from app.core.tenancy.engine_registry import TenantEngineRegistry
from app.core.tenancy.registry_provider import (
    get_tenant_engine_registry,
    tenant_engine_registry,
)
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver


def get_control_db() -> Generator:
    """Yield a control-plane session from the existing SessionLocal."""
    db = control_session.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_tenant_db_from_context(
    context: TenantContext,
    registry: TenantEngineRegistry,
) -> Generator:
    """Future explicit dependency; no JWT or request integration is performed."""
    db = registry.get_session(context)
    try:
        yield db
    finally:
        db.close()


def revalidate_tenant_context(claims: dict, resolver: TenantResolver) -> TenantContext:
    """Re-resolve current Control Plane state and compare signed identity."""
    context = resolver.resolver_por_codigo_empresa(claims["empresa_codigo"])
    resolver.require_active_database(context)
    if (
        context.tenant_database_id != claims["tenant_id"]
        or context.empresa_id != claims["empresa_id"]
        or context.empresa_codigo != claims["empresa_codigo"]
    ):
        raise ValueError("El contexto tenant no coincide con el token")
    return context
