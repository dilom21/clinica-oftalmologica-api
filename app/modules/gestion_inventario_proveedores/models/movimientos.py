"""CU27: tablas de existencias y movimientos ya migradas a cada tenant.

No se crean tablas desde SQLAlchemy. Las existencias pertenecen a un lote;
los movimientos son inmutables y conservan ambos saldos.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Inventario(Base):
    __tablename__ = "inventario"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    lote_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("lote.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False, unique=True,
    )
    stock_actual: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    stock_reservado: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )


class MovimientoInventario(Base):
    __tablename__ = "movimiento_inventario"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    inventario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("inventario.id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    usuario_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="SET NULL", onupdate="CASCADE"),
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_anterior: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_posterior: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )
    observaciones: Mapped[str | None] = mapped_column(Text)
    referencia_tipo: Mapped[str | None] = mapped_column(String(40))
    referencia_id: Mapped[int | None] = mapped_column(BigInteger)
