from datetime import date, datetime, time
from decimal import Decimal
from ipaddress import IPv4Address, IPv6Address

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class SaasLoginRequest(BaseModel):
    correo: EmailStr
    password: str = Field(min_length=1)


class SaasLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    saas_usuario_id: int


class EmpresaResponse(BaseModel):
    id: int
    codigo: str
    slug: str
    nombre: str | None
    estado: str
    plan: str | None
    estado_suscripcion: str | None
    database_name: str | None
    estado_tenant: str | None
    version_schema: str | None
    fecha_provisionamiento: datetime | None


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    codigo: str
    nombre: str | None
    descripcion: str | None
    precio_mensual: Decimal | None
    moneda: str | None
    limite_usuarios: int | None
    limite_almacenamiento_mb: int | None
    estado: bool | None


class SuscripcionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    empresa_id: int
    plan_id: int
    fecha_inicio: date
    fecha_fin: date
    estado: str


class TenantResponse(BaseModel):
    empresa: int
    database_name: str
    estado: str
    version_schema: str | None
    fecha_provisionamiento: datetime | None
    ultima_verificacion: datetime | None


class ProvisionamientoResponse(BaseModel):
    id: int
    empresa_id: int
    tenant_database_id: int
    estado: str
    paso_actual: str | None
    intentos: int
    fecha_inicio: datetime | None
    fecha_fin: datetime | None
    mensaje_error: str | None


class BitacoraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    saas_usuario_id: int | None
    fecha_hora: datetime | None
    ip: str | None
    accion: str
    entidad_afectada: str | None
    id_registro_afectado: int | None
    descripcion: str | None
    resultado: str | None

    @field_validator("ip", mode="before")
    @classmethod
    def serialize_inet(cls, value):
        if isinstance(value, (IPv4Address, IPv6Address)):
            return str(value)
        return value


class EstadoRequest(BaseModel):
    estado: str = Field(min_length=1, max_length=20)


class BackupCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    empresa_id: int = Field(gt=0)


class BackupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    empresa_id: int
    tenant_database_id: int
    tipo: str
    estado: str
    nombre_archivo: str | None
    formato: str | None
    size_bytes: int | None
    sha256: str | None
    version_schema: str | None
    fecha_inicio: datetime
    fecha_fin: datetime | None


class RestoreValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: int = Field(gt=0)


class RestoreCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backup_id: int = Field(gt=0)
    confirmacion: str = Field(min_length=1, max_length=60)


class RestoreValidateResponse(BaseModel):
    backup_id: int
    empresa_id: int
    tenant_database_id: int
    estado: str
    tipo: str
    size_bytes: int | None
    sha256: str | None
    version_schema: str | None
    valido: bool


class RestoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    empresa_id: int
    tenant_database_id: int
    backup_id: int
    pre_restore_backup_id: int | None
    estado: str
    etapa: str | None
    fecha_inicio: datetime
    fecha_fin: datetime | None
    mensaje_error: str | None
    rollback_estado: str | None
    rollback_mensaje: str | None


class BackupPolicyUpdateRequest(BaseModel):
    """Scheduling inputs accepted from the SaaS console; everything else is rejected."""

    model_config = ConfigDict(extra="forbid")
    habilitado: bool
    frecuencia: str = Field(min_length=1, max_length=20)
    hora_local: time
    timezone: str = Field(min_length=1, max_length=64)
    retencion_cantidad: int = Field(ge=1, le=365)

    @field_validator("hora_local")
    @classmethod
    def hora_local_must_be_naive(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("hora_local must not include a timezone offset")
        return value


class BackupPolicyResponse(BaseModel):
    empresa_id: int
    empresa_codigo: str
    empresa_nombre: str | None
    estado_empresa: str
    configurada: bool
    habilitado: bool | None
    frecuencia: str | None
    hora_local: time | None
    timezone: str | None
    retencion_cantidad: int | None
    ultimo_backup_automatico: datetime | None
    proximo_backup: datetime | None
    fecha_actualizacion: datetime | None
