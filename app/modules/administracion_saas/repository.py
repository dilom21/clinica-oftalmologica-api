from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (Empresa, PlanSaas, ProvisionamientoTenant, SaasBitacora,
                     SaasUsuario, Suscripcion, TenantDatabase)


class SaasRepository:
    def __init__(self, db: Session):
        self.db = db

    def find_user(self, correo: str):
        return self.db.scalars(select(SaasUsuario).where(SaasUsuario.correo == correo)).first()

    def empresas(self):
        rows = self.db.execute(
            select(Empresa, PlanSaas.codigo, Suscripcion.estado, TenantDatabase)
            .outerjoin(Suscripcion, Suscripcion.empresa_id == Empresa.id)
            .outerjoin(PlanSaas, PlanSaas.id == Suscripcion.plan_id)
            .outerjoin(TenantDatabase, TenantDatabase.empresa_id == Empresa.id)
            .order_by(Empresa.id)
        ).all()
        result = []
        seen = set()
        for empresa, plan, sub_estado, tenant in rows:
            if empresa.id in seen:
                continue
            seen.add(empresa.id)
            result.append((empresa, plan, sub_estado, tenant))
        return result

    def planes(self):
        return list(self.db.scalars(select(PlanSaas).order_by(PlanSaas.id)).all())

    def suscripciones(self):
        return list(self.db.scalars(select(Suscripcion).order_by(Suscripcion.id)).all())

    def tenants(self):
        return list(self.db.scalars(select(TenantDatabase).order_by(TenantDatabase.id)).all())

    def provisionamientos(self):
        return list(self.db.scalars(select(ProvisionamientoTenant).order_by(ProvisionamientoTenant.id)).all())

    def bitacora(self):
        return list(self.db.scalars(select(SaasBitacora).order_by(SaasBitacora.id.desc())).all())

    def get_empresa(self, empresa_id: int):
        return self.db.get(Empresa, empresa_id)

    def get_suscripcion(self, suscripcion_id: int):
        return self.db.get(Suscripcion, suscripcion_id)

    def log(self, user_id, action, entity=None, record_id=None, description=None, ip=None, result="EXITO"):
        # PostgreSQL owns id generation (identity/autoincrement), so the ORM insert
        # must not send an explicit id. The SQLite in-memory control plane used by
        # tests cannot autoincrement BIGINT primary keys, so it gets a fallback.
        values = dict(
            saas_usuario_id=user_id,
            fecha_hora=datetime.now(timezone.utc),
            accion=action,
            entidad_afectada=entity,
            id_registro_afectado=record_id,
            descripcion=description,
            ip=ip,
            resultado=result,
        )
        if self.db.get_bind().dialect.name == "sqlite":
            values["id"] = (self.db.scalar(select(func.max(SaasBitacora.id))) or 0) + 1
        self.db.add(SaasBitacora(**values))
