from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import crear_access_token, verificar_password
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    LoginRequest,
    LoginResponse,
)


def iniciar_sesion(
    db: Session,
    datos: LoginRequest,
    ip: str | None = None,
) -> LoginResponse:
    usuario = repo.obtener_usuario_por_correo(
        db,
        datos.correo,
    )

    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    if not usuario.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo",
        )

    if not verificar_password(
        datos.password,
        usuario.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    try:
        repo.registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="LOGIN",
            entidad_afectada="usuario",
            id_registro_afectado=usuario.id,
            descripcion="Inicio de sesión exitoso",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    access_token = crear_access_token(
        usuario_id=usuario.id,
        rol_id=usuario.rol_id,
    )

    return LoginResponse(access_token=access_token)
