"""Contratos de lectura de inventario y alertas de CU30."""

from datetime import date
from decimal import Decimal
from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator


def _cantidad_json(valor: Decimal) -> float:
    numero = float(valor)
    if not isfinite(numero):
        raise ValueError("La cantidad debe poder representarse como un número finito")
    return numero


Cantidad = Annotated[
    Decimal,
    Field(allow_inf_nan=False),
    PlainSerializer(_cantidad_json, return_type=float, when_used="json"),
]
EstadoStock = Literal["NORMAL", "BAJO", "AGOTADO", "SIN_UMBRAL"]
EstadoVencimiento = Literal["VIGENTE", "PROXIMO", "VENCIDO", "SIN_FECHA"]
FiltroStock = Literal["TODOS", "NORMAL", "BAJO", "AGOTADO", "SIN_UMBRAL"]
FiltroVencimiento = Literal["TODOS", "PROXIMO", "VENCIDO", "SIN_FECHA"]


class FiltrosInventario(BaseModel):
    q: str | None = Field(default=None, max_length=120)
    categoria: str | None = Field(default=None, max_length=120)
    stock: FiltroStock = "TODOS"
    vencimiento: FiltroVencimiento = "TODOS"
    dias_vencimiento: int = Field(default=30, ge=1, le=365)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    model_config = ConfigDict(extra="forbid")

    @field_validator("q", "categoria", mode="before")
    @classmethod
    def normalizar_texto(cls, valor):
        return (valor.strip() or None) if isinstance(valor, str) else valor


class LoteInventario(BaseModel):
    id: int
    codigo: str
    existencia: Cantidad
    fecha_vencimiento: date | None
    estado_vencimiento: EstadoVencimiento


class ProductoInventario(BaseModel):
    id: int
    codigo: str | None
    nombre: str
    categoria: str | None
    stock_actual: Cantidad
    stock_minimo: Cantidad | None
    estado_stock: EstadoStock
    lotes: list[LoteInventario]


class ResumenInventario(BaseModel):
    productos: int
    stock_bajo: int
    agotados: int
    lotes_proximos: int
    lotes_vencidos: int


class InventarioPagina(BaseModel):
    items: list[ProductoInventario]
    total: int
    page: int
    page_size: int
    total_pages: int
    resumen: ResumenInventario
    fecha_corte: date


class LotesProductoRespuesta(BaseModel):
    producto_id: int
    lotes: list[LoteInventario]
    fecha_corte: date


class AlertaInventario(BaseModel):
    tipo: Literal["STOCK_BAJO", "AGOTADO", "PROXIMO_VENCIMIENTO", "VENCIDO"]
    producto_id: int
    producto_nombre: str
    lote_id: int | None
    lote_codigo: str | None
    existencia: Cantidad
    fecha_vencimiento: date | None


class AlertasPagina(BaseModel):
    items: list[AlertaInventario]
    total: int
    page: int
    page_size: int
    total_pages: int
    fecha_corte: date
