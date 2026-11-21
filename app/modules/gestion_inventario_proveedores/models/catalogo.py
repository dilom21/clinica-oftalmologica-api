"""CU26 - Modelos de tablas existentes: NO crea ni migra tablas.

La base `public` ya cuenta con categoria, producto y lote en postgres y tenants.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Identity, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


class Categoria(Base):
    __tablename__ = "categoria"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    productos: Mapped[list["Producto"]] = relationship(back_populates="categoria")


class Producto(Base):
    __tablename__ = "producto"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    categoria_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("categoria.id", ondelete="RESTRICT", onupdate="CASCADE"), nullable=False)
    codigo: Mapped[str] = mapped_column(String(50), nullable=False)
    nombre: Mapped[str] = mapped_column(String(160), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    unidad_medida: Mapped[str] = mapped_column(String(30), nullable=False, server_default=text("'UNIDAD'"))
    stock_minimo: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    categoria: Mapped[Categoria] = relationship(back_populates="productos")
    lotes: Mapped[list["Lote"]] = relationship(back_populates="producto")


class Lote(Base):
    __tablename__ = "lote"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    producto_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("producto.id", ondelete="RESTRICT", onupdate="CASCADE"), nullable=False)
    numero_lote: Mapped[str] = mapped_column(String(80), nullable=False)
    fecha_fabricacion: Mapped[date | None] = mapped_column(Date)
    fecha_vencimiento: Mapped[date | None] = mapped_column(Date)
    observaciones: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    producto: Mapped[Producto] = relationship(back_populates="lotes")
