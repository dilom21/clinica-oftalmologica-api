"""Mapeo de las tablas existentes de compras e inventario; no ejecuta DDL."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import fecha_hora_utc
from app.database.base import Base


class Producto(Base):
    __tablename__ = "producto"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(50))
    nombre: Mapped[str] = mapped_column(String(160))
    unidad_medida: Mapped[str] = mapped_column(String(30))
    estado: Mapped[bool] = mapped_column(Boolean)


class Lote(Base):
    __tablename__ = "lote"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(BigInteger)
    numero_lote: Mapped[str] = mapped_column(String(80))
    fecha_fabricacion: Mapped[date | None] = mapped_column(Date)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date)
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, default=True)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=fecha_hora_utc)


class Inventario(Base):
    __tablename__ = "inventario"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lote_id: Mapped[int] = mapped_column(BigInteger)
    stock_actual: Mapped[int] = mapped_column(Integer, default=0)
    stock_reservado: Mapped[int] = mapped_column(Integer, default=0)
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=fecha_hora_utc)


class Compra(Base):
    __tablename__ = "compra"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(Integer)
    usuario_id: Mapped[int] = mapped_column(BigInteger)
    numero_documento: Mapped[str | None] = mapped_column(String(80))
    fecha_compra: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_recepcion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moneda: Mapped[str] = mapped_column(String(3))
    estado: Mapped[str] = mapped_column(String(20))
    observaciones: Mapped[str | None] = mapped_column(Text)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DetalleCompra(Base):
    __tablename__ = "detalle_compra"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    compra_id: Mapped[int] = mapped_column(BigInteger)
    lote_id: Mapped[int] = mapped_column(BigInteger)
    cantidad: Mapped[int] = mapped_column(Integer)
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    observaciones: Mapped[str | None] = mapped_column(Text)


class MovimientoInventario(Base):
    __tablename__ = "movimiento_inventario"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    inventario_id: Mapped[int] = mapped_column(BigInteger)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    tipo: Mapped[str] = mapped_column(String(32))
    cantidad: Mapped[int] = mapped_column(Integer)
    stock_anterior: Mapped[int] = mapped_column(Integer)
    stock_posterior: Mapped[int] = mapped_column(Integer)
    fecha_hora: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observaciones: Mapped[str | None] = mapped_column(Text)
    referencia_tipo: Mapped[str | None] = mapped_column(String(40))
    referencia_id: Mapped[int | None] = mapped_column(BigInteger)


