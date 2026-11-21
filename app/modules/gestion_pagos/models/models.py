from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
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
    clave_idempotencia: Mapped[str | None] = mapped_column(
        String(100),
    )

    detalles: Mapped[list["PagoDetalle"]] = relationship(
        back_populates="pago",
        order_by=lambda: PagoDetalle.id.asc(),
    )
    intento_cuota: Mapped["IntentoPagoCuota | None"] = relationship(
        back_populates="pago",
        uselist=False,
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
        Index(
            "uq_pago_clave_idempotencia",
            "clave_idempotencia",
            unique=True,
            postgresql_where=text("clave_idempotencia IS NOT NULL"),
            sqlite_where=text("clave_idempotencia IS NOT NULL"),
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


class NotificacionPago(Base):
    """Outbox durable; Resend nunca participa del commit financiero."""

    __tablename__ = "notificacion_pago"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    pago_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pago.id", ondelete="RESTRICT"), nullable=False,
    )
    tipo: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'COMPROBANTE_PAGO'"),
    )
    estado: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'PENDIENTE'"),
    )
    destinatario: Mapped[str | None] = mapped_column(String(150))
    clave_idempotencia: Mapped[str] = mapped_column(String(200), nullable=False)
    proveedor: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("'RESEND'"),
    )
    proveedor_mensaje_id: Mapped[str | None] = mapped_column(String(255))
    intentos: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"),
    )
    proximo_intento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_error: Mapped[str | None] = mapped_column(String(255))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_aceptado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_entregado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pago: Mapped[Pago] = relationship()

    __table_args__ = (
        UniqueConstraint("pago_id", "tipo", name="uq_notificacion_pago_tipo"),
        UniqueConstraint("clave_idempotencia", name="uq_notificacion_pago_clave"),
        CheckConstraint(
            "estado IN ('PENDIENTE','PROCESANDO','ACEPTADO_POR_PROVEEDOR','FALLIDO','ENTREGADO')",
            name="ck_notificacion_pago_estado",
        ),
        CheckConstraint("intentos >= 0", name="ck_notificacion_pago_intentos"),
        Index(
            "uq_notificacion_pago_proveedor_mensaje",
            "proveedor_mensaje_id",
            unique=True,
            postgresql_where=text("proveedor_mensaje_id IS NOT NULL"),
            sqlite_where=text("proveedor_mensaje_id IS NOT NULL"),
        ),
    )


class PlanPago(Base):
    __tablename__ = "plan_pago"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    paciente_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("paciente.id", ondelete="RESTRICT"), nullable=False,
    )
    consulta_clinica_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("consulta_clinica.id", ondelete="RESTRICT"),
        nullable=False,
    )
    monto_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'BOB'"),
    )
    cantidad_cuotas: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVO'"),
    )
    observaciones: Mapped[str | None] = mapped_column(Text)
    creado_por: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False,
    )
    clave_idempotencia: Mapped[str] = mapped_column(String(100), nullable=False)

    servicios: Mapped[list["PlanPagoServicio"]] = relationship(
        back_populates="plan", order_by=lambda: PlanPagoServicio.servicio_realizado_id.asc(),
    )
    cuotas: Mapped[list["Cuota"]] = relationship(
        back_populates="plan", order_by=lambda: Cuota.numero_cuota.asc(),
    )

    __table_args__ = (
        CheckConstraint("monto_total > 0", name="ck_plan_pago_monto"),
        CheckConstraint("moneda = 'BOB'", name="ck_plan_pago_moneda"),
        CheckConstraint("cantidad_cuotas > 0", name="ck_plan_pago_cantidad"),
        CheckConstraint(
            "estado IN ('ACTIVO','COMPLETADO','CANCELADO')",
            name="ck_plan_pago_estado",
        ),
        UniqueConstraint("clave_idempotencia", name="uq_plan_pago_clave_idempotencia"),
        Index("ix_plan_pago_paciente_estado", "paciente_id", "estado", "fecha_creacion"),
        Index("ix_plan_pago_consulta", "consulta_clinica_id"),
        Index("ix_plan_pago_creado_por", "creado_por"),
    )


class PlanPagoServicio(Base):
    __tablename__ = "plan_pago_servicio"

    plan_pago_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("plan_pago.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    servicio_realizado_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("servicio_realizado.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    monto_financiado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )

    plan: Mapped[PlanPago] = relationship(back_populates="servicios")
    servicio_realizado: Mapped[ServicioRealizado] = relationship()

    __table_args__ = (
        CheckConstraint("monto_financiado > 0", name="ck_pps_monto"),
        Index(
            "uq_pps_servicio_comprometido",
            "servicio_realizado_id",
            unique=True,
            postgresql_where=text("activo IS TRUE"),
            sqlite_where=text("activo IS 1"),
        ),
        Index("ix_pps_servicio_historial", "servicio_realizado_id"),
    )


class Cuota(Base):
    __tablename__ = "cuota"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    plan_pago_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("plan_pago.id", ondelete="RESTRICT"), nullable=False,
    )
    numero_cuota: Mapped[int] = mapped_column(Integer, nullable=False)
    monto_programado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDIENTE'"),
    )

    plan: Mapped[PlanPago] = relationship(back_populates="cuotas")
    intentos: Mapped[list["IntentoPagoCuota"]] = relationship(
        back_populates="cuota", order_by=lambda: IntentoPagoCuota.fecha_creacion.desc(),
    )

    __table_args__ = (
        UniqueConstraint("plan_pago_id", "numero_cuota", name="uq_cuota_plan_numero"),
        CheckConstraint("numero_cuota > 0", name="ck_cuota_numero"),
        CheckConstraint("monto_programado > 0", name="ck_cuota_monto"),
        CheckConstraint(
            "estado IN ('PENDIENTE','PAGADA','CANCELADA')",
            name="ck_cuota_estado",
        ),
        Index(
            "ix_cuota_plan_estado_vencimiento",
            "plan_pago_id", "estado", "fecha_vencimiento",
        ),
    )


class IntentoPagoCuota(Base):
    __tablename__ = "intento_pago_cuota"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    cuota_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cuota.id", ondelete="RESTRICT"), nullable=False,
    )
    pago_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("pago.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"),
    )
    aplicado_a_cuota: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )

    cuota: Mapped[Cuota] = relationship(back_populates="intentos")
    pago: Mapped[Pago] = relationship(back_populates="intento_cuota")

    __table_args__ = (
        Index(
            "uq_intento_cuota_aplicada",
            "cuota_id",
            unique=True,
            postgresql_where=text("aplicado_a_cuota IS TRUE"),
            sqlite_where=text("aplicado_a_cuota IS 1"),
        ),
        Index("ix_intento_cuota_historial", "cuota_id", "fecha_creacion"),
    )
