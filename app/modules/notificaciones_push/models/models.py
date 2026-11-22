"""Modelos de persistencia de notificaciones push (Fase 3C.2).

Separacion de bases:

* ``dispositivo_push``, ``notificacion``, ``notificacion_entrega``,
  ``preferencia_notificacion`` y ``cita_evento`` viven en la base **tenant**.
  Los IDs de usuario son locales a esa base: no se agrega ``tenant_id`` ni
  ``empresa_id`` porque cada empresa tiene su propia base fisica.
* ``saas_control.dispositivo_push_registry`` vive en el **Control Plane** y solo
  conserva una huella SHA-256 determinista del token FCM. Nunca guarda el token
  en claro ni datos personales o clinicos.

El token FCM en claro es un secreto operativo: se almacena unicamente en
``dispositivo_push.token_fcm`` (base tenant), no se incluye en ``repr`` y no se
expone en ningun schema de salida.
"""

from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


PLATAFORMAS_PUSH = ("ANDROID", "IOS", "WEB")

TIPOS_NOTIFICACION = (
    "CITA_CREADA",
    "CITA_CONFIRMADA",
    "CITA_REPROGRAMADA",
    "CITA_CANCELADA",
    "RECORDATORIO_CITA",
    "ACTUALIZACION_OPERATIVA",
)

ESTADOS_ENTREGA = (
    "PENDIENTE",
    "PROCESANDO",
    "ACEPTADO_POR_FCM",
    "FALLIDO",
    "INVALIDO",
    # Fase 3C.5: entrega descartada sin enviar (preferencia push desactivada,
    # dispositivo ya no elegible o notificacion cancelada). No es un fallo del
    # proveedor ni un token invalido.
    "OMITIDO",
)

ESTADOS_CITA = (
    "PROGRAMADA",
    "CONFIRMADA",
    "EN_ESPERA",
    "ATENDIDA",
    "CANCELADA",
    "NO_ASISTIO",
)

TIPOS_EVENTO_CITA = (
    "CITA_CREADA",
    "CITA_ESTADO_CAMBIADO",
    "CITA_REPROGRAMADA",
)

ESTADOS_PROPIEDAD_TOKEN = ("PENDIENTE", "ACTIVO", "REVOCADO")

# Fase 3C.5.2: estados de la solicitud durable de revocacion global.
# ``PENDIENTE``/``PROCESANDO`` son recuperables; ``COMPLETADA`` confirma que el
# Control Plane ya quedo ``REVOCADO``; ``NO_ENCONTRADO`` y ``RECHAZADA`` son
# terminales con evidencia (no se reintentan solos); ``FALLIDA`` agoto intentos
# ante estados inesperados del registro global.
ESTADOS_REVOCACION_GLOBAL = (
    "PENDIENTE",
    "PROCESANDO",
    "COMPLETADA",
    "NO_ENCONTRADO",
    "RECHAZADA",
    "FALLIDA",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return "{} IN ({})".format(
        column,
        ", ".join(f"'{value}'" for value in values),
    )


class DispositivoPush(Base):
    """Dispositivo movil/web de un usuario del tenant.

    La revocacion es logica (``activo = false`` y ``revocado_en``) para no
    perder la trazabilidad de las entregas historicas. El borrado fisico solo
    puede ocurrir cuando ya no exista evidencia asociada; por eso
    ``notificacion_entrega.dispositivo_id`` usa ``ON DELETE RESTRICT``.
    """

    __tablename__ = "dispositivo_push"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False,
    )
    token_fcm: Mapped[str] = mapped_column(String(1024), nullable=False)
    plataforma: Mapped[str] = mapped_column(String(10), nullable=False)
    instalacion_id: Mapped[str | None] = mapped_column(String(100))
    app_version: Mapped[str | None] = mapped_column(String(50))
    nombre_dispositivo: Mapped[str | None] = mapped_column(String(120))
    activo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_ultimo_uso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            _in_list("plataforma", PLATAFORMAS_PUSH),
            name="ck_dispositivo_push_plataforma",
        ),
        CheckConstraint(
            "(activo AND revocado_en IS NULL) OR (NOT activo)",
            name="ck_dispositivo_push_revocacion",
        ),
        # Un token FCM solo puede estar activo una vez dentro del tenant.
        Index(
            "uq_dispositivo_push_token_activo",
            "token_fcm",
            unique=True,
            postgresql_where=text("activo"),
            sqlite_where=text("activo = 1"),
        ),
        Index("ix_dispositivo_push_usuario_activo", "usuario_id", "activo"),
    )

    def __repr__(self) -> str:  # El token FCM nunca se serializa.
        return (
            f"<DispositivoPush id={self.id!r} usuario_id={self.usuario_id!r} "
            f"plataforma={self.plataforma!r} activo={self.activo!r}>"
        )


