from datetime import datetime

from pydantic import BaseModel, ConfigDict


# =========================================================
# USUARIO
# =========================================================

class UsuarioCrear(BaseModel):
    correo: str
    password: str
    rol_id: int
    estado: bool = True


class UsuarioActualizar(BaseModel):
    correo: str | None = None
    password: str | None = None
    estado: bool | None = None


class UsuarioRespuesta(BaseModel):
    id: int
    correo: str
    estado: bool
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# LOGIN
# CU01
# =========================================================

class LoginRequest(BaseModel):
    correo: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# =========================================================
# RECUPERACIÓN DE CONTRASEÑA
# CU03
# =========================================================

class RecuperarPasswordRequest(BaseModel):
    correo: str


class RestablecerPasswordRequest(BaseModel):
    token: str
    nueva_password: str


# =========================================================
# ROL
# =========================================================

class RolCrear(BaseModel):
    nombre: str
    descripcion: str | None = None
    estado: bool = True


class RolActualizar(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    estado: bool | None = None


class RolRespuesta(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    estado: bool
    protegido: bool
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# MÓDULO
# =========================================================

class ModuloCrear(BaseModel):
    nombre: str
    descripcion: str | None = None
    estado: bool = True


class ModuloRespuesta(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# FUNCIÓN
# =========================================================

class FuncionCrear(BaseModel):
    modulo_id: int
    nombre: str
    descripcion: str | None = None
    estado: bool = True


class FuncionRespuesta(BaseModel):
    id: int
    modulo_id: int
    nombre: str
    descripcion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# ACCIÓN
# =========================================================

class AccionCrear(BaseModel):
    nombre: str
    descripcion: str | None = None
    estado: bool = True


class AccionRespuesta(BaseModel):
    id: int
    nombre: str
    descripcion: str | None
    estado: bool

    model_config = ConfigDict(from_attributes=True)


# =========================================================
# ASIGNACIÓN DE ROL A USUARIO
# La relación real es usuario.rol_id -> rol.id (no existe usuario_rol).
# =========================================================

class AsignarRolRequest(BaseModel):
    rol_id: int


# =========================================================
# PERMISO ROL - FUNCIÓN - ACCIÓN
# =========================================================

class RolFuncionCrear(BaseModel):
    rol_id: int
    funcion_id: int
    accion_id: int
    descripcion: str | None = None


# =========================================================
# CU05 - ROL CON PERMISOS
# =========================================================

class PermisoRolCrear(BaseModel):
    funcion_id: int
    accion_id: int


class RolCrearConPermisos(BaseModel):
    nombre: str
    descripcion: str | None = None
    estado: bool = True
    permisos: list[PermisoRolCrear] = []


class RolActualizarConPermisos(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    estado: bool | None = None
    permisos: list[PermisoRolCrear] | None = None


class PermisoRolRespuesta(BaseModel):
    rol_id: int
    funcion_id: int
    funcion_nombre: str
    modulo_id: int
    modulo_nombre: str
    accion_id: int
    accion_nombre: str


class ModuloConFuncionesRespuesta(BaseModel):
    id: int
    nombre: str
    funciones: list[FuncionRespuesta] = []


# =========================================================
# BITÁCORA
# CU06
# =========================================================

class BitacoraRespuesta(BaseModel):
    id: int
    usuario_id: int | None
    fecha_hora: datetime
    ip: str | None
    accion: str
    entidad_afectada: str | None
    id_registro_afectado: int | None
    descripcion: str | None

    model_config = ConfigDict(from_attributes=True)


class BitacoraPaginadaRespuesta(BaseModel):
    items: list[BitacoraRespuesta]
    total: int
    page: int
    page_size: int
    total_pages: int


class BitacoraFiltros(BaseModel):
    usuario_id: int | None = None
    accion: str | None = None
    entidad_afectada: str | None = None
    id_registro_afectado: int | None = None
    desde: datetime | None = None
    hasta: datetime | None = None

# =========================================================
# MENÚ DINÁMICO
# =========================================================

class MenuFuncionRespuesta(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class MenuModuloRespuesta(BaseModel):
    id: int
    nombre: str
    funciones: list[MenuFuncionRespuesta] = []

    model_config = ConfigDict(from_attributes=True)
