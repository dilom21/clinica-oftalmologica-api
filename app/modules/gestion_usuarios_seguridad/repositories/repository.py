from datetime import datetime, timezone

from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.models.models import (
    Usuario,
    Rol,
    Modulo,
    Funcion,
    Accion,
    RolFuncion,
    TokenRecuperacion,
    Bitacora,
)


# =========================================================
# USUARIO
# =========================================================

def obtener_usuario_por_id(db: Session, usuario_id: int):
    return db.get(Usuario, usuario_id)


def obtener_usuario_por_correo(db: Session, correo: str):
    stmt = select(Usuario).where(
        func.lower(Usuario.correo) == correo.lower()
    )
    return db.scalar(stmt)


def listar_usuarios(db: Session):
    stmt = select(Usuario).order_by(Usuario.id)
    return db.scalars(stmt).all()


def crear_usuario(
    db: Session,
    correo: str,
    password_hash: str,
    estado: bool = True,
):
    usuario = Usuario(
        correo=correo,
        password_hash=password_hash,
        estado=estado,
        fecha_creacion=datetime.now(timezone.utc),
    )

    db.add(usuario)
    db.flush()
    db.refresh(usuario)

    return usuario


def actualizar_password_usuario(
    db: Session,
    usuario_id: int,
    nuevo_password_hash: str,
):
    stmt = (
        update(Usuario)
        .where(Usuario.id == usuario_id)
        .values(password_hash=nuevo_password_hash)
    )
    db.execute(stmt)


# =========================================================
# ROLES
# =========================================================

def obtener_rol_por_id(db: Session, rol_id: int):
    return db.get(Rol, rol_id)


def listar_roles(db: Session):
    stmt = select(Rol).order_by(Rol.nombre)
    return db.scalars(stmt).all()


def crear_rol(
    db: Session,
    nombre: str,
    descripcion: str | None = None,
    estado: bool = True,
):
    rol = Rol(
        nombre=nombre,
        descripcion=descripcion,
        estado=estado,
        fecha_creacion=datetime.now(timezone.utc),
    )

    db.add(rol)
    db.flush()
    db.refresh(rol)

    return rol


# =========================================================
# MÓDULOS
# =========================================================

def listar_modulos(db: Session):
    stmt = select(Modulo).order_by(Modulo.nombre)
    return db.scalars(stmt).all()


def crear_modulo(
    db: Session,
    nombre: str,
    descripcion: str | None = None,
):
    modulo = Modulo(
        nombre=nombre,
        descripcion=descripcion,
        estado=True,
    )

    db.add(modulo)
    db.flush()
    db.refresh(modulo)

    return modulo


# =========================================================
# FUNCIONES
# =========================================================

def listar_funciones(db: Session):
    stmt = select(Funcion).order_by(Funcion.nombre)
    return db.scalars(stmt).all()


def crear_funcion(
    db: Session,
    modulo_id: int,
    nombre: str,
    descripcion: str | None = None,
):
    funcion = Funcion(
        modulo_id=modulo_id,
        nombre=nombre,
        descripcion=descripcion,
        estado=True,
    )

    db.add(funcion)
    db.flush()
    db.refresh(funcion)

    return funcion


# =========================================================
# ACCIONES
# =========================================================

def listar_acciones(db: Session):
    stmt = select(Accion).order_by(Accion.nombre)
    return db.scalars(stmt).all()


def crear_accion(
    db: Session,
    nombre: str,
    descripcion: str | None = None,
):
    accion = Accion(
        nombre=nombre,
        descripcion=descripcion,
        estado=True,
    )

    db.add(accion)
    db.flush()
    db.refresh(accion)

    return accion


# =========================================================
# PERMISOS
# =========================================================

def asignar_permiso(
    db: Session,
    rol_id: int,
    funcion_id: int,
    accion_id: int,
    descripcion: str | None = None,
):
    permiso = RolFuncion(
        rol_id=rol_id,
        funcion_id=funcion_id,
        accion_id=accion_id,
        descripcion=descripcion,
    )

    db.add(permiso)
    db.flush()
    db.refresh(permiso)

    return permiso


# =========================================================
# TOKEN RECUPERACIÓN
# =========================================================

def crear_token_recuperacion(
    db: Session,
    usuario_id: int,
    token_hash: str,
    fecha_expiracion: datetime,
):
    token = TokenRecuperacion(
        usuario_id=usuario_id,
        token_hash=token_hash,
        fecha_creacion=datetime.now(timezone.utc),
        fecha_expiracion=fecha_expiracion,
        usado=False,
    )

    db.add(token)
    db.flush()
    db.refresh(token)

    return token


def obtener_token_por_hash(db: Session, token_hash: str):
    stmt = select(TokenRecuperacion).where(
        TokenRecuperacion.token_hash == token_hash
    )

    return db.scalar(stmt)


def marcar_token_como_usado(
    db: Session,
    token_id: int,
):
    stmt = (
        update(TokenRecuperacion)
        .where(TokenRecuperacion.id == token_id)
        .values(usado=True)
    )
    db.execute(stmt)


def invalidar_tokens_activos_usuario(
    db: Session,
    usuario_id: int,
):
    stmt = (
        update(TokenRecuperacion)
        .where(
            TokenRecuperacion.usuario_id == usuario_id,
            TokenRecuperacion.usado.is_(False),
        )
        .values(usado=True)
    )
    db.execute(stmt)


# =========================================================
# BITÁCORA
# =========================================================

def registrar_bitacora(
    db: Session,
    usuario_id: int | None,
    accion: str,
    ip: str | None = None,
    entidad_afectada: str | None = None,
    id_registro_afectado: int | None = None,
    descripcion: str | None = None,
):
    registro = Bitacora(
        usuario_id=usuario_id,
        fecha_hora=datetime.now(timezone.utc),
        ip=ip,
        accion=accion,
        entidad_afectada=entidad_afectada,
        id_registro_afectado=id_registro_afectado,
        descripcion=descripcion,
    )

    db.add(registro)
    db.flush()

    return registro


def listar_bitacora(db: Session):
    stmt = select(Bitacora).order_by(
        Bitacora.fecha_hora.desc()
    )

    return db.scalars(stmt).all()
