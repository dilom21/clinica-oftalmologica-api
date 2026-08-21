from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import UsuarioCrear


def crear_usuario(
    db: Session,
    datos: UsuarioCrear,
):
    existente = repo.obtener_usuario_por_correo(
        db,
        datos.correo,
    )

    if existente:
        raise HTTPException(
            status_code=409,
            detail="El correo ya está registrado",
        )

    try:
        usuario = repo.crear_usuario(
            db=db,
            correo=datos.correo,
            password_hash=hash_password(datos.password),
            estado=datos.estado,
        )

        repo.registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="CREAR_USUARIO",
            entidad_afectada="usuario",
            id_registro_afectado=usuario.id,
            descripcion="Usuario registrado en el sistema",
        )

        db.commit()
        db.refresh(usuario)

        return usuario

    except Exception:
        db.rollback()
        raise


def listar_usuarios(db: Session):
    return repo.listar_usuarios(db)


def obtener_usuario(
    db: Session,
    usuario_id: int,
):
    usuario = repo.obtener_usuario_por_id(
        db,
        usuario_id,
    )

    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    return usuario
