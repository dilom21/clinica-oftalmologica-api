import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.database.session import SessionLocal
from app.modules.gestion_usuarios_seguridad.models.models import (
    Funcion,
    RolFuncion,
    Usuario,
)


bearer_scheme = HTTPBearer()


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def obtener_usuario_actual(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
        usuario_id = int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    usuario = db.get(Usuario, usuario_id)
    if not usuario or not usuario.estado:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return usuario


def obtener_administrador_actual(
    usuario: Usuario = Depends(obtener_usuario_actual),
) -> Usuario:
    nombre_rol = usuario.rol.nombre.lower() if usuario.rol else ""
    if nombre_rol not in {"admin", "administrador"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere un rol administrador",
        )

    return usuario


def requerir_permiso(nombre_funcion: str):
    def validar_permiso(
        usuario: Usuario = Depends(obtener_usuario_actual),
        db: Session = Depends(get_db),
    ) -> Usuario:
        permiso = db.scalar(
            select(RolFuncion.id)
            .join(Funcion, Funcion.id == RolFuncion.funcion_id)
            .where(
                RolFuncion.rol_id == usuario.rol_id,
                Funcion.nombre == nombre_funcion,
                Funcion.estado.is_(True),
            )
            .limit(1)
        )
        if permiso is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tiene permiso para {nombre_funcion}",
            )

        return usuario

    return validar_permiso
