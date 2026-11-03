"""Consulta de existencias y alertas sin modificar el inventario."""

from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.time import fecha_local_aplicacion
from app.modules.gestion_inventario_proveedores.repositories.inventario import (
    ProductoLectura, RepositorioInventario,
)
from app.modules.gestion_inventario_proveedores.schemas.inventario import (
    AlertaInventario, AlertasPagina, EstadoStock, EstadoVencimiento,
    FiltrosInventario, InventarioPagina, LoteInventario, LotesProductoRespuesta,
    ProductoInventario, ResumenInventario,
)


def estado_stock(actual: Decimal, minimo: Decimal | None) -> EstadoStock:
    if actual <= 0:
        return "AGOTADO"
    if minimo is None:
        return "SIN_UMBRAL"
    return "BAJO" if actual <= minimo else "NORMAL"


def estado_vencimiento(
    vencimiento: date | None, fecha_corte: date, dias: int,
) -> EstadoVencimiento:
    if vencimiento is None:
        return "SIN_FECHA"
    if vencimiento < fecha_corte:
        return "VENCIDO"
    if vencimiento <= fecha_corte + timedelta(days=dias):
        return "PROXIMO"
    return "VIGENTE"


def _lotes(
    producto: ProductoLectura, fecha_corte: date, dias: int,
) -> list[LoteInventario]:
    return [
        LoteInventario(
            id=lote.id, codigo=lote.codigo, existencia=lote.existencia,
            fecha_vencimiento=lote.fecha_vencimiento,
            estado_vencimiento=estado_vencimiento(lote.fecha_vencimiento, fecha_corte, dias),
        )
        for lote in sorted(producto.lotes, key=lambda lote: (lote.codigo.casefold(), lote.id))
    ]


def _coincide_busqueda(producto: ProductoLectura, texto: str | None) -> bool:
    if texto is None:
        return True
    busqueda = texto.casefold()
    campos = (producto.nombre, producto.codigo, *(lote.codigo for lote in producto.lotes))
    return any(busqueda in campo.casefold() for campo in campos if campo is not None)


def _productos_filtrados(
    db: Session, repositorio: RepositorioInventario,
    filtros: FiltrosInventario, fecha_corte: date,
) -> list[ProductoInventario]:
    productos = []
    for producto in repositorio.consultar(db):
        if not _coincide_busqueda(producto, filtros.q):
            continue
        if filtros.categoria is not None and (
            producto.categoria is None
            or producto.categoria.casefold() != filtros.categoria.casefold()
        ):
            continue
        estado = estado_stock(producto.stock_actual, producto.stock_minimo)
        if filtros.stock != "TODOS" and estado != filtros.stock:
            continue
        lotes = _lotes(producto, fecha_corte, filtros.dias_vencimiento)
        if filtros.vencimiento != "TODOS":
            lotes = [lote for lote in lotes if lote.estado_vencimiento == filtros.vencimiento]
            if not lotes:
                continue
        productos.append(ProductoInventario(
            id=producto.id, codigo=producto.codigo, nombre=producto.nombre,
            categoria=producto.categoria, stock_actual=producto.stock_actual,
            stock_minimo=producto.stock_minimo, estado_stock=estado, lotes=lotes,
        ))
    return sorted(productos, key=lambda producto: (producto.nombre.casefold(), producto.id))


def _paginar(items: list, filtros: FiltrosInventario) -> dict:
    total = len(items)
    inicio = (filtros.page - 1) * filtros.page_size
    return {
        "items": items[inicio:inicio + filtros.page_size], "total": total,
        "page": filtros.page, "page_size": filtros.page_size,
        "total_pages": (total + filtros.page_size - 1) // filtros.page_size,
    }


def listar_inventario(
    db: Session, filtros: FiltrosInventario, repositorio: RepositorioInventario,
) -> InventarioPagina:
    fecha_corte = fecha_local_aplicacion()
    productos = _productos_filtrados(db, repositorio, filtros, fecha_corte)
    lotes_con_stock = [lote for producto in productos for lote in producto.lotes if lote.existencia > 0]
    resumen = ResumenInventario(
        productos=len(productos),
        stock_bajo=sum(producto.estado_stock == "BAJO" for producto in productos),
        agotados=sum(producto.estado_stock == "AGOTADO" for producto in productos),
        lotes_proximos=sum(lote.estado_vencimiento == "PROXIMO" for lote in lotes_con_stock),
        lotes_vencidos=sum(lote.estado_vencimiento == "VENCIDO" for lote in lotes_con_stock),
    )
    return InventarioPagina(**_paginar(productos, filtros), resumen=resumen, fecha_corte=fecha_corte)


def consultar_lotes(
    db: Session, producto_id: int, dias_vencimiento: int, repositorio: RepositorioInventario,
) -> LotesProductoRespuesta:
    fecha_corte = fecha_local_aplicacion()
    producto = next(
        (producto for producto in repositorio.consultar(db) if producto.id == producto_id), None,
    )
    if producto is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Producto no encontrado en el inventario de esta empresa",
        )
    return LotesProductoRespuesta(
        producto_id=producto.id, lotes=_lotes(producto, fecha_corte, dias_vencimiento),
        fecha_corte=fecha_corte,
    )


def listar_alertas(
    db: Session, filtros: FiltrosInventario, repositorio: RepositorioInventario,
) -> AlertasPagina:
    fecha_corte = fecha_local_aplicacion()
    productos = _productos_filtrados(db, repositorio, filtros, fecha_corte)
    alertas = []
    for producto in productos:
        if producto.estado_stock in {"BAJO", "AGOTADO"}:
            alertas.append(AlertaInventario(
                tipo="STOCK_BAJO" if producto.estado_stock == "BAJO" else "AGOTADO",
                producto_id=producto.id, producto_nombre=producto.nombre,
                lote_id=None, lote_codigo=None, existencia=producto.stock_actual,
                fecha_vencimiento=None,
            ))
        for lote in producto.lotes:
            if lote.existencia <= 0 or lote.estado_vencimiento not in {"PROXIMO", "VENCIDO"}:
                continue
            alertas.append(AlertaInventario(
                tipo=("PROXIMO_VENCIMIENTO" if lote.estado_vencimiento == "PROXIMO" else "VENCIDO"),
                producto_id=producto.id, producto_nombre=producto.nombre,
                lote_id=lote.id, lote_codigo=lote.codigo, existencia=lote.existencia,
                fecha_vencimiento=lote.fecha_vencimiento,
            ))
    return AlertasPagina(**_paginar(alertas, filtros), fecha_corte=fecha_corte)
