"""Resolve logical company identity into safe tenant metadata."""

from datetime import date
import re

from app.core import time
from app.core.tenancy.context import TenantContext
from app.core.tenancy.exceptions import (
    ControlPlaneConsistencyError,
    InvalidTenantDatabaseNameError,
    SubscriptionInactiveError,
    TenantDatabaseUnavailableError,
    TenantInactiveError,
    TenantNotFoundError,
)
from app.core.tenancy.repository import TenantControlPlaneRepository


DATABASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class TenantResolver:
    def __init__(self, repository: TenantControlPlaneRepository, today=None):
        self.repository = repository
        self._today = today or time.fecha_local_aplicacion

    def resolver_por_codigo_empresa(self, codigo: str) -> TenantContext:
        empresa = self.repository.get_empresa_by_codigo(codigo)
        if empresa is None:
            raise TenantNotFoundError("Empresa no encontrada")
        if empresa.estado != "ACTIVA":
            raise TenantInactiveError("Empresa no está activa")

        hoy: date = self._today()
        suscripciones_activas = [
            item
            for item in self.repository.get_suscripciones(empresa.id)
            if item.estado == "ACTIVA"
        ]
        if len(suscripciones_activas) > 1:
            raise ControlPlaneConsistencyError(
                "Configuración de suscripción inconsistente"
            )

        suscripciones = [
            item for item in suscripciones_activas
            if item.fecha_inicio <= hoy and item.fecha_fin >= hoy
        ]
        if len(suscripciones) != 1:
            raise SubscriptionInactiveError("Suscripción no vigente")

        suscripcion = suscripciones[0]
        plan = self.repository.get_plan(suscripcion.plan_id)
        tenant_database = self.repository.get_tenant_database(empresa.id)
        if plan is None or tenant_database is None:
            raise TenantNotFoundError("Configuración de tenant incompleta")
        self._validar_database_name(tenant_database.database_name)

        # PENDIENTE is valid metadata, but is intentionally rejected by session().
        return TenantContext(
            empresa_id=empresa.id,
            empresa_codigo=empresa.codigo,
            empresa_slug=empresa.slug,
            empresa_estado=empresa.estado,
            suscripcion_id=suscripcion.id,
            plan_codigo=plan.codigo,
            suscripcion_estado=suscripcion.estado,
            suscripcion_fecha_inicio=suscripcion.fecha_inicio,
            suscripcion_fecha_fin=suscripcion.fecha_fin,
            tenant_database_id=tenant_database.id,
            database_name=tenant_database.database_name,
            database_estado=tenant_database.estado,
            version_schema=tenant_database.version_schema,
        )

    @staticmethod
    def _validar_database_name(database_name: str) -> None:
        if not isinstance(database_name, str) or not DATABASE_NAME_PATTERN.fullmatch(database_name):
            raise InvalidTenantDatabaseNameError("Nombre de base de datos inválido")

    def require_active_database(self, context: TenantContext) -> None:
        self._validar_database_name(context.database_name)
        if context.database_estado != "ACTIVA":
            raise TenantDatabaseUnavailableError("Base de datos tenant no disponible")
