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
    ajustes: Mapped[list["PagoAjuste"]] = relationship(
        back_populates="pago",
        order_by=lambda: PagoAjuste.id.asc(),
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


# =========================================================
# CU36 - Libro estructurado de ajustes financieros
# `Pago.monto` y `PagoDetalle.monto_aplicado` son inmutables: toda
# anulacion, correccion, devolucion o reembolso se registra aqui.
# =========================================================

TIPOS_AJUSTE = (
    "ANULACION",
    "CORRECCION",
    "DEVOLUCION",
    "REEMBOLSO",
    "RECONCILIACION",
)
NATURALEZAS_AJUSTE = (
    "ERROR_DE_REGISTRO",
    "PAGO_DUPLICADO",
    "SERVICIO_CANCELADO",
    "SOBREPAGO",
    "PAGO_INSUFICIENTE",
    "REVERSION_FINANCIACION",
    "OTRO",
)
EFECTOS_CAJA_AJUSTE = (
    "SIN_EFECTO",
    "DEVOLUCION_MANUAL",
    "DEVOLUCION_STRIPE",
)
ESTADOS_AJUSTE = (
    "PENDIENTE_PROVEEDOR",
    "CONFIRMADO",
    "FALLIDO",
    "CANCELADO",
    "DESCONOCIDO",
)
ESTADOS_AJUSTE_ABIERTOS = ("PENDIENTE_PROVEEDOR", "DESCONOCIDO")
ESTADOS_PAGO_CAPTURADO = ("APROBADO", "REEMBOLSADO")


class PagoAjuste(Base):
    """Cabecera de una operacion financiera sobre un pago (CU36)."""

    __tablename__ = "pago_ajuste"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    pago_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pago.id", ondelete="RESTRICT"), nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    naturaleza: Mapped[str] = mapped_column(String(40), nullable=False)
    efecto_caja: Mapped[str] = mapped_column(String(20), nullable=False)
    estado: Mapped[str] = mapped_column(
        String(25), nullable=False, server_default=text("'CONFIRMADO'"),
    )
    # Importe firmado del ajuste. Positivo aumenta la cobertura vigente
    # (solo correcciones de registro); negativo la reduce. Vale cero solo
    # para anulaciones y conciliaciones, que no mueven importes.
    importe: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    moneda: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'BOB'"),
    )
    motivo: Mapped[str] = mapped_column(Text, nullable=False)
    evidencia: Mapped[str | None] = mapped_column(Text)
    referencia_externa: Mapped[str | None] = mapped_column(String(255))
    clave_idempotencia: Mapped[str] = mapped_column(String(100), nullable=False)
    revertir_aplicacion_cuota: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    creado_por: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"),
    )
    confirmado_por: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"),
    )
    conciliado_por: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"),
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_confirmacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    fecha_conciliacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    observaciones: Mapped[str | None] = mapped_column(Text)

    pago: Mapped[Pago] = relationship(back_populates="ajustes")
    detalles: Mapped[list["PagoAjusteDetalle"]] = relationship(
        back_populates="ajuste",
        order_by=lambda: PagoAjusteDetalle.id.asc(),
    )

    __table_args__ = (
        CheckConstraint(
            "tipo IN ('ANULACION','CORRECCION','DEVOLUCION','REEMBOLSO',"
            "'RECONCILIACION')",
            name="ck_pago_ajuste_tipo",
        ),
        CheckConstraint(
            "naturaleza IN ('ERROR_DE_REGISTRO','PAGO_DUPLICADO',"
            "'SERVICIO_CANCELADO','SOBREPAGO','PAGO_INSUFICIENTE',"
            "'REVERSION_FINANCIACION','OTRO')",
            name="ck_pago_ajuste_naturaleza",
        ),
        CheckConstraint(
            "efecto_caja IN ('SIN_EFECTO','DEVOLUCION_MANUAL',"
            "'DEVOLUCION_STRIPE')",
            name="ck_pago_ajuste_efecto_caja",
        ),
        CheckConstraint(
            "estado IN ('PENDIENTE_PROVEEDOR','CONFIRMADO','FALLIDO',"
            "'CANCELADO','DESCONOCIDO')",
            name="ck_pago_ajuste_estado",
        ),
        CheckConstraint(
            "(tipo IN ('ANULACION','RECONCILIACION') AND importe = 0)"
            " OR (tipo = 'CORRECCION' AND importe <> 0)"
            " OR (tipo IN ('DEVOLUCION','REEMBOLSO') AND importe < 0)",
            name="ck_pago_ajuste_importe",
        ),
        CheckConstraint(
            "importe <= 0 OR naturaleza = 'ERROR_DE_REGISTRO'",
            name="ck_pago_ajuste_correccion_positiva",
        ),
        CheckConstraint(
            "(tipo = 'REEMBOLSO' AND efecto_caja = 'DEVOLUCION_STRIPE')"
            " OR (tipo = 'DEVOLUCION' AND efecto_caja = 'DEVOLUCION_MANUAL')"
            " OR (tipo IN ('ANULACION','CORRECCION','RECONCILIACION')"
            " AND efecto_caja = 'SIN_EFECTO')",
            name="ck_pago_ajuste_efecto_por_tipo",
        ),
        CheckConstraint(
            "length(moneda) = 3 AND moneda = upper(moneda)",
            name="ck_pago_ajuste_moneda",
        ),
        UniqueConstraint(
            "clave_idempotencia", name="uq_pago_ajuste_clave_idempotencia",
        ),
        Index(
            "uq_pago_ajuste_referencia_externa",
            "referencia_externa",
            unique=True,
            postgresql_where=text("referencia_externa IS NOT NULL"),
            sqlite_where=text("referencia_externa IS NOT NULL"),
        ),
        Index("ix_pago_ajuste_pago_fecha", "pago_id", "fecha_creacion"),
        Index("ix_pago_ajuste_estado", "estado"),
    )


