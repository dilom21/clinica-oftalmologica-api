"""Lazy, thread-safe registry of tenant engines and session factories."""

from dataclasses import dataclass
from threading import RLock

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core import config
from app.core.tenancy.context import TenantContext
from app.core.tenancy.exceptions import (
    TenantConnectionError,
    TenantDatabaseUnavailableError,
    InvalidTenantDatabaseNameError,
)
from app.core.tenancy.resolver import DATABASE_NAME_PATTERN


@dataclass(frozen=True, slots=True)
class TenantConnection:
    engine: object
    session_factory: sessionmaker


class TenantEngineRegistry:
    def __init__(self, base_url: str | URL | None = None, engine_factory=None):
        self._base_url = make_url(base_url or config.DATABASE_URL)
        self._engine_factory = engine_factory or create_engine
        self._items: dict[tuple[int, str, str | None], TenantConnection] = {}
        self._lock = RLock()

    def get_or_create(self, context: TenantContext) -> TenantConnection:
        if context.database_estado != "ACTIVA":
            raise TenantDatabaseUnavailableError("Base de datos tenant no disponible")
        if not isinstance(context.database_name, str) or not DATABASE_NAME_PATTERN.fullmatch(
            context.database_name
        ):
            raise InvalidTenantDatabaseNameError("Nombre de base de datos inválido")
        tenant_url = self._base_url.set(database=context.database_name)
        key = (context.tenant_database_id, context.database_name, context.version_schema)
        with self._lock:
            current = self._items.get(key)
            if current is not None:
                return current
            try:
                engine = self._engine_factory(tenant_url, pool_pre_ping=True)
                factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            except Exception as exc:
                raise TenantConnectionError("No se pudo preparar la conexión tenant") from exc
            current = TenantConnection(engine, factory)
            self._items[key] = current
            return current

    def get_session(self, context: TenantContext) -> Session:
        return self.get_or_create(context).session_factory()

    def dispose_tenant(self, tenant_database_id: int) -> None:
        with self._lock:
            matches = [key for key in self._items if key[0] == tenant_database_id]
            for key in matches:
                self._items.pop(key).engine.dispose()

    def dispose_all(self) -> None:
        with self._lock:
            items = list(self._items.values())
            self._items.clear()
            for item in items:
                item.engine.dispose()

    def check_tenant_connection(self, context: TenantContext) -> bool:
        connection = self.get_or_create(context)
        try:
            with connection.engine.connect() as db_connection:
                db_connection.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            raise TenantConnectionError("No se pudo comprobar la conexión tenant") from exc


def derive_tenant_url(base_url: str | URL, database_name: str) -> URL:
    """Derive a URL without parsing or rebuilding credentials manually."""
    return make_url(base_url).set(database=database_name)


def check_tenant_connection(context: TenantContext, registry: TenantEngineRegistry) -> bool:
    return registry.check_tenant_connection(context)
