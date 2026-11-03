"""Entradas y respuestas del CU29; importes calculados por el servidor."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_INT = 2**31 - 1
MAX_ID = 2**63 - 1
IdEntrada = Annotated[int, Field(strict=True, gt=0, le=MAX_ID)]
Dinero = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class _Entrada(BaseModel):
    # Impide enviar empresa_id, usuario_id, stock, estado o totales arbitrarios.
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def limpiar_texto(cls, valor):
        if isinstance(valor, str):
            valor = valor.strip()
            if not valor or "\x00" in valor:
                raise ValueError("El campo no puede estar vacío ni contener caracteres nulos")
        return valor


class DetalleCompraCrear(_Entrada):
    producto_id: IdEntrada
    lote_id: IdEntrada | None = None
    numero_lote: str | None = Field(default=None, min_length=1, max_length=80)
    fecha_fabricacion: date | None = None
    fecha_vencimiento: date | None = None
    cantidad: int = Field(strict=True, gt=0, le=MAX_INT)
    costo_unitario: Dinero
    observaciones: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validar_lote(self):
        if (self.lote_id is None) == (self.numero_lote is None):
            raise ValueError("Indique lote_id o numero_lote, exclusivamente uno")
        if (self.fecha_fabricacion is not None and self.fecha_vencimiento is not None
                and self.fecha_fabricacion > self.fecha_vencimiento):
            raise ValueError("La fabricación no puede ser posterior al vencimiento")
        return self


class CompraCrear(_Entrada):
    proveedor_id: int = Field(strict=True, gt=0, le=MAX_INT)
    numero_documento: str | None = Field(default=None, min_length=1, max_length=80)
    moneda: str = Field(default="BOB", pattern=r"^[A-Z]{3}$")
    observaciones: str | None = Field(default=None, max_length=2000)
    detalles: list[DetalleCompraCrear] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def rechazar_lotes_repetidos(self):
        claves = [(d.producto_id, d.lote_id, d.numero_lote) for d in self.detalles]
        if len(claves) != len(set(claves)):
            raise ValueError("Un lote solo puede aparecer una vez en la compra")
        return self


class ProductoRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    codigo: str
    nombre: str
    unidad_medida: str


class ProductosPagina(BaseModel):
    items: list[ProductoRespuesta]
    total: int
    pagina: int
    tamanio: int = 20


class LoteRespuesta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    producto_id: int
    numero_lote: str
    fecha_fabricacion: date | None
    fecha_vencimiento: date | None


class LotesPagina(BaseModel):
    items: list[LoteRespuesta]
    total: int
    pagina: int
    tamanio: int = 20


class DetalleCompraRespuesta(BaseModel):
    id: int
    producto_id: int
    producto_nombre: str
    lote_id: int
    numero_lote: str
    fecha_fabricacion: date | None
    fecha_vencimiento: date | None
    cantidad: int
    costo_unitario: Decimal
    subtotal: Decimal
    observaciones: str | None


class CompraResumen(BaseModel):
    id: int
    empresa_id: int
    proveedor_id: int
    proveedor_razon_social: str
    usuario_id: int
    numero_documento: str | None
    fecha_compra: datetime
    fecha_recepcion: datetime | None
    moneda: str
    estado: Literal["BORRADOR", "CONFIRMADA", "RECIBIDA", "ANULADA"]
    observaciones: str | None
    total: Decimal


class CompraRespuesta(CompraResumen):
    detalles: list[DetalleCompraRespuesta]


class ComprasPagina(BaseModel):
    items: list[CompraResumen]
    total: int
    pagina: int
    tamanio: int = 20


class CompraOperacion(BaseModel):
    mensaje: str
    compra: CompraRespuesta


