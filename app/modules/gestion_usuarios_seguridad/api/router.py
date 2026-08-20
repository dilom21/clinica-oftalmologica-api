from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    UsuarioCrear,
    UsuarioRespuesta,
    RolCrear,
    RolRespuesta,
    UsuarioRolCrear,
    BitacoraRespuesta,
)

from app.modules.gestion_usuarios_seguridad.services import service


router = APIRouter(
    prefix="/seguridad",
    tags=["Usuarios y Seguridad"],
)


# =========================================================
# CU04 - USUARIOS
# =========================================================

@router.post(
    "/usuarios",
    response_model=UsuarioRespuesta,
    status_code=201,
)
def crear_usuario(
    datos: UsuarioCrear,
    db: Session = Depends(get_db),
):
    return service.crear_usuario(db, datos)


@router.get(
    "/usuarios",
    response_model=list[UsuarioRespuesta],
)
def listar_usuarios(
    db: Session = Depends(get_db),
):
    return service.listar_usuarios(db)


@router.get(
    "/usuarios/{usuario_id}",
    response_model=UsuarioRespuesta,
)
def obtener_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
):
    return service.obtener_usuario(
        db,
        usuario_id,
    )


# =========================================================
# CU05 - ROLES
# =========================================================

@router.post(
    "/roles",
    response_model=RolRespuesta,
    status_code=201,
)
def crear_rol(
    datos: RolCrear,
    db: Session = Depends(get_db),
):
    return service.crear_rol(db, datos)


@router.get(
    "/roles",
    response_model=list[RolRespuesta],
)
def listar_roles(
    db: Session = Depends(get_db),
):
    return service.listar_roles(db)


@router.post("/usuarios/asignar-rol")
def asignar_rol(
    datos: UsuarioRolCrear,
    db: Session = Depends(get_db),
):
    service.asignar_rol(
        db,
        datos.usuario_id,
        datos.rol_id,
    )

    return {
        "mensaje": "Rol asignado correctamente"
    }


# =========================================================
# CU06 - BITÁCORA
# =========================================================

@router.get(
    "/bitacora",
    response_model=list[BitacoraRespuesta],
)
def consultar_bitacora(
    db: Session = Depends(get_db),
):
    return service.consultar_bitacora(db)