class Notificacion(Base):
    """Notificacion de bandeja, independiente de que existan dispositivos.

    ``titulo``, ``cuerpo`` y ``datos`` solo admiten contenido operativo. No
    deben contener diagnostico, motivo, observaciones ni cualquier dato clinico.
    """

    __tablename__ = "notificacion"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    evento_clave: Mapped[str] = mapped_column(String(150), nullable=False)
    titulo: Mapped[str] = mapped_column(String(150), nullable=False)
    cuerpo: Mapped[str] = mapped_column(Text, nullable=False)
    datos: Mapped[dict | None] = mapped_column(JSON)
    cita_id: Mapped[int | None] = mapped_column(
        # La cita puede retirarse administrativamente; la notificacion ya
        # emitida conserva su evidencia y solo pierde el enlace.
        BigInteger, ForeignKey("cita.id", ondelete="SET NULL"),
    )
    leida: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE"),
    )
    fecha_lectura: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        CheckConstraint(
            _in_list("tipo", TIPOS_NOTIFICACION),
            name="ck_notificacion_tipo",
        ),
        CheckConstraint(
            "(leida AND fecha_lectura IS NOT NULL) "
            "OR (NOT leida AND fecha_lectura IS NULL)",
            name="ck_notificacion_lectura_coherente",
        ),
        UniqueConstraint("evento_clave", name="uq_notificacion_evento_clave"),
        Index(
            "ix_notificacion_usuario_fecha",
            "usuario_id",
            desc("fecha_creacion"),
        ),
        Index("ix_notificacion_usuario_leida", "usuario_id", "leida"),
    )


class NotificacionEntrega(Base):
    """Outbox por dispositivo. Nunca se borra en cascada: es evidencia.

    ``ACEPTADO_POR_FCM`` significa que FCM acepto la solicitud; no implica
    entrega ni lectura.
    """

    __tablename__ = "notificacion_entrega"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    notificacion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notificacion.id", ondelete="RESTRICT"),
        nullable=False,
    )
    dispositivo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dispositivo_push.id", ondelete="RESTRICT"),
        nullable=False,
    )
    estado: Mapped[str] = mapped_column(
        String(25), nullable=False, server_default=text("'PENDIENTE'"),
    )
    proveedor: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'FCM'"),
    )
    proveedor_mensaje_id: Mapped[str | None] = mapped_column(String(255))
    intentos: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"),
    )
    proximo_intento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    codigo_ultimo_error: Mapped[str | None] = mapped_column(String(100))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_aceptado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            _in_list("estado", ESTADOS_ENTREGA),
            name="ck_notificacion_entrega_estado",
        ),
        CheckConstraint(
            "proveedor = 'FCM'",
            name="ck_notificacion_entrega_proveedor",
        ),
        CheckConstraint("intentos >= 0", name="ck_notificacion_entrega_intentos"),
        UniqueConstraint(
            "notificacion_id",
            "dispositivo_id",
            name="uq_notificacion_entrega_notificacion_dispositivo",
        ),
        Index(
            "uq_notificacion_entrega_proveedor_mensaje",
            "proveedor_mensaje_id",
            unique=True,
            postgresql_where=text("proveedor_mensaje_id IS NOT NULL"),
            sqlite_where=text("proveedor_mensaje_id IS NOT NULL"),
        ),
        Index(
            "ix_notificacion_entrega_cola",
            "estado",
            "proximo_intento",
        ),
        Index(
            "ix_notificacion_entrega_lease",
            "bloqueado_hasta",
            postgresql_where=text("estado = 'PROCESANDO'"),
            sqlite_where=text("estado = 'PROCESANDO'"),
        ),
    )


