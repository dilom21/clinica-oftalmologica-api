from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import hash_password, verificar_password

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo

from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    UsuarioCrear,
    RolCrear,
)


# =========================================================
# CU01 - INICIAR SESIÓN
# =========================================================

def autenticar_usuario(
    db: Session,
    correo: str,
    password: str,
):
    usuario = repo.obtener_usuario_por_correo(db, correo)

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
        password,
        usuario.password_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo o contraseña incorrectos",
        )

    return usuario


# =========================================================
# CU04 - GESTIONAR USUARIOS
# =========================================================

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


# =========================================================
# CU05 - ROLES
# =========================================================

def crear_rol(
    db: Session,
    datos: RolCrear,
):
    try:
        rol = repo.crear_rol(
            db=db,
            nombre=datos.nombre,
            descripcion=datos.descripcion,
            estado=datos.estado,
        )

        db.commit()
        db.refresh(rol)

        return rol

    except Exception:
        db.rollback()
        raise


def listar_roles(db: Session):
    return repo.listar_roles(db)


def asignar_rol(
    db: Session,
    usuario_id: int,
    rol_id: int,
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

    rol = repo.obtener_rol_por_id(
        db,
        rol_id,
    )

    if not rol:
        raise HTTPException(
            status_code=404,
            detail="Rol no encontrado",
        )

    try:
        asignacion = repo.asignar_rol_usuario(
            db,
            usuario_id,
            rol_id,
        )

        db.commit()

        return asignacion

    except Exception:
        db.rollback()
        raise


# =========================================================
# CU06 - BITÁCORA
# =========================================================

def consultar_bitacora(db: Session):
    return repo.listar_bitacora(db)