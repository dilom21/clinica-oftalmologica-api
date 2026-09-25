import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


# =========================================================
# VALIDACIONES REUTILIZABLES
# =========================================================

_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validar_politica_password(password: str) -> str:
    """Valida la política de contraseñas del CU-04.

    Requisitos:
    - mínimo 8 caracteres
    - al menos una letra mayúscula
    - al menos una letra minúscula
    - al menos un número
    """
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    if not re.search(r"[A-Z]", password):
        raise ValueError(
            "La contraseña debe incluir al menos una letra mayúscula"
        )
    if not re.search(r"[a-z]", password):
        raise ValueError(
            "La contraseña debe incluir al menos una letra minúscula"
        )
    if not re.search(r"[0-9]", password):
        raise ValueError("La contraseña debe incluir al menos un número")

    return password


def normalizar_correo(correo: str) -> str:
    correo_normalizado = correo.strip().lower()
    if not _EMAIL_REGEX.match(correo_normalizado):
        raise ValueError("El correo electrónico no es válido")

    return correo_normalizado


def validar_politica_password_paciente(password: str) -> str:
    """Valida la política de contraseñas del registro móvil de pacientes.

    Reutiliza la política base del sistema (mínimo 8 caracteres, mayúscula,
    minúscula y número) y además exige al menos un carácter especial.
    """
    validar_politica_password(password)

    if not re.search(r"[!@#$%^&*?_\-]", password):
        raise ValueError(
            "La contraseña no cumple con los requisitos de seguridad."
        )

    return password


# =========================================================
# USUARIO
# =========================================================

class UsuarioCrear(BaseModel):
    correo: str
    password: str
    rol_id: int

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, correo: str) -> str:
        return normalizar_correo(correo)

    @field_validator("password")
    @classmethod
    def validar_password(cls, password: str) -> str:
        return validar_politica_password(password)


class UsuarioActualizar(BaseModel):
    correo: str | None = None
    password: str | None = None
    rol_id: int | None = None

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, correo: str | None) -> str | None:
        if correo is None:
            return None
        return normalizar_correo(correo)

    @field_validator("password")
    @classmethod
    def validar_password(cls, password: str | None) -> str | None:
        if password is None:
            return None
        return validar_politica_password(password)


class UsuarioEstadoActualizar(BaseModel):
    estado: bool


class RolUsuarioRespuesta(BaseModel):
    id: int
    nombre: str

    model_config = ConfigDict(from_attributes=True)


class UsuarioRespuesta(BaseModel):
    id: int
    correo: str
    estado: bool
    fecha_creacion: datetime
    rol_id: int
    rol: RolUsuarioRespuesta

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
# REGISTRO MÓVIL DE PACIENTES
# El rol, estado y fecha_creacion los determina el backend.
# =========================================================

class MensajeRespuesta(BaseModel):
    message: str


class RegistroPacienteRequest(BaseModel):
    ci: str
    nombres: str
    apellidos: str
    fecha_nacimiento: date
    sexo: str
    telefono: str
    contacto_emergencia: str | None = None
    direccion: str | None = None
    correo: str
    password: str

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "ci",
        "nombres",
        "apellidos",
        "sexo",
        "telefono",
        "contacto_emergencia",
        "direccion",
    )
    @classmethod
    def _limpiar_texto(cls, valor: str | None, info) -> str | None:
        if valor is None:
            return None

        texto = valor.strip()

        if (
            info.field_name in ("ci", "nombres", "apellidos", "sexo", "telefono")
            and not texto
        ):
            raise ValueError("Este campo es obligatorio")

        return texto or None

    @field_validator("correo")
    @classmethod
    def _validar_correo(cls, correo: str) -> str:
        return normalizar_correo(correo)

    @field_validator("password")
    @classmethod
    def _validar_password(cls, password: str) -> str:
        return validar_politica_password_paciente(password)


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
    accion_id: int
    accion_nombre: str

    model_config = ConfigDict(from_attributes=True)


class MenuModuloRespuesta(BaseModel):
    id: int
    nombre: str
    funciones: list[MenuFuncionRespuesta] = []

    model_config = ConfigDict(from_attributes=True)
