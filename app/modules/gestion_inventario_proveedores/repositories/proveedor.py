from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.models.proveedor import Proveedor


def _escapar_like(texto: str) -> str:
    return (
        texto.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def listar(
    db: Session,
    *,
    estado: bool | None,
    busqueda: str | None,
    skip: int,
    limit: int,
):
    stmt = select(Proveedor)
    if estado is True:
        stmt = stmt.where(Proveedor.estado.is_(True))
    elif estado is False:
        # Filas historicas con estado NULL se tratan como inactivas.
        stmt = stmt.where(or_(Proveedor.estado.is_(False), Proveedor.estado.is_(None)))
    if busqueda:
        patron = f"%{_escapar_like(busqueda)}%"
        stmt = stmt.where(or_(
            Proveedor.razon_social.ilike(patron, escape="\\"),
            Proveedor.nit.ilike(patron, escape="\\"),
        ))
    stmt = (
        stmt.order_by(Proveedor.razon_social.asc(), Proveedor.id.asc())
        .offset(skip)
        .limit(limit)
    )
    return db.scalars(stmt).all()


def obtener_por_id(
    db: Session,
    proveedor_id: int,
    *,
    bloquear: bool = False,
):
    stmt = select(Proveedor).where(Proveedor.id == proveedor_id)
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def existe_nit(db: Session, nit: str, *, excluir_id: int | None = None) -> bool:
    stmt = select(Proveedor.id).where(Proveedor.nit == nit)
    if excluir_id is not None:
        stmt = stmt.where(Proveedor.id != excluir_id)
    return db.scalar(stmt.limit(1)) is not None


def crear(db: Session, datos: dict):
    proveedor = Proveedor(**datos)
    db.add(proveedor)
    db.flush()
    db.refresh(proveedor)
    return proveedor


def actualizar(db: Session, proveedor, cambios: dict):
    for campo, valor in cambios.items():
        setattr(proveedor, campo, valor)
    db.flush()
    db.refresh(proveedor)
    return proveedor
