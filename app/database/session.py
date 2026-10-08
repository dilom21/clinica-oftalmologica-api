"""Database session dependency.

The legacy login keeps using the original database. Clinical requests are
routed by the *signed* JWT they carry:

* no token / legacy token  -> original database (legacy behaviour);
* valid tenant token       -> the company's own database, resolved on the
                              server from the SaaS control plane;
* SaaS admin token         -> rejected (never a clinical resource).

A tenant resolution failure never falls back to the legacy database, and no
internal database name or credential is ever exposed to the client.
"""

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.tenancy.exceptions import (
    SubscriptionInactiveError,
    TenantError,
    TenantInactiveError,
    TenantNotFoundError,
)
from app.core.tenancy.registry_provider import get_tenant_engine_registry
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.database.connection import engine
from app.modules.gestion_usuarios_seguridad.models.models import Usuario


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)

_TENANT_REQUIRED_CLAIMS = {"sub", "rol_id", "tenant_id", "empresa_id", "empresa_codigo"}


def open_control_session() -> Session:
    """Session for the SaaS control plane (colocated with the legacy database).

    It is a separate factory because tests monkeypatch it to inject an isolated
    control plane; tenant connections must never be taken from here.
    """
    return SessionLocal()


def get_db_legacy():
    """Legacy-only session, used by the pre-authentication endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def _unauthorized(detail: str = "Token inválido o expirado") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _tenant_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="El tenant no está disponible",
    )


def _legacy_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _open_tenant_session(claims: dict, registry) -> Session:
    """Resolve the current Control Plane state and open the tenant session."""
    if claims.get("token_type") != "tenant" or not _TENANT_REQUIRED_CLAIMS.issubset(claims):
        raise _unauthorized()

    control = None
    try:
        try:
            control = open_control_session()
            resolver = TenantResolver(TenantControlPlaneRepository(control))
            context = resolver.resolver_por_codigo_empresa(claims["empresa_codigo"])
            resolver.require_active_database(context)
        except HTTPException:
            raise
        except (TenantNotFoundError, TenantInactiveError) as exc:
            raise _unauthorized("Credenciales inválidas") from exc
        except SubscriptionInactiveError as exc:
            raise _unauthorized("La suscripción no está vigente") from exc
        except TenantError as exc:
            raise _tenant_unavailable() from exc
        except Exception as exc:
            raise _tenant_unavailable() from exc
    finally:
        if control is not None:
            control.close()

    # The signed identity must still match the freshly resolved metadata: a
    # token minted for another company (or a stale database) is rejected.
    if (
        context.empresa_id != claims.get("empresa_id")
        or context.tenant_database_id != claims.get("tenant_id")
        or context.empresa_codigo != claims.get("empresa_codigo")
    ):
        raise _unauthorized("El contexto tenant no coincide con el token")

    try:
        db = registry.get_session(context)
    except Exception as exc:
        raise _tenant_unavailable() from exc

    try:
        usuario = db.get(Usuario, int(claims["sub"]))
    except Exception as exc:
        db.close()
        raise _tenant_unavailable() from exc
    if usuario is None or not usuario.estado:
        db.close()
        raise _unauthorized("Usuario no encontrado o inactivo")
    # The role is trusted only after confirming it against the tenant database.
    if usuario.rol_id != claims.get("rol_id"):
        db.close()
        raise _unauthorized("Token no válido para recursos clínicos")
    # Internal, server-validated context for downstream integrations. It is
    # never populated from request bodies or arbitrary headers.
    db.info["tenant_context"] = context
    return db


def get_db(
    request: Request,
    registry=Depends(get_tenant_engine_registry),
):
    token = _extract_bearer(request)
    if token is None:
        yield from _legacy_session()
        return

    try:
        claims = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise _unauthorized()

    token_type = claims.get("token_type")
    if token_type is None:
        # Legacy clinical token: unchanged behaviour.
        yield from _legacy_session()
        return
    if token_type != "tenant":
        # SaaS admin (or unknown) tokens never grant clinical access.
        if token_type == "saas_admin":
            raise _unauthorized("Token SaaS no válido para rutas clínicas")
        raise _unauthorized("Token no válido para recursos clínicos")

    db = _open_tenant_session(claims, registry)
    try:
        yield db
    finally:
        db.close()
