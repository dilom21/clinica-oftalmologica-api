"""CU29: tablas existentes; catálogo y existencias usan los mapeos canónicos."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from .catalogo import Lote, Producto
from .movimientos import Inventario, MovimientoInventario


class Compra(Base):
    __tablename__ = "compra"
    __table_args__ = (
        CheckConstraint("numero_documento IS NULL OR length(trim(numero_documento)) > 0", name="ck_compra_documento"),
        CheckConstraint("moneda ~ '^[A-Z]{3}$'", name="ck_compra_moneda").ddl_if(dialect="postgresql"),
        CheckConstraint("estado IN ('BORRADOR','CONFIRMADA','RECIBIDA','ANULADA')", name="ck_compra_estado"),
        CheckConstraint("(estado='RECIBIDA' AND fecha_recepcion IS NOT NULL) OR (estado<>'RECIBIDA' AND fecha_recepcion IS NULL)", name="ck_compra_recepcion"),
        CheckConstraint("fecha_recepcion IS NULL OR fecha_recepcion >= fecha_compra", name="ck_compra_fecha_recepcion"),
        Index("uq_compra_proveedor_documento", "proveedor_id", "numero_documento", unique=True, postgresql_where=text("numero_documento IS NOT NULL")),
        Index("idx_compra_proveedor_fecha", "proveedor_id", text("fecha_compra DESC")),
        Index("idx_compra_estado_fecha", "estado", text("fecha_compra DESC")),
        Index("idx_compra_usuario", "usuario_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(Integer, ForeignKey("public.proveedores.id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)
    usuario_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuario.id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)
    numero_documento: Mapped[str | None] = mapped_column(String(80))
    fecha_compra: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    fecha_recepcion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    moneda: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'BOB'"))
    estado: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'BORRADOR'"))
    observaciones: Mapped[str | None] = mapped_column(Text)
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    fecha_actualizacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))


class DetalleCompra(Base):
    __tablename__ = "detalle_compra"
    __table_args__ = (
        UniqueConstraint("compra_id", "lote_id", name="uq_detalle_compra_compra_lote"),
        CheckConstraint("cantidad > 0", name="ck_detalle_compra_cantidad"),
        CheckConstraint("costo_unitario >= 0", name="ck_detalle_compra_costo_unitario"),
        Index("idx_detalle_compra_lote", "lote_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    compra_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("compra.id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)
    lote_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("lote.id", onupdate="CASCADE", ondelete="RESTRICT"), nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    costo_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    observaciones: Mapped[str | None] = mapped_column(Text)
