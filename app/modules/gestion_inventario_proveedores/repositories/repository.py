"""Persistencia de proveedores; la transacción pertenece al servicio."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.models.models import Proveedor


def listar(db: Session, *, estado: bool | None, buscar: str | None, pagina: int):
    filtros = []
    if estado is not None:
        filtros.append(Proveedor.estado.is_(estado))
    if buscar and buscar.strip():
        termino = (
            buscar.strip().replace("/", "//").replace("%", "/%").replace("_", "/_")
        )
        patron = f"%{termino}%"
        filtros.append(or_(
            Proveedor.razon_social.ilike(patron, escape="/"),
            Proveedor.nit.ilike(patron, escape="/"),
        ))
    total = db.scalar(select(func.count()).select_from(Proveedor).where(*filtros))
    items = db.scalars(
        select(Proveedor).where(*filtros)
        .order_by(Proveedor.razon_social, Proveedor.id)
        .offset((pagina - 1) * 20).limit(20)
    ).all()
    return items, total


def obtener_por_id(db: Session, proveedor_id: int, *, bloquear: bool = False):
    consulta = select(Proveedor).where(Proveedor.id == proveedor_id)
    if bloquear:
        consulta = consulta.with_for_update()
    return db.scalar(consulta)


def existe_nit(db: Session, nit: str, proveedor_id: int | None = None) -> bool:
    consulta = select(Proveedor.id).where(Proveedor.nit == nit)
    if proveedor_id is not None:
        consulta = consulta.where(Proveedor.id != proveedor_id)
    return db.scalar(consulta.limit(1)) is not None


def crear(db: Session, datos: dict):
    proveedor = Proveedor(**datos, estado=True)
    db.add(proveedor)
    db.flush()
    return proveedor


def actualizar(db: Session, proveedor: Proveedor, cambios: dict):
    for campo, valor in cambios.items():
        setattr(proveedor, campo, valor)
    db.flush()
    return proveedor