import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.tenancy.dependencies import get_control_db

from .models import SaasUsuario

bearer = HTTPBearer(auto_error=False)


def get_saas_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_control_db),
) -> SaasUsuario:
    try:
        if credentials is None:
            raise ValueError
        payload = jwt.decode(credentials.credentials, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("token_type") != "saas_admin":
            raise ValueError
        user_id = payload.get("saas_usuario_id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError
        if payload.get("sub") != str(user_id):
            raise ValueError
    except (jwt.InvalidTokenError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Token SaaS inválido o expirado",
                            headers={"WWW-Authenticate": "Bearer"})
    user = db.get(SaasUsuario, user_id)
    if not user or not user.estado:
        raise HTTPException(status_code=401, detail="Usuario SaaS no encontrado o inactivo",
                            headers={"WWW-Authenticate": "Bearer"})
    if user.rol not in {"SUPERADMIN", "OPERADOR"}:
        raise HTTPException(status_code=403, detail="El usuario no tiene un rol SaaS administrativo")
    return user


def get_saas_superadmin(
    usuario: SaasUsuario = Depends(get_saas_admin),
) -> SaasUsuario:
    """Authorize destructive/control-plane configuration operations."""
    if usuario.rol != "SUPERADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La operación requiere rol SUPERADMIN",
        )
    return usuario