class PreferenciaNotificacion(Base):
    """Una fila por usuario. Desactivar push no oculta la bandeja."""

    __tablename__ = "preferencia_notificacion"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False,
    )
    push_habilitado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    citas_creadas: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    citas_confirmadas: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    citas_reprogramadas: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    citas_canceladas: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    recordatorios: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("usuario_id", name="uq_preferencia_notificacion_usuario"),
    )


class CitaEvento(Base):
    """Evento estructurado de cita; reemplaza a ``bitacora.descripcion``.

    No contiene datos del paciente, motivo, observaciones ni texto clinico.
    ``REPROGRAMADA`` es un tipo de evento, nunca un estado de cita.
    """

    __tablename__ = "cita_evento"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    cita_id: Mapped[int] = mapped_column(
        # Evidencia de auditoria: la cita no se borra fisicamente.
        BigInteger, ForeignKey("cita.id", ondelete="RESTRICT"), nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    estado_anterior: Mapped[str | None] = mapped_column(String(20))
    estado_nuevo: Mapped[str | None] = mapped_column(String(20))
    fecha_anterior: Mapped[date | None] = mapped_column(Date)
    hora_anterior: Mapped[time | None] = mapped_column(Time)
    fecha_nueva: Mapped[date | None] = mapped_column(Date)
    hora_nueva: Mapped[time | None] = mapped_column(Time)
    fecha_evento: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    actor_usuario_id: Mapped[int | None] = mapped_column(
        # El evento debe sobrevivir aunque el actor sea dado de baja.
        BigInteger, ForeignKey("usuario.id", ondelete="SET NULL"),
    )
    clave_idempotencia: Mapped[str] = mapped_column(String(150), nullable=False)

    __table_args__ = (
        CheckConstraint(
            _in_list("tipo", TIPOS_EVENTO_CITA),
            name="ck_cita_evento_tipo",
        ),
        CheckConstraint(
            "estado_anterior IS NULL OR " + _in_list("estado_anterior", ESTADOS_CITA),
            name="ck_cita_evento_estado_anterior",
        ),
        CheckConstraint(
            "estado_nuevo IS NULL OR " + _in_list("estado_nuevo", ESTADOS_CITA),
            name="ck_cita_evento_estado_nuevo",
        ),
        CheckConstraint(
            "tipo <> 'CITA_CREADA' OR ("
            "estado_anterior IS NULL AND fecha_anterior IS NULL "
            "AND hora_anterior IS NULL)",
            name="ck_cita_evento_creacion",
        ),
        CheckConstraint(
            "tipo <> 'CITA_ESTADO_CAMBIADO' OR ("
            "estado_anterior IS NOT NULL AND estado_nuevo IS NOT NULL "
            "AND estado_anterior <> estado_nuevo)",
            name="ck_cita_evento_cambio_estado",
        ),
        CheckConstraint(
            "tipo <> 'CITA_REPROGRAMADA' OR ("
            "fecha_anterior IS NOT NULL AND hora_anterior IS NOT NULL "
            "AND fecha_nueva IS NOT NULL AND hora_nueva IS NOT NULL "
            "AND (fecha_anterior <> fecha_nueva "
            "OR hora_anterior <> hora_nueva))",
            name="ck_cita_evento_reprogramacion",
        ),
        UniqueConstraint(
            "clave_idempotencia", name="uq_cita_evento_clave_idempotencia",
        ),
        Index("ix_cita_evento_cita_fecha", "cita_id", "fecha_evento"),
        Index("ix_cita_evento_tipo_fecha", "tipo", "fecha_evento"),
    )


class RevocacionPushGlobal(Base):
    """Solicitud durable de revocacion de una huella FCM (Fase 3C.5.2).

    Vive en la base **tenant** y actua como outbox entre el worker FCM y el
    proceso que actualiza ``saas_control.dispositivo_push_registry``. Solo
    guarda la huella SHA-256 calculada en backend, nunca el token original.

    ``(dispositivo_id, token_fingerprint)`` es la clave de idempotencia: el
    mismo dispositivo y token invalidado no generan dos solicitudes. Si el
    dispositivo cambia de token, la nueva huella produce una solicitud nueva.

    La tabla no se borra en cascada: es evidencia de la revocacion.
    """

    __tablename__ = "dispositivo_push_revocacion"

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    dispositivo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("dispositivo_push.id", ondelete="RESTRICT"),
        nullable=False,
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"), nullable=False,
    )
    token_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDIENTE'"),
    )
    intentos: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"),
    )
    proximo_intento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    codigo_ultimo_error: Mapped[str | None] = mapped_column(String(100))
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_procesado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "length(token_fingerprint) = 64 "
            "AND token_fingerprint = lower(token_fingerprint)",
            name="ck_dispositivo_push_revocacion_huella",
        ),
        CheckConstraint(
            _in_list("estado", ESTADOS_REVOCACION_GLOBAL),
            name="ck_dispositivo_push_revocacion_estado",
        ),
        CheckConstraint(
            "intentos >= 0", name="ck_dispositivo_push_revocacion_intentos",
        ),
        UniqueConstraint(
            "dispositivo_id",
            "token_fingerprint",
            name="uq_dispositivo_push_revocacion_dispositivo_huella",
        ),
        Index(
            "ix_dispositivo_push_revocacion_cola",
            "estado",
            "proximo_intento",
        ),
        Index(
            "ix_dispositivo_push_revocacion_lease",
            "bloqueado_hasta",
            postgresql_where=text("estado = 'PROCESANDO'"),
            sqlite_where=text("estado = 'PROCESANDO'"),
        ),
        Index(
            "ix_dispositivo_push_revocacion_usuario",
            "usuario_id",
            "estado",
        ),
    )

    def __repr__(self) -> str:  # La huella nunca se serializa.
        return (
            f"<RevocacionPushGlobal id={self.id!r} "
            f"dispositivo_id={self.dispositivo_id!r} estado={self.estado!r}>"
        )


