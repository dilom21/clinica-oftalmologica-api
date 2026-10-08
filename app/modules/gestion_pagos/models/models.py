from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.modules.gestion_historial_clinico.models.models import ServicioRealizado


class Pago(Base):
    __tablename__ = "pago"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_hora_pago: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'BOB'"),
    )
    metodo_pago: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'TARJETA'"),
    )
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDIENTE'"),
    )
    pasarela: Mapped[str | None] = mapped_column(String(50))
    referencia_transaccion: Mapped[str | None] = mapped_column(
        String(255), unique=True,
    )
    observaciones: Mapped[str | None] = mapped_column(Text)

    detalles: Mapped[list["PagoDetalle"]] = relationship(
        back_populates="pago",
        order_by=lambda: PagoDetalle.id.asc(),
    )

    __table_args__ = (
        CheckConstraint("monto > 0", name="ck_pago_monto_positivo"),
        CheckConstraint(
            "length(moneda) = 3 AND moneda = upper(moneda)",
            name="ck_pago_moneda",
        ),
        CheckConstraint(
            "metodo_pago IN ('EFECTIVO', 'TARJETA', 'TRANSFERENCIA', 'QR', 'OTRO')",
            name="ck_pago_metodo",
        ),
        CheckConstraint(
            "estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'ANULADO', 'REEMBOLSADO')",
            name="ck_pago_estado",
        ),
    )


class PagoDetalle(Base):
    __tablename__ = "pago_detalle"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    pago_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("pago.id", ondelete="CASCADE"),
        nullable=False,
    )
    servicio_realizado_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("servicio_realizado.id", ondelete="RESTRICT"),
        nullable=False,
    )
    monto_aplicado: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False,
    )

    pago: Mapped[Pago] = relationship(back_populates="detalles")
    servicio_realizado: Mapped[ServicioRealizado] = relationship()

    __table_args__ = (
        CheckConstraint(
            "monto_aplicado > 0",
            name="ck_pago_detalle_monto_positivo",
        ),
        UniqueConstraint(
            "pago_id",
            "servicio_realizado_id",
            name="uq_pago_detalle_pago_servicio",
        ),
    )
