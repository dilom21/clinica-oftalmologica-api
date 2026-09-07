from datetime import datetime, timezone

from sqlalchemy import select, func, update, delete
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
    rol_id: int,
    estado: bool = True,
):
    usuario = Usuario(
        correo=correo,  # Todo en minúsculas, como en tu models.py
        password_hash=password_hash,
        rol_id=rol_id,
        estado=True,
        fecha_creacion=datetime.now(timezone.utc)
    )

    db.add(usuario)
    db.flush()
    db.refresh(usuario)

    return usuario


def asignar_rol_usuario(
    db: Session,
    usuario_id: int,
    rol_id: int,
):
    stmt = (
        update(Usuario)
        .where(Usuario.id == usuario_id)
        .values(rol_id=rol_id)
    )
    db.execute(stmt)


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


def actualizar_usuario(
    db: Session,
    usuario: Usuario,
    correo: str | None = None,
    rol_id: int | None = None,
    password_hash: str | None = None,
):
    if correo is not None:
        usuario.correo = correo
    if rol_id is not None:
        usuario.rol_id = rol_id
    if password_hash is not None:
        usuario.password_hash = password_hash

    db.flush()
    db.refresh(usuario)

    return usuario


def actualizar_estado_usuario(
    db: Session,
    usuario: Usuario,
    estado: bool,
):
    usuario.estado = estado

    db.flush()
    db.refresh(usuario)

    return usuario


# =========================================================
# ROLES
# =========================================================

def obtener_rol_por_id(db: Session, rol_id: int):
    return db.get(Rol, rol_id)


def obtener_rol_por_nombre(db: Session, nombre: str):
    stmt = select(Rol).where(
        func.lower(Rol.nombre) == nombre.lower()
    )
    return db.scalar(stmt)


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


def actualizar_rol(
    db: Session,
    rol: Rol,
    nombre: str | None = None,
    descripcion: str | None = None,
    estado: bool | None = None,
):
    if nombre is not None:
        rol.nombre = nombre
    if descripcion is not None:
        rol.descripcion = descripcion
    if estado is not None:
        rol.estado = estado

    db.flush()
    db.refresh(rol)

    return rol


def desactivar_rol(
    db: Session,
    rol: Rol,
):
    rol.estado = False

    db.flush()
    db.refresh(rol)

    return rol


def contar_usuarios_por_rol(
    db: Session,
    rol_id: int,
):
    stmt = select(func.count(Usuario.id)).where(
        Usuario.rol_id == rol_id
    )
    return db.scalar(stmt)


# =========================================================
# MÓDULOS
# =========================================================

def listar_modulos(db: Session):
    stmt = select(Modulo).order_by(Modulo.nombre)
    return db.scalars(stmt).all()


def listar_modulos_activos(db: Session):
    stmt = (
        select(Modulo)
        .where(Modulo.estado.is_(True))
        .order_by(Modulo.id)
    )
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


def listar_funciones_activas(db: Session):
    stmt = (
        select(Funcion)
        .where(Funcion.estado.is_(True))
        .order_by(Funcion.id)
    )
    return db.scalars(stmt).all()


def obtener_funcion_por_id(db: Session, funcion_id: int):
    return db.get(Funcion, funcion_id)


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


def obtener_accion_por_id(db: Session, accion_id: int):
    return db.get(Accion, accion_id)


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


def eliminar_permisos_rol(
    db: Session,
    rol_id: int,
):
    stmt = (
        delete(RolFuncion)
        .where(RolFuncion.rol_id == rol_id)
    )
    db.execute(stmt)


def obtener_permisos_rol(
    db: Session,
    rol_id: int,
):
    stmt = (
        select(
            RolFuncion.rol_id.label("rol_id"),
            Funcion.id.label("funcion_id"),
            Funcion.nombre.label("funcion_nombre"),
            Modulo.id.label("modulo_id"),
            Modulo.nombre.label("modulo_nombre"),
            Accion.id.label("accion_id"),
            Accion.nombre.label("accion_nombre"),
        )
        .join(Funcion, Funcion.id == RolFuncion.funcion_id)
        .join(Modulo, Modulo.id == Funcion.modulo_id)
        .join(Accion, Accion.id == RolFuncion.accion_id)
        .where(RolFuncion.rol_id == rol_id)
        .order_by(Modulo.id, Funcion.id)
    )

    return db.execute(stmt).mappings().all()


