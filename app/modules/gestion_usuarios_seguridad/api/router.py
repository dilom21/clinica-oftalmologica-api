<<<<<<< HEAD
from fastapi import APIRouter, Depends, HTTPException
=======
from fastapi import APIRouter, Depends, Query, Request
>>>>>>> 60cf664107ac716042a130922fa83e5d85fa683e
from sqlalchemy.orm import Session
from pydantic import BaseModel

# Importamos la conexión a la base de datos y el repositorio
from app.database.session import get_db
from app.core.dependencies import (
    obtener_administrador_actual,
    obtener_usuario_actual,
    requerir_permiso,
)

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

# El "molde" de los datos que envía Angular
class UsuarioRegistro(BaseModel):
    correo: str
    password_hash: str
    rol_id: int
    estado: bool = True

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de Usuarios y Seguridad funcionando"}

# --- RUTA POST ACTUALIZADA CON BASE DE DATOS ---
@router.post("/usuarios")
def registrar_usuario(usuario: UsuarioRegistro, db: Session = Depends(get_db)):
     # 1. Verificamos si el correo ya existe
    usuario_existente = repository.obtener_usuario_por_correo(db, usuario.correo)
    if usuario_existente:
        # Si existe, detenemos todo y lanzamos un error 400
        raise HTTPException(status_code=400, detail="Este correo ya está registrado.")
    
    # 2. Si no existe, procedemos a guardarlo normalmente
    nuevo_usuario = repository.crear_usuario(db, usuario)
    return {
         "mensaje": "¡Usuario guardado permanentemente en la base de datos!",
        "id_generado": nuevo_usuario.ID,
        "correo": nuevo_usuario.Correo
    }

@router.get("/usuarios")
def listar_usuarios(db: Session = Depends(get_db)):
    usuarios = repository.obtener_usuarios(db)
    return usuarios

@router.delete("/usuarios/{usuario_id}")
def eliminar_usuario(usuario_id: int, db: Session = Depends(get_db)):
    usuario = repository.dar_de_baja_usuario(db, usuario_id)
    
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    return {"mensaje": f"El usuario {usuario_id} fue dado de baja exitosamente."}

<<<<<<< HEAD
@router.put("/usuarios/{usuario_id}")
def actualizar_usuario_endpoint(usuario_id: int, usuario: UsuarioRegistro, db: Session = Depends(get_db)):
    
    # Mandamos al repositorio a actualizar los datos
    usuario_actualizado = repository.actualizar_usuario(db, usuario_id, usuario)
    
    if not usuario_actualizado:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    return {
        "mensaje": "¡Usuario actualizado exitosamente!",
        "id_generado": usuario_actualizado.ID
    }
=======
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
    usuario=Depends(obtener_usuario_actual),
):
    return menu_service.obtener_menu(db, rol_id=usuario.rol_id)



>>>>>>> 60cf664107ac716042a130922fa83e5d85fa683e
