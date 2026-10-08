from datetime import date, datetime, time

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Identity, Index, Integer, Numeric, String, Text, Time, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.core.time import fecha_hora_utc


class Empresa(Base):
    __tablename__ = "empresa"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    razon_social: Mapped[str | None] = mapped_column(String(160))
    nombre_comercial: Mapped[str | None] = mapped_column(String(160))
    nit: Mapped[str | None] = mapped_column(String(30))
    correo: Mapped[str | None] = mapped_column(String(150))
    telefono: Mapped[str | None] = mapped_column(String(30))
    direccion: Mapped[str | None] = mapped_column(String(255))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    fecha_registro: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_actualizacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanSaas(Base):
    __tablename__ = "plan_saas"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), nullable=False)
    nombre: Mapped[str | None] = mapped_column(String(80))
    descripcion: Mapped[str | None] = mapped_column(String(255))
    precio_mensual: Mapped[float | None] = mapped_column(Numeric(12, 2))
    moneda: Mapped[str | None] = mapped_column(String(3))
    limite_usuarios: Mapped[int | None] = mapped_column(Integer)
    limite_almacenamiento_mb: Mapped[int | None] = mapped_column(Integer)
    estado: Mapped[bool | None] = mapped_column(Boolean)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Suscripcion(Base):
    __tablename__ = "suscripcion"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    renovacion_auto: Mapped[bool | None] = mapped_column(Boolean)
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenantDatabase(Base):
    __tablename__ = "tenant_database"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    database_name: Mapped[str] = mapped_column(String(63), nullable=False)
    host_alias: Mapped[str | None] = mapped_column(String(100))
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    version_schema: Mapped[str | None] = mapped_column(String(30))
    fecha_provisionamiento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_verificacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SaasUsuario(Base):
    __tablename__ = "saas_usuario"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    correo: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nombres: Mapped[str] = mapped_column(String(100), nullable=False)
    apellidos: Mapped[str] = mapped_column(String(120), nullable=False)
    rol: Mapped[str] = mapped_column(String(30), nullable=False)
    estado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=fecha_hora_utc
    )
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SaasBitacora(Base):
    __tablename__ = "saas_bitacora"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    saas_usuario_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("saas_control.saas_usuario.id", ondelete="SET NULL"),
    )
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ip: Mapped[str | None] = mapped_column(INET)
    accion: Mapped[str] = mapped_column(String(100), nullable=False)
    entidad_afectada: Mapped[str | None] = mapped_column(String(100))
    id_registro_afectado: Mapped[int | None] = mapped_column(BigInteger)
    descripcion: Mapped[str | None] = mapped_column(Text)
    resultado: Mapped[str | None] = mapped_column(String(20))


class ProvisionamientoTenant(Base):
    __tablename__ = "provisionamiento_tenant"
    __table_args__ = {"schema": "saas_control"}
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_database_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    estado: Mapped[str] = mapped_column(String(20), nullable=False)
    paso_actual: Mapped[str | None] = mapped_column(String(100))
    intentos: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fecha_inicio: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mensaje_error: Mapped[str | None] = mapped_column(Text)


class BackupTenant(Base):
    __tablename__ = "backup_tenant"
    __table_args__ = (
        Index("uq_backup_tenant_en_proceso", "tenant_database_id", unique=True,
              postgresql_where=text("estado = 'EN_PROCESO'"),
              sqlite_where=text("estado = 'EN_PROCESO'")),
        Index("idx_backup_empresa_inicio", "empresa_id", "fecha_inicio"),
        Index("idx_backup_estado_tipo", "estado", "tipo"),
        Index("uq_backup_tenant_automatico_ventana", "empresa_id", "ventana", unique=True,
              postgresql_where=text("tipo = 'AUTOMATICO' AND ventana IS NOT NULL AND estado <> 'ERROR'"),
              sqlite_where=text("tipo = 'AUTOMATICO' AND ventana IS NOT NULL AND estado <> 'ERROR'")),
        {"schema": "saas_control"},
    )
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("saas_control.empresa.id"), nullable=False)
    tenant_database_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("saas_control.tenant_database.id"), nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    ventana: Mapped[str | None] = mapped_column(String(80))
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="EN_PROCESO")
    storage_key: Mapped[str | None] = mapped_column(String(255))
    nombre_archivo: Mapped[str | None] = mapped_column(String(160))
    formato: Mapped[str | None] = mapped_column(String(20))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    version_schema: Mapped[str | None] = mapped_column(String(30))
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=fecha_hora_utc)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_por_saas_usuario_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("saas_control.saas_usuario.id", ondelete="SET NULL")
    )
    mensaje_error: Mapped[str | None] = mapped_column(Text)


class RestoreTenant(Base):
    __tablename__ = "restore_tenant"
    __table_args__ = (
        Index("uq_restore_tenant_activo", "tenant_database_id", unique=True,
              postgresql_where=text("estado IN ('PENDIENTE', 'EN_PROCESO', 'ROLLBACK_EN_PROCESO')"),
              sqlite_where=text("estado IN ('PENDIENTE', 'EN_PROCESO', 'ROLLBACK_EN_PROCESO')")),
        Index("idx_restore_empresa_inicio", "empresa_id", "fecha_inicio"),
        Index("idx_restore_estado", "estado"),
        Index("idx_restore_backup", "backup_id"),
        {"schema": "saas_control"},
    )
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("saas_control.empresa.id"), nullable=False)
    tenant_database_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("saas_control.tenant_database.id"), nullable=False)
    backup_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("saas_control.backup_tenant.id"), nullable=False)
    pre_restore_backup_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("saas_control.backup_tenant.id")
    )
    estado: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDIENTE")
    etapa: Mapped[str | None] = mapped_column(String(50))
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=fecha_hora_utc)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_por_saas_usuario_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("saas_control.saas_usuario.id", ondelete="SET NULL")
    )
    mensaje_error: Mapped[str | None] = mapped_column(Text)
    rollback_estado: Mapped[str | None] = mapped_column(String(30))
    rollback_mensaje: Mapped[str | None] = mapped_column(Text)


class BackupPolicy(Base):
    __tablename__ = "backup_policy"
    __table_args__ = (
        Index("idx_backup_policy_habilitado", "habilitado", "proximo_backup"),
        {"schema": "saas_control"},
    )
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True)
    empresa_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("saas_control.empresa.id"), nullable=False, unique=True)
    habilitado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    frecuencia: Mapped[str] = mapped_column(String(20), nullable=False, default="DIARIA")
    hora_local: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/La_Paz")
    retencion_cantidad: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    ultimo_backup_automatico: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proximo_backup: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fecha_creacion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=fecha_hora_utc)
    fecha_actualizacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
