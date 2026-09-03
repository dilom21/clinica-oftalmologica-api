from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.core.dependencies import obtener_administrador_actual, requerir_permiso

from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    LoginRequest,
    LoginResponse,
    RecuperarPasswordRequest,
    RestablecerPasswordRequest,
    UsuarioCrear,
    UsuarioActualizar,
    UsuarioEstadoActualizar,
    UsuarioRespuesta,
    AsignarRolRequest,
    RolCrearConPermisos,
    RolActualizarConPermisos,
    RolRespuesta,
    PermisoRolRespuesta,
    ModuloConFuncionesRespuesta,
    AccionRespuesta,
    BitacoraRespuesta,
    BitacoraPaginadaRespuesta,
    BitacoraFiltros,
    MenuModuloRespuesta,
)

from app.modules.gestion_usuarios_seguridad.services import (
    auth_service,
    bitacora_service,
    menu_service,
    password_service,
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
    request: Request,
    datos: LoginRequest,
    db: Session = Depends(get_db),
):
    return auth_service.iniciar_sesion(
        db,
        datos,
        request.client.host if request.client else None,
    )


# =========================================================
# CU03 - RECUPERAR CONTRASEÑA
# =========================================================

@router.post("/password/recuperar")
def recuperar_password(
    datos: RecuperarPasswordRequest,
    db: Session = Depends(get_db),
):
    return password_service.solicitar_recuperacion_password(db, datos)


@router.post("/password/restablecer")
def restablecer_password(
    datos: RestablecerPasswordRequest,
    db: Session = Depends(get_db),
):
    return password_service.restablecer_password(db, datos)


# =========================================================
# CU04 - USUARIOS
# =========================================================

@router.post(
    "/usuarios",
    response_model=UsuarioRespuesta,
    status_code=201,
)
def crear_usuario(
    request: Request,
    datos: UsuarioCrear,
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return usuario_service.crear_usuario(
        db,
        datos,
        administrador,
        request.client.host if request.client else None,
    )


@router.get(
    "/usuarios",
    response_model=list[UsuarioRespuesta],
)
def listar_usuarios(
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return usuario_service.listar_usuarios(db)


@router.get(
    "/usuarios/{usuario_id}",
    response_model=UsuarioRespuesta,
)
def obtener_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return usuario_service.obtener_usuario(
        db,
        usuario_id,
    )


@router.put(
    "/usuarios/{usuario_id}",
    response_model=UsuarioRespuesta,
)
def actualizar_usuario(
    request: Request,
    usuario_id: int,
    datos: UsuarioActualizar,
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return usuario_service.actualizar_usuario(
        db,
        usuario_id,
        datos,
        administrador,
        request.client.host if request.client else None,
    )


@router.patch(
    "/usuarios/{usuario_id}/estado",
    response_model=UsuarioRespuesta,
)
def actualizar_estado_usuario(
    request: Request,
    usuario_id: int,
    datos: UsuarioEstadoActualizar,
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return usuario_service.actualizar_estado_usuario(
        db,
        usuario_id,
        datos,
        administrador,
        request.client.host if request.client else None,
    )


@router.post(
    "/usuarios/{usuario_id}/rol",
    response_model=UsuarioRespuesta,
)
def asignar_rol(
    request: Request,
    usuario_id: int,
    datos: AsignarRolRequest,
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return usuario_service.asignar_rol_usuario(
        db,
        usuario_id,
        datos.rol_id,
        administrador,
        request.client.host if request.client else None,
    )


# =========================================================
# CU05 - ROLES Y PERMISOS
# =========================================================
permiso_gestion_roles = requerir_permiso("Gestionar roles y permisos")

@router.post(
    "/roles",
    response_model=RolRespuesta,
    status_code=201,
)
def crear_rol(
    datos: RolCrearConPermisos,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.crear_rol_con_permisos(db, datos)


@router.get(
    "/roles",
    response_model=list[RolRespuesta],
)
def listar_roles(
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.listar_roles(db)


@router.get(
    "/roles/{rol_id}",
    response_model=RolRespuesta,
)
def obtener_rol(
    rol_id: int,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.obtener_rol(db, rol_id)


@router.get(
    "/roles/{rol_id}/permisos",
    response_model=list[PermisoRolRespuesta],
)
def obtener_permisos_rol(
    rol_id: int,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.obtener_permisos_rol(db, rol_id)


@router.put(
    "/roles/{rol_id}",
    response_model=RolRespuesta,
)
def actualizar_rol(
    rol_id: int,
    datos: RolActualizarConPermisos,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.actualizar_rol_con_permisos(db, rol_id, datos)


@router.delete(
    "/roles/{rol_id}",
    response_model=RolRespuesta,
)
def desactivar_rol(
    rol_id: int,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.desactivar_rol(db, rol_id)


@router.get(
    "/modulos-funciones",
    response_model=list[ModuloConFuncionesRespuesta],
)
def listar_modulos_funciones(
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.listar_modulos_con_funciones(db)


@router.get(
    "/acciones",
    response_model=list[AccionRespuesta],
)
def listar_acciones(
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_gestion_roles),
):
    return rol_service.listar_acciones(db)


# =========================================================
# CU06 - BITÁCORA
# =========================================================

@router.get(
    "/bitacora",
    response_model=list[BitacoraRespuesta] | BitacoraPaginadaRespuesta,
)
def consultar_bitacora(
    request: Request,
    filtros: BitacoraFiltros = Depends(),
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
    page: int | None = Query(None, ge=1),
    page_size: int | None = Query(None, ge=1, le=100),
):
    registros = bitacora_service.consultar_bitacora(
        db=db,
        usuario_id=filtros.usuario_id,
        accion=filtros.accion,
        entidad_afectada=filtros.entidad_afectada,
        id_registro_afectado=filtros.id_registro_afectado,
        desde=filtros.desde,
        hasta=filtros.hasta,
        page=page,
        page_size=page_size,
    )

    bitacora_service.registrar_consulta(
        db=db,
        usuario_id=administrador.id,
        ip=request.client.host if request.client else None,
    )
    return registros


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



