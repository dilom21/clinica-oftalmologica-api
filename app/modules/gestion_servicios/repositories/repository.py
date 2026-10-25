from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_historial_clinico.models.models import (
    ServicioOftalmologico,
)


def listar(db: Session):
    stmt = select(ServicioOftalmologico).order_by(
        ServicioOftalmologico.nombre.asc(),
        ServicioOftalmologico.id.asc(),
    )
    return db.scalars(stmt).all()


def obtener_por_id(
    db: Session,
    servicio_id: int,
    *,
    bloquear: bool = False,
):
    stmt = select(ServicioOftalmologico).where(
        ServicioOftalmologico.id == servicio_id,
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def crear(
    db: Session,
    *,
    nombre: str,
    descripcion: str | None,
    precio,
    duracion: int | None,
    estado: bool,
):
    servicio = ServicioOftalmologico(
        nombre=nombre,
        descripcion=descripcion,
        precio=precio,
        duracion=duracion,
        estado=estado,
    )
    db.add(servicio)
    db.flush()
    db.refresh(servicio)
    return servicio


def actualizar(db: Session, servicio, cambios: dict):
    for campo, valor in cambios.items():
        setattr(servicio, campo, valor)
    db.flush()
    db.refresh(servicio)
    return servicio