class DispositivoPushRegistry(Base):
    """Registro global minimo de propiedad de tokens (solo huellas).

    Vive en ``saas_control``. No contiene token FCM, correo, nombre, paciente,
    datos clinicos ni contenido de notificaciones. ``usuario_id`` solo tiene
    sentido junto a ``tenant_database_id`` porque los IDs son locales al tenant.
    """

    __tablename__ = "dispositivo_push_registry"
    __table_args__ = (
        CheckConstraint(
            "length(token_fingerprint) = 64 "
            "AND token_fingerprint = lower(token_fingerprint)",
            name="ck_dispositivo_push_registry_huella",
        ),
        CheckConstraint(
            _in_list("estado", ESTADOS_PROPIEDAD_TOKEN),
            name="ck_dispositivo_push_registry_estado",
        ),
        CheckConstraint(
            "huella_version >= 1",
            name="ck_dispositivo_push_registry_version",
        ),
        CheckConstraint(
            "(estado = 'REVOCADO' AND fecha_revocacion IS NOT NULL) "
            "OR (estado <> 'REVOCADO' AND fecha_revocacion IS NULL)",
            name="ck_dispositivo_push_registry_revocacion",
        ),
        CheckConstraint("usuario_id > 0", name="ck_dispositivo_push_registry_usuario"),
        UniqueConstraint(
            "token_fingerprint", name="uq_dispositivo_push_registry_huella",
        ),
        Index(
            "ix_dispositivo_push_registry_tenant_usuario",
            "tenant_database_id",
            "usuario_id",
        ),
        {"schema": "saas_control"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Identity(always=True), primary_key=True,
    )
    token_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_database_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("saas_control.tenant_database.id", ondelete="RESTRICT"),
        nullable=False,
    )
    usuario_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estado: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'ACTIVO'"),
    )
    huella_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"),
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    fecha_revocacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return (
            f"<DispositivoPushRegistry id={self.id!r} "
            f"tenant_database_id={self.tenant_database_id!r} "
            f"estado={self.estado!r}>"
        )
