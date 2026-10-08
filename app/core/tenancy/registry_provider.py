"""Shared provider for the tenant engine registry.

Kept in its own module so both ``app.database.session`` and
``app.core.tenancy.dependencies`` can depend on the same singleton without
creating an import cycle. ``app.core.tenancy.dependencies`` re-exports these
names to preserve the historical import surface.
"""

from app.core.tenancy.engine_registry import TenantEngineRegistry


tenant_engine_registry = TenantEngineRegistry()


def get_tenant_engine_registry() -> TenantEngineRegistry:
    return tenant_engine_registry
