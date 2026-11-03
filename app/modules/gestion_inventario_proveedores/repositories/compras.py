"""Consultas en la sesión tenant; el servicio controla commit y rollback."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.models.compras import (
    Compra, DetalleCompra, Inventario, Lote, Producto,
)
from app.modules.gestion_inventario_proveedores.models.models import Proveedor


def _bloquear(db: Session, modelo, *filtros):
    return db.scalar(
        select(modelo).where(*filtros).with_for_update()
        .execution_options(populate_existing=True)
    )


def bloquear_proveedor(db: Session, proveedor_id: int):
    return _bloquear(db, Proveedor, Proveedor.id == proveedor_id)


def bloquear_producto(db: Session, producto_id: int):
    return _bloquear(db, Producto, Producto.id == producto_id)


def obtener_producto_activo(db: Session, producto_id: int):
    return db.scalar(select(Producto).where(
        Producto.id == producto_id, Producto.estado.is_(True),
    ))


def existe_documento(db: Session, proveedor_id: int, numero_documento: str):
    return db.scalar(select(Compra.id).where(
        Compra.proveedor_id == proveedor_id,
        Compra.numero_documento == numero_documento,
    ).limit(1)) is not None


def bloquear_lote(db: Session, producto_id: int, *, lote_id=None, numero_lote=None):
    filtro = Lote.id == lote_id if lote_id is not None else Lote.numero_lote == numero_lote
    return _bloquear(db, Lote, Lote.producto_id == producto_id, filtro)


def bloquear_inventario(db: Session, lote_id: int):
    return _bloquear(db, Inventario, Inventario.lote_id == lote_id)


def guardar(db: Session, registro):
    db.add(registro)
    db.flush()
    return registro


def obtener_compra(db: Session, compra_id: int):
    return db.execute(
        select(Compra, Proveedor.razon_social)
        .join(Proveedor, Proveedor.id == Compra.proveedor_id)
        .where(Compra.id == compra_id)
    ).first()


def obtener_detalles(db: Session, compra_id: int):
    return db.execute(
        select(DetalleCompra, Lote, Producto.nombre)
        .join(Lote, Lote.id == DetalleCompra.lote_id)
        .join(Producto, Producto.id == Lote.producto_id)
        .where(DetalleCompra.compra_id == compra_id).order_by(DetalleCompra.id)
    ).all()


def listar(db: Session, *, pagina: int, proveedor_id: int | None):
    filtros = [] if proveedor_id is None else [Compra.proveedor_id == proveedor_id]
    total = db.scalar(select(func.count()).select_from(Compra).where(*filtros)) or 0
    importes = select(
        DetalleCompra.compra_id,
        func.sum(DetalleCompra.cantidad * DetalleCompra.costo_unitario).label("importe"),
    ).group_by(DetalleCompra.compra_id).subquery()
    filas = db.execute(
        select(Compra, Proveedor.razon_social, func.coalesce(importes.c.importe, 0))
        .join(Proveedor, Proveedor.id == Compra.proveedor_id)
        .outerjoin(importes, importes.c.compra_id == Compra.id).where(*filtros)
        .order_by(Compra.fecha_compra.desc(), Compra.id.desc())
        .offset((pagina - 1) * 20).limit(20)
    ).all()
    return filas, total


def listar_productos(db: Session, *, buscar: str | None, pagina: int):
    filtros = [Producto.estado.is_(True)]
    if buscar and buscar.strip():
        termino = buscar.strip().replace("/", "//").replace("%", "/%").replace("_", "/_")
        patron = f"%{termino}%"
        filtros.append(
            Producto.nombre.ilike(patron, escape="/")
            | Producto.codigo.ilike(patron, escape="/")
        )
    total = db.scalar(select(func.count()).select_from(Producto).where(*filtros)) or 0
    items = db.scalars(
        select(Producto).where(*filtros).order_by(Producto.nombre, Producto.id)
        .offset((pagina - 1) * 20).limit(20)
    ).all()
    return items, total


def listar_lotes(db: Session, producto_id: int, *, hoy: date, pagina: int):
    filtros = [
        Lote.producto_id == producto_id, Lote.estado.is_(True),
        Lote.fecha_vencimiento.is_(None) | (Lote.fecha_vencimiento >= hoy),
        Lote.fecha_fabricacion.is_(None) | (Lote.fecha_fabricacion <= hoy),
    ]
    total = db.scalar(select(func.count()).select_from(Lote).where(*filtros)) or 0
    items = db.scalars(
        select(Lote).where(*filtros).order_by(Lote.numero_lote, Lote.id)
        .offset((pagina - 1) * 20).limit(20)
    ).all()
    return items, total
