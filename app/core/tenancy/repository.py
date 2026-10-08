"""Read-only access to the SaaS control plane."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase


class TenantControlPlaneRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_empresa_by_codigo(self, codigo: str) -> Empresa | None:
        return self.db.scalars(
            select(Empresa).where(Empresa.codigo == codigo)
        ).first()

    def list_active_empresas(self) -> list[Empresa]:
        return list(self.db.scalars(
            select(Empresa).where(Empresa.estado == "ACTIVA").order_by(Empresa.codigo)
        ).all())

    def get_suscripciones(self, empresa_id: int) -> list[Suscripcion]:
        return list(self.db.scalars(
            select(Suscripcion).where(Suscripcion.empresa_id == empresa_id)
        ).all())

    def get_plan(self, plan_id: int) -> PlanSaas | None:
        return self.db.get(PlanSaas, plan_id)

    def get_tenant_database(self, empresa_id: int) -> TenantDatabase | None:
        return self.db.scalars(
            select(TenantDatabase).where(TenantDatabase.empresa_id == empresa_id)
        ).first()

    # Explicit aliases make the repository convenient to fake in focused tests.
    find_empresa_by_codigo = get_empresa_by_codigo
    list_suscripciones = get_suscripciones
    find_plan = get_plan
    find_tenant_database = get_tenant_database
