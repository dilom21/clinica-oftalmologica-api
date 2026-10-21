"""Immutable, non-sensitive tenant resolution result."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class TenantContext:
    empresa_id: int
    empresa_codigo: str
    empresa_slug: str | None
    empresa_estado: str
    suscripcion_id: int
    plan_codigo: str
    suscripcion_estado: str
    suscripcion_fecha_inicio: date
    suscripcion_fecha_fin: date
    tenant_database_id: int
    database_name: str
    database_estado: str
    version_schema: str | None
