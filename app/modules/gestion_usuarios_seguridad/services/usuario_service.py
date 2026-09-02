from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    UsuarioActualizar,
    UsuarioCrear,
    UsuarioEstadoActualizar,
)


# =========================================================
# HELPERS
# =========================================================

def _obtener_rol_activo(
    db: Session,
    rol_id: int,
):
    rol = repo.obtener_rol_por_id(db, rol_id)

    if not rol:
        raise HTTPException(
            status_code=400,
            detail="El rol no existe",
        )

    if not rol.estado:
        raise HTTPException(
            status_code=400,
            detail="El rol está inactivo",
        )

    return rol


def _obtener_usuario_o_404(
    db: Session,
    usuario_id: int,
):
    usuario = repo.obtener_usuario_por_id(db, usuario_id)

    if not usuario:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado",
        )

    return usuario


def crear_usuario(
    db: Session,
    datos: UsuarioCrear,
    administrador,
    ip: str | None = None,
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

    rol = _obtener_rol_activo(db, datos.rol_id)

    try:
        # El estado inicial siempre es True; el cliente no decide el estado.
        usuario = repo.crear_usuario(
            db=db,
            correo=datos.correo,
            password_hash=hash_password(datos.password),
            rol_id=datos.rol_id,
            estado=True,
        )

        repo.registrar_bitacora(
            db=db,
            usuario_id=administrador.id,
            accion="CREAR_USUARIO",
            ip=ip,
            entidad_afectada="usuario",
            id_registro_afectado=usuario.id,
            descripcion=(
                f"Usuario '{datos.correo}' creado "
                f"con rol '{rol.nombre}'"
            ),
        )

        db.commit()
        db.refresh(usuario)

        return usuario

    except Exception:
        db.rollback()
        raise


def actualizar_usuario(
    db: Session,
    usuario_id: int,
    datos: UsuarioActualizar,
    administrador,
    ip: str | None = None,
):
    if (
        datos.correo is None
        and datos.password is None
        and datos.rol_id is None
    ):
        raise HTTPException(
            status_code=400,
            detail="No se enviaron campos para actualizar",
        )

    usuario = _obtener_usuario_o_404(db, usuario_id)

    if (
        datos.correo is not None
        and datos.correo != usuario.correo
    ):
        otro_usuario = repo.obtener_usuario_por_correo(
            db,
            datos.correo,
        )

        if otro_usuario and otro_usuario.id != usuario.id:
            raise HTTPException(
                status_code=409,
                detail="El correo ya está registrado",
            )

    rol = None
    if datos.rol_id is not None:
        rol = _obtener_rol_activo(db, datos.rol_id)

    nuevo_password_hash = None
    if datos.password is not None:
        nuevo_password_hash = hash_password(datos.password)

    cambios = []
    if datos.correo is not None and datos.correo != usuario.correo:
        cambios.append("correo")
    if datos.rol_id is not None and datos.rol_id != usuario.rol_id:
        cambios.append(f"rol a '{rol.nombre}'")
    if nuevo_password_hash is not None:
        cambios.append("contraseña")

    try:
        usuario = repo.actualizar_usuario(
            db=db,
            usuario=usuario,
            correo=datos.correo,
            rol_id=datos.rol_id,
            password_hash=nuevo_password_hash,
        )

        repo.registrar_bitacora(
            db=db,
            usuario_id=administrador.id,
            accion="ACTUALIZAR_USUARIO",
            ip=ip,
            entidad_afectada="usuario",
            id_registro_afectado=usuario.id,
            descripcion=(
                "Usuario actualizado: "
                + (", ".join(cambios) or "sin cambios")
            ),
        )

        db.commit()
        db.refresh(usuario)

        return usuario

    except Exception:
        db.rollback()
        raise


def actualizar_estado_usuario(
    db: Session,
    usuario_id: int,
    datos: UsuarioEstadoActualizar,
    administrador,
    ip: str | None = None,
):
    usuario = _obtener_usuario_o_404(db, usuario_id)

    if not datos.estado and usuario.id == administrador.id:
        raise HTTPException(
            status_code=400,
            detail="No puedes deshabilitar tu propia cuenta",
        )

    accion = (
        "DESHABILITAR_USUARIO"
        if not datos.estado
        else "HABILITAR_USUARIO"
    )

    try:
        usuario = repo.actualizar_estado_usuario(
            db=db,
            usuario=usuario,
            estado=datos.estado,
        )

        repo.registrar_bitacora(
            db=db,
            usuario_id=administrador.id,
            accion=accion,
            ip=ip,
            entidad_afectada="usuario",
            id_registro_afectado=usuario.id,
            descripcion=(
                f"Cuenta del usuario '{usuario.correo}' "
                f"{'deshabilitada' if not datos.estado else 'habilitada'}"
            ),
        )

        db.commit()
        db.refresh(usuario)

        return usuario

    except Exception:
        db.rollback()
        raise


def asignar_rol_usuario(
    db: Session,
    usuario_id: int,
    rol_id: int,
    administrador=None,
    ip: str | None = None,
):
    usuario = _obtener_usuario_o_404(db, usuario_id)
    rol = _obtener_rol_activo(db, rol_id)

    try:
        repo.asignar_rol_usuario(
            db,
            usuario_id,
            rol_id,
        )

        repo.registrar_bitacora(
            db=db,
            usuario_id=administrador.id if administrador else None,
            accion="ASIGNAR_ROL",
            ip=ip,
            entidad_afectada="usuario",
            id_registro_afectado=usuario_id,
            descripcion=f"Rol '{rol.nombre}' asignado al usuario",
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
    return _obtener_usuario_o_404(db, usuario_id)
 