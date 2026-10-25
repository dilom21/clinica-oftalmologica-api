from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_servicios.repositories import repository as repo
from app.modules.gestion_servicios.schemas.schemas import (
    ServicioActualizar,
    ServicioCrear,
)
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


_CAMPOS_MODELO = {
    "precio_base": "precio",
    "duracion_estimada": "duracion",
}


def _exigir_administrador(usuario):
    if nombre_rol_actual(usuario) not in {"admin", "administrador"}:
        raise HTTPException(403, "Se requiere un rol administrador")


def _obtener_o_404(db: Session, servicio_id: int, *, bloquear: bool = False):
    servicio = repo.obtener_por_id(db, servicio_id, bloquear=bloquear)
    if servicio is None:
        raise HTTPException(404, "Servicio oftalmologico no encontrado")
    return servicio


def listar(db: Session):
    return repo.listar(db)


def consultar(db: Session, servicio_id: int):
    return _obtener_o_404(db, servicio_id)


def crear(
    db: Session,
    datos: ServicioCrear,
    administrador,
    ip: str | None = None,
):
    _exigir_administrador(administrador)
    try:
        servicio = repo.crear(
            db,
            nombre=datos.nombre,
            descripcion=datos.descripcion,
            precio=datos.precio_base,
            duracion=datos.duracion_estimada,
            estado=datos.estado,
        )
        registrar_bitacora(
            db=db,
            usuario_id=administrador.id,
            ip=ip,
            accion="CREAR_SERVICIO_OFTALMOLOGICO",
            entidad_afectada="servicio_oftalmologico",
            id_registro_afectado=servicio.id,
            descripcion=f"Servicio oftalmologico '{servicio.nombre}' creado",
        )
        db.commit()
        db.refresh(servicio)
        return servicio
    except Exception:
        db.rollback()
        raise


def actualizar(
    db: Session,
    servicio_id: int,
    datos: ServicioActualizar,
    administrador,
    ip: str | None = None,
):
    _exigir_administrador(administrador)
    try:
        servicio = _obtener_o_404(db, servicio_id, bloquear=True)
        entrada = datos.model_dump(exclude_unset=True)
        cambios = {_CAMPOS_MODELO.get(campo, campo): valor for campo, valor in entrada.items()}
        servicio = repo.actualizar(db, servicio, cambios)
        registrar_bitacora(
            db=db,
            usuario_id=administrador.id,
            ip=ip,
            accion="ACTUALIZAR_SERVICIO_OFTALMOLOGICO",
            entidad_afectada="servicio_oftalmologico",
            id_registro_afectado=servicio.id,
            descripcion=f"Servicio oftalmologico '{servicio.nombre}' actualizado",
        )
        db.commit()
        db.refresh(servicio)
        return servicio
    except Exception:
        db.rollback()
        raise
