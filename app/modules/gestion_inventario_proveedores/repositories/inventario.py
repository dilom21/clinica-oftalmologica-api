"""Lectura de las tablas de inventario existentes en la sesión de la empresa."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from fastapi import HTTPException, status
from sqlalchemy import (
    BigInteger, Boolean, Date, Integer, String, and_, column, func, select, table,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class LoteLectura:
    id: int
    codigo: str
    existencia: Decimal
    fecha_vencimiento: date | None


@dataclass(frozen=True)
class ProductoLectura:
    id: int
    codigo: str | None
    nombre: str
    categoria: str | None
    stock_actual: Decimal
    stock_minimo: Decimal | None
    lotes: tuple[LoteLectura, ...]


class RepositorioInventario(Protocol):
    def consultar(self, db: Session) -> Sequence[ProductoLectura]:
        """Lee únicamente el inventario de la sesión tenant recibida."""
        ...


# Proyecciones Core de tablas existentes; no crean ni alteran el esquema.
_categoria = table(
    "categoria", column("id", BigInteger), column("nombre", String),
    schema="public",
)
_producto = table(
    "producto", column("id", BigInteger), column("categoria_id", BigInteger),
    column("codigo", String), column("nombre", String),
    column("stock_minimo", Integer), column("estado", Boolean), schema="public",
)
_lote = table(
    "lote", column("id", BigInteger), column("producto_id", BigInteger),
    column("numero_lote", String), column("fecha_vencimiento", Date),
    column("estado", Boolean), schema="public",
)
_inventario = table(
    "inventario", column("lote_id", BigInteger), column("stock_actual", Integer),
    schema="public",
)


def _fuente_no_disponible(error: SQLAlchemyError) -> HTTPException:
    original = getattr(error, "orig", None)
    codigo_sql = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    if codigo_sql in {"42P01", "42703"}:
        detalle = {
            "codigo": "INVENTARIO_DEPENDENCIAS_PENDIENTES",
            "mensaje": "El inventario todavía no está disponible para esta empresa.",
            "dependencias": ["CU26", "CU27", "CU28", "CU29"],
        }
    else:
        detalle = {
            "codigo": "INVENTARIO_NO_DISPONIBLE",
            "mensaje": "No se pudo consultar el inventario de esta empresa. Intenta nuevamente.",
        }
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detalle)


class RepositorioInventarioSQL:
    def consultar(self, db: Session) -> tuple[ProductoLectura, ...]:
        existencias = (
            select(
                _inventario.c.lote_id,
                func.sum(_inventario.c.stock_actual).label("existencia"),
            )
            .group_by(_inventario.c.lote_id)
            .subquery()
        )
        consulta = (
            select(
                _producto.c.id.label("producto_id"),
                _producto.c.codigo.label("producto_codigo"),
                _producto.c.nombre.label("producto_nombre"),
                _categoria.c.nombre.label("categoria_nombre"),
                _producto.c.stock_minimo,
                _lote.c.id.label("lote_id"),
                _lote.c.numero_lote,
                _lote.c.fecha_vencimiento,
                func.coalesce(existencias.c.existencia, 0).label("existencia"),
            )
            .select_from(
                _producto
                .outerjoin(_categoria, _categoria.c.id == _producto.c.categoria_id)
                .outerjoin(
                    _lote,
                    and_(_lote.c.producto_id == _producto.c.id, _lote.c.estado.is_(True)),
                )
                .outerjoin(existencias, existencias.c.lote_id == _lote.c.id)
            )
            .where(_producto.c.estado.is_(True))
            .order_by(
                func.lower(_producto.c.nombre), _producto.c.id,
                func.lower(_lote.c.numero_lote), _lote.c.id,
            )
        )
        productos: dict[int, dict] = {}
        try:
            with db.execute(consulta.execution_options(yield_per=500)) as resultado:
                for fila in resultado.mappings():
                    producto = productos.setdefault(fila["producto_id"], {
                        "id": fila["producto_id"],
                        "codigo": fila["producto_codigo"],
                        "nombre": fila["producto_nombre"],
                        "categoria": fila["categoria_nombre"],
                        "stock_actual": Decimal(0),
                        "stock_minimo": (
                            Decimal(fila["stock_minimo"])
                            if fila["stock_minimo"] is not None else None
                        ),
                        "lotes": [],
                    })
                    if fila["lote_id"] is not None:
                        existencia = Decimal(fila["existencia"])
                        producto["stock_actual"] += existencia
                        producto["lotes"].append(LoteLectura(
                            id=fila["lote_id"], codigo=fila["numero_lote"],
                            existencia=existencia,
                            fecha_vencimiento=fila["fecha_vencimiento"],
                        ))
        except SQLAlchemyError as error:
            raise _fuente_no_disponible(error) from error
        return tuple(
            ProductoLectura(**{**producto, "lotes": tuple(producto["lotes"])})
            for producto in productos.values()
        )


def obtener_repositorio_inventario() -> RepositorioInventario:
    """Una fuente por petición; no se almacena información de empresas en caché."""
    return RepositorioInventarioSQL()
