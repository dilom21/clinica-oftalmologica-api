import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import sessionmaker

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.tenancy.dependencies import get_tenant_engine_registry, revalidate_tenant_context
from app.core.tenancy.engine_registry import TenantEngineRegistry
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.database.connection import engine


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)


def open_control_session():
    return SessionLocal()


def get_legacy_login_db():
    """Keep legacy login credentials on the legacy database, regardless of bearer headers."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def clinical_claims(request: Request) -> dict | None:
    """Return verified tenant claims, or None only for an absent/legacy token."""
    authorization = request.headers.get("Authorization")
    if authorization is None:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Token inválido o expirado")
    try:
        claims = jwt.decode(parts[1], JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        token_type = claims.get("token_type")
        if token_type == "saas_admin":
            raise HTTPException(
                status_code=401,
                detail="Token SaaS no válido para rutas clínicas",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if token_type == "tenant":
            from app.core.dependencies import validate_tenant_claims
            validate_tenant_claims(claims)
            return claims
        if token_type is not None or any(
            key in claims for key in ("tenant_id", "empresa_id", "empresa_codigo")
        ):
            raise ValueError("unexpected token type")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.isdecimal() or int(subject) <= 0:
            raise ValueError("invalid subject")
        return None
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Token inválido o expirado") from exc


def get_db(
    request: Request,
    registry: TenantEngineRegistry = Depends(get_tenant_engine_registry),
):
    claims = clinical_claims(request)
    if claims is None:
        db = SessionLocal()
    else:
        control_db = None
        db = None
        try:
            control_db = open_control_session()
            context = revalidate_tenant_context(
                claims, TenantResolver(TenantControlPlaneRepository(control_db))
            )
            db = registry.get_session(context)
            # Open the connection before any clinical handler can execute.
            db.connection()
            from app.modules.gestion_usuarios_seguridad.models.models import Usuario
            user = db.get(Usuario, int(claims["sub"]))
            if user is None or not user.estado or user.rol_id != claims["rol_id"]:
                raise HTTPException(status_code=401, detail="Token tenant inválido")
        except ValueError as exc:
            if db is not None:
                db.close()
            raise HTTPException(status_code=401, detail="Token tenant inválido") from exc
        except HTTPException:
            if db is not None:
                db.close()
            raise
        except Exception as exc:
            if db is not None:
                db.close()
            raise HTTPException(status_code=503, detail="El tenant no está disponible") from exc
        finally:
            if control_db is not None:
                control_db.close()

    try:
        yield db
    finally:
        db.close()
