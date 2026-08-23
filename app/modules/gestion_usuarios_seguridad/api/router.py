from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    LoginRequest,
    LoginResponse,
    UsuarioCrear,
    UsuarioRespuesta,
    RolCrear,
    RolRespuesta,
    BitacoraRespuesta,
    MenuModuloRespuesta,
)

from app.modules.gestion_usuarios_seguridad.services import (
    auth_service,
    bitacora_service,
    menu_service,
    rol_service,
    usuario_service,
)


router = APIRouter(
    prefix="/seguridad",
    tags=["Usuarios y Seguridad"],
)


# =========================================================
# CU01 - INICIAR SESIÓN
# =========================================================

@router.post(
    "/login",
    response_model=LoginResponse,
)
def iniciar_sesion(
    datos: LoginRequest,
    db: Session = Depends(get_db),
):
    return auth_service.iniciar_sesion(db, datos)


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
    return usuario_service.crear_usuario(db, datos)


@router.get(
    "/usuarios",
    response_model=list[UsuarioRespuesta],
)
def listar_usuarios(
    db: Session = Depends(get_db),
):
    return usuario_service.listar_usuarios(db)


@router.get(
    "/usuarios/{usuario_id}",
    response_model=UsuarioRespuesta,
)
def obtener_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
):
    return usuario_service.obtener_usuario(
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
    return rol_service.crear_rol(db, datos)


@router.get(
    "/roles",
    response_model=list[RolRespuesta],
)
def listar_roles(
    db: Session = Depends(get_db),
):
    return rol_service.listar_roles(db)


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
    return bitacora_service.consultar_bitacora(db)


# =========================================================
# MENÚ DINÁMICO
# =========================================================

@router.get(
    "/menu",
    response_model=list[MenuModuloRespuesta],
)
def obtener_menu(
    db: Session = Depends(get_db),
):
    return menu_service.obtener_menu(db)
