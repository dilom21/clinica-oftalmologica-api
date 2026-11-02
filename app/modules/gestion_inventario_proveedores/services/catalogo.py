"""CU26 - Reglas de negocio sin gestión directa de inventario.

Usar siempre la sesión que FastAPI resuelve para el token del tenant.
Ningún método obtiene conexiones desde el plano de control o URLs propias.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.models.catalogo import Categoria, Lote, Producto
from app.modules.gestion_inventario_proveedores.schemas.catalogo import (
    CategoriaActualizar, CategoriaCrear, EstadoActualizar,
    LoteActualizar, LoteCrear, ProductoActualizar, ProductoCrear,
)


def _guardar(db: Session, objeto, conflicto: str):
    try:
        db.add(objeto)
        db.commit()
        db.refresh(objeto)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflicto) from exc
    except Exception:
        db.rollback()
        raise
    return objeto


def _obtener(db: Session, modelo, id_: int, entidad: str):
    objeto = db.get(modelo, id_)
    if objeto is None:
        raise HTTPException(status_code=404, detail=f"{entidad} no encontrado")
    return objeto


def _categoria_activa(db: Session, id_: int) -> Categoria:
    categoria = _obtener(db, Categoria, id_, "Categoría")
    if not categoria.estado:
        raise HTTPException(status_code=409, detail="La categoría está inactiva")
    return categoria


def _producto_activo(db: Session, id_: int) -> Producto:
    producto = _obtener(db, Producto, id_, "Producto")
    if not producto.estado:
        raise HTTPException(status_code=409, detail="El producto está inactivo")
    return producto


# Categorías
def listar_categorias(db: Session, incluir_inactivos: bool = False) -> list[Categoria]:
    stmt = select(Categoria)
    if not incluir_inactivos:
        stmt = stmt.where(Categoria.estado.is_(True))
    return list(db.scalars(stmt.order_by(Categoria.id)).all())


def obtener_categoria(db: Session, categoria_id: int) -> Categoria:
    return _obtener(db, Categoria, categoria_id, "Categoría")


def crear_categoria(db: Session, data: CategoriaCrear) -> Categoria:
    return _guardar(db, Categoria(**data.model_dump()), "Ya existe una categoría con ese nombre")


def actualizar_categoria(db: Session, categoria_id: int, data: CategoriaActualizar) -> Categoria:
    categoria = obtener_categoria(db, categoria_id)
    for campo, valor in data.model_dump().items():
        setattr(categoria, campo, valor)
    return _guardar(db, categoria, "Ya existe una categoría con ese nombre")


def cambiar_estado_categoria(db: Session, categoria_id: int, data: EstadoActualizar) -> Categoria:
    categoria = obtener_categoria(db, categoria_id)
    if not data.estado:
        activos = db.scalar(select(func.count()).select_from(Producto).where(
            Producto.categoria_id == categoria_id, Producto.estado.is_(True)))
        if activos:
            raise HTTPException(status_code=409, detail="La categoría tiene productos activos")
    categoria.estado = data.estado
    return _guardar(db, categoria, "No se pudo actualizar la categoría")


# Productos
def listar_productos(db: Session, categoria_id: int | None = None, incluir_inactivos: bool = False) -> list[Producto]:
    stmt = select(Producto)
    if categoria_id is not None:
        stmt = stmt.where(Producto.categoria_id == categoria_id)
    if not incluir_inactivos:
        stmt = stmt.where(Producto.estado.is_(True))
    return list(db.scalars(stmt.order_by(Producto.id)).all())


def obtener_producto(db: Session, producto_id: int) -> Producto:
    return _obtener(db, Producto, producto_id, "Producto")


def crear_producto(db: Session, data: ProductoCrear) -> Producto:
    _categoria_activa(db, data.categoria_id)
    return _guardar(db, Producto(**data.model_dump()), "Código de producto duplicado o datos inválidos")


def actualizar_producto(db: Session, producto_id: int, data: ProductoActualizar) -> Producto:
    producto = obtener_producto(db, producto_id)
    _categoria_activa(db, data.categoria_id)
    if producto.unidad_medida != data.unidad_medida:
        # Evita reinterpretar cantidades previamente asignadas a lotes.
        tiene_lotes = db.scalar(select(func.count()).select_from(Lote).where(Lote.producto_id == producto_id))
        if tiene_lotes:
            raise HTTPException(status_code=409, detail="No se puede cambiar la unidad de medida de un producto con lotes")
    for campo, valor in data.model_dump().items():
        setattr(producto, campo, valor)
    producto.fecha_actualizacion = datetime.now(timezone.utc)
    return _guardar(db, producto, "Código de producto duplicado o datos inválidos")


def cambiar_estado_producto(db: Session, producto_id: int, data: EstadoActualizar) -> Producto:
    producto = obtener_producto(db, producto_id)
    if data.estado:
        _categoria_activa(db, producto.categoria_id)
    else:
        activos = db.scalar(select(func.count()).select_from(Lote).where(
            Lote.producto_id == producto_id, Lote.estado.is_(True)))
        if activos:
            raise HTTPException(status_code=409, detail="El producto tiene lotes activos")
    producto.estado = data.estado
    producto.fecha_actualizacion = datetime.now(timezone.utc)
    return _guardar(db, producto, "No se pudo actualizar el producto")


# Lotes
def listar_lotes(db: Session, producto_id: int | None = None, incluir_inactivos: bool = False) -> list[Lote]:
    stmt = select(Lote)
    if producto_id is not None:
        stmt = stmt.where(Lote.producto_id == producto_id)
    if not incluir_inactivos:
        stmt = stmt.where(Lote.estado.is_(True))
    return list(db.scalars(stmt.order_by(Lote.id)).all())


def obtener_lote(db: Session, lote_id: int) -> Lote:
    return _obtener(db, Lote, lote_id, "Lote")


def crear_lote(db: Session, data: LoteCrear) -> Lote:
    _producto_activo(db, data.producto_id)
    return _guardar(db, Lote(**data.model_dump()), "Ya existe este lote para el producto o tiene fechas inválidas")


def actualizar_lote(db: Session, lote_id: int, data: LoteActualizar) -> Lote:
    lote = obtener_lote(db, lote_id)
    for campo, valor in data.model_dump().items():
        setattr(lote, campo, valor)
    return _guardar(db, lote, "Número de lote duplicado o fechas inválidas")


def cambiar_estado_lote(db: Session, lote_id: int, data: EstadoActualizar) -> Lote:
    lote = obtener_lote(db, lote_id)
    if data.estado:
        _producto_activo(db, lote.producto_id)
    else:
        # Existencias > 0 impiden desactivar un lote con saldo disponible/reservado.
        # Consulta con SQL text: este modelo pertenece al CU27 y no se duplica aquí.
        from sqlalchemy import text
        stock = db.execute(text("""
            SELECT COALESCE(stock_actual,0) AS stock_actual,
                   COALESCE(stock_reservado,0) AS stock_reservado
              FROM inventario WHERE lote_id = :lote_id
        """), {"lote_id": lote_id}).mappings().first()
        if stock and (stock["stock_actual"] > 0 or stock["stock_reservado"] > 0):
            raise HTTPException(status_code=409, detail="El lote tiene existencias y no puede desactivarse")
    lote.estado = data.estado
    return _guardar(db, lote, "No se pudo actualizar el lote")