def listar_menu_por_rol(
    db: Session,
    rol_id: int,
):
    """Módulos y funciones permitidos a un rol según `rol_funcion`.

    Solo incluye módulos, funciones y acciones activas. Devuelve filas
    ordenadas para construir el menú dinámico del usuario autenticado.
    """
    stmt = (
        select(
            Modulo.id.label("modulo_id"),
            Modulo.nombre.label("modulo_nombre"),
            Funcion.id.label("funcion_id"),
            Funcion.nombre.label("funcion_nombre"),
            Funcion.descripcion.label("funcion_descripcion"),
            Accion.id.label("accion_id"),
            Accion.nombre.label("accion_nombre"),
        )
        .select_from(RolFuncion)
        .join(Funcion, Funcion.id == RolFuncion.funcion_id)
        .join(Modulo, Modulo.id == Funcion.modulo_id)
        .join(Accion, Accion.id == RolFuncion.accion_id)
        .where(
            RolFuncion.rol_id == rol_id,
            Modulo.estado.is_(True),
            Funcion.estado.is_(True),
            Accion.estado.is_(True),
        )
        .order_by(Modulo.id, Funcion.id)
    )

    return db.execute(stmt).mappings().all()


def listar_modulos_con_funciones(db: Session):
    stmt = (
        select(
            Modulo.id.label("modulo_id"),
            Modulo.nombre.label("modulo_nombre"),
            Funcion.id.label("funcion_id"),
            Funcion.nombre.label("funcion_nombre"),
            Funcion.descripcion.label("funcion_descripcion"),
        )
        .outerjoin(
            Funcion,
            (Funcion.modulo_id == Modulo.id)
            & (Funcion.estado.is_(True)),
        )
        .where(Modulo.estado.is_(True))
        .order_by(Modulo.id, Funcion.id)
    )

    return db.execute(stmt).mappings().all()


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


def listar_bitacora(
    db: Session,
    usuario_id: int | None = None,
    accion: str | None = None,
    entidad_afectada: str | None = None,
    id_registro_afectado: int | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    offset: int | None = None,
    limit: int | None = None,
):
    filtros = []
    if usuario_id is not None:
        filtros.append(Bitacora.usuario_id == usuario_id)
    if accion is not None:
        filtros.append(func.lower(Bitacora.accion) == accion.lower())
    if entidad_afectada is not None:
        filtros.append(
            func.lower(Bitacora.entidad_afectada) == entidad_afectada.lower()
        )
    if id_registro_afectado is not None:
        filtros.append(Bitacora.id_registro_afectado == id_registro_afectado)
    if desde is not None:
        filtros.append(Bitacora.fecha_hora >= desde)
    if hasta is not None:
        filtros.append(Bitacora.fecha_hora <= hasta)

    stmt = select(Bitacora).where(*filtros).order_by(
        Bitacora.fecha_hora.desc(),
        Bitacora.id.desc(),
    )
    if offset is not None:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    return db.scalars(stmt).all()


def contar_bitacora(
    db: Session,
    usuario_id: int | None = None,
    accion: str | None = None,
    entidad_afectada: str | None = None,
    id_registro_afectado: int | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
):
    filtros = []
    if usuario_id is not None:
        filtros.append(Bitacora.usuario_id == usuario_id)
    if accion is not None:
        filtros.append(func.lower(Bitacora.accion) == accion.lower())
    if entidad_afectada is not None:
        filtros.append(
            func.lower(Bitacora.entidad_afectada) == entidad_afectada.lower()
        )
    if id_registro_afectado is not None:
        filtros.append(Bitacora.id_registro_afectado == id_registro_afectado)
    if desde is not None:
        filtros.append(Bitacora.fecha_hora >= desde)
    if hasta is not None:
        filtros.append(Bitacora.fecha_hora <= hasta)

    return db.scalar(select(func.count()).select_from(Bitacora).where(*filtros)) or 0
