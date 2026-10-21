"""Tenant login orchestration; the legacy login remains in auth_service."""

from collections.abc import Callable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import crear_tenant_access_token, verificar_password
from app.core.tenancy.engine_registry import TenantEngineRegistry
from app.core.tenancy.exceptions import (
    ControlPlaneConsistencyError,
    InvalidTenantDatabaseNameError,
    SubscriptionInactiveError,
    TenantConnectionError,
    TenantDatabaseUnavailableError,
    TenantInactiveError,
    TenantNotFoundError,
)
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    LoginResponse,
    TenantLoginRequest,
)


class TenantUserRepository:
    """Default adapter; tests can replace it with an in-memory fake."""

    def __init__(self, db: Session):
        self.db = db

    def get_usuario_by_correo(self, correo: str) -> Usuario | None:
        return self.db.scalars(
            select(Usuario).where(Usuario.correo == correo)
        ).first()


def _tenant_unavailable(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="El tenant no está disponible",
    )


def autenticar_usuario_tenant(
    datos: TenantLoginRequest,
    control_plane_repository: TenantControlPlaneRepository,
    tenant_registry: TenantEngineRegistry,
    user_repository_factory: Callable[[Session], object] = TenantUserRepository,
    resolver: TenantResolver | None = None,
) -> LoginResponse:
    """Resolve active control-plane metadata before opening a tenant session."""
    resolver = resolver or TenantResolver(control_plane_repository)
    try:
        context = resolver.resolver_por_codigo_empresa(datos.empresa_codigo)
        # This check intentionally precedes registry.get_session: PENDIENTE
        # must not create an engine or touch a physical tenant database.
        resolver.require_active_database(context)
        tenant_db = tenant_registry.get_session(context)
    except (TenantNotFoundError, TenantInactiveError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
        ) from exc
    except SubscriptionInactiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La suscripción no está vigente",
        ) from exc
    except (
        TenantDatabaseUnavailableError,
        TenantConnectionError,
        InvalidTenantDatabaseNameError,
        ControlPlaneConsistencyError,
    ) as exc:
        raise _tenant_unavailable(exc) from exc

    try:
        user = user_repository_factory(tenant_db).get_usuario_by_correo(datos.correo)
        if user is None or not verificar_password(datos.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales inválidas",
            )
        if not user.estado:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Usuario inactivo",
            )
        return LoginResponse(
            access_token=crear_tenant_access_token(
                usuario_id=user.id,
                rol_id=user.rol_id,
                tenant_id=context.tenant_database_id,
                empresa_id=context.empresa_id,
                empresa_codigo=context.empresa_codigo,
            )
        )
    finally:
        tenant_db.close()