class PagoAjusteDetalle(Base):
    """Distribucion del importe del ajuste por servicio realizado."""

    __tablename__ = "pago_ajuste_detalle"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    ajuste_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("pago_ajuste.id", ondelete="RESTRICT"),
        nullable=False,
    )
    servicio_realizado_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("servicio_realizado.id", ondelete="RESTRICT"),
        nullable=False,
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    ajuste: Mapped[PagoAjuste] = relationship(back_populates="detalles")
    servicio_realizado: Mapped[ServicioRealizado] = relationship()

    __table_args__ = (
        CheckConstraint("monto <> 0", name="ck_pago_ajuste_detalle_monto"),
        UniqueConstraint(
            "ajuste_id", "servicio_realizado_id",
            name="uq_pago_ajuste_detalle_ajuste_servicio",
        ),
        Index(
            "ix_pago_ajuste_detalle_servicio", "servicio_realizado_id",
        ),
    )


ESTADOS_WEBHOOK_HUERFANO = ("PENDIENTE", "ATRIBUIDO", "DESCARTADO")


class StripeWebhookHuerfano(Base):
    """Evento Stripe verificado que no pudo atribuirse a un tenant/ajuste.

    Se persiste antes de responder a Stripe para que un refund creado fuera
    del flujo (por ejemplo, en el dashboard) quede disponible para
    conciliacion manual auditada. Un evento huerfano nunca se contabiliza
    automaticamente ni se atribuye sin refund_id explicito de un administrador.
    """

    __tablename__ = "stripe_webhook_huerfano"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    stripe_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    refund_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_intent_id: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDIENTE'"),
    )
    ajuste_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pago_ajuste.id", ondelete="RESTRICT"),
    )
    motivo: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint(
            "stripe_event_id", name="uq_stripe_webhook_huerfano_evento",
        ),
        CheckConstraint("amount > 0", name="ck_stripe_webhook_huerfano_amount"),
        CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_stripe_webhook_huerfano_moneda",
        ),
        CheckConstraint(
            "estado IN ('PENDIENTE','ATRIBUIDO','DESCARTADO')",
            name="ck_stripe_webhook_huerfano_estado",
        ),
        Index(
            "ix_stripe_webhook_huerfano_refund", "refund_id", "estado",
        ),
        Index(
            "ix_stripe_webhook_huerfano_pi", "payment_intent_id",
        ),
    )
