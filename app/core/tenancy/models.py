"""Compatibility exports for the control-plane SQLAlchemy mappings."""

from app.modules.administracion_saas.models import (
    Empresa,
    PlanSaas,
    ProvisionamientoTenant,
    SaasBitacora,
    SaasUsuario,
    Suscripcion,
    TenantDatabase,
)

__all__ = [
    "Empresa", "PlanSaas", "Suscripcion", "TenantDatabase",
    "SaasUsuario", "SaasBitacora", "ProvisionamientoTenant",
]
