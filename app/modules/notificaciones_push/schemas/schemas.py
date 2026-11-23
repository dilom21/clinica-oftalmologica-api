"""DTO de persistencia (no son contratos HTTP de esta fase).

Fase 3C.2 no expone endpoints. Estos schemas validan datos que la futura capa
de servicio entregara a los repositorios y permiten probar que:

* no se aceptan campos no declarados (``extra="forbid"``);
* el token FCM nunca aparece en la representacion de un schema de salida;
* ``datos``/``titulo``/``cuerpo`` no transportan claves clinicas;
* los eventos de cita son coherentes con su tipo.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.notificaciones_push.models.models import ESTADOS_CITA


PlataformaPush = Literal["ANDROID", "IOS", "WEB"]
TipoNotificacion = Literal[
    "CITA_CREADA",
    "CITA_CONFIRMADA",
    "CITA_REPROGRAMADA",
    "CITA_CANCELADA",
    "RECORDATORIO_CITA",
    "ACTUALIZACION_OPERATIVA",
]
TipoEventoCita = Literal[
    "CITA_CREADA",
    "CITA_ESTADO_CAMBIADO",
    "CITA_REPROGRAMADA",
]

# Claves que jamas deben viajar en el JSON de navegacion de una notificacion.
CLAVES_CLINICAS_PROHIBIDAS = frozenset({
    "diagnostico",
    "diagnosticos",
    "motivo",
    "motivo_consulta",
    "observaciones",
    "anamnesis",
    "receta",
    "tratamiento",
    "indicaciones",
    "examen",
    "examenes",
    "resultado",
    "resultados",
    "antecedente",
    "antecedentes",
    "historial",
    "historial_clinico",
    "paciente",
    "paciente_id",
})


class _Persistencia(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class DispositivoPushPersistencia(_Persistencia):
    """Alta/actualizacion de un dispositivo del tenant autenticado.

    ``token_fcm`` se excluye de ``repr`` y nunca debe registrarse en logs.
    """

    usuario_id: int = Field(gt=0)
    token_fcm: str = Field(min_length=20, max_length=1024, repr=False)
    plataforma: PlataformaPush
    instalacion_id: str | None = Field(default=None, max_length=100)
    app_version: str | None = Field(default=None, max_length=50)
    nombre_dispositivo: str | None = Field(default=None, max_length=120)


class DispositivoPushVista(_Persistencia):
    """Vista interna sin token: nunca incluye ``token_fcm``."""

    id: int
    usuario_id: int
    plataforma: PlataformaPush
    instalacion_id: str | None = None
    app_version: str | None = None
    nombre_dispositivo: str | None = None
    activo: bool
    fecha_registro: datetime
    fecha_actualizacion: datetime
    fecha_ultimo_uso: datetime | None = None
    revocado_en: datetime | None = None


class NotificacionPersistencia(_Persistencia):
    usuario_id: int = Field(gt=0)
    tipo: TipoNotificacion
    evento_clave: str = Field(min_length=1, max_length=150)
    titulo: str = Field(min_length=1, max_length=150)
    cuerpo: str = Field(min_length=1)
    datos: dict | None = None
    cita_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _sin_datos_clinicos(self) -> "NotificacionPersistencia":
        if self.datos:
            normalizadas = {str(clave).strip().lower() for clave in self.datos}
            filtradas = normalizadas & CLAVES_CLINICAS_PROHIBIDAS
            if filtradas:
                raise ValueError(
                    "La notificacion no puede incluir datos clinicos: "
                    + ", ".join(sorted(filtradas))
                )
        return self


class NotificacionVista(_Persistencia):
    id: int
    usuario_id: int
    tipo: TipoNotificacion
    evento_clave: str
    titulo: str
    cuerpo: str
    datos: dict | None = None
    cita_id: int | None = None
    leida: bool
    fecha_lectura: datetime | None = None
    cancelada_en: datetime | None = None
    fecha_creacion: datetime
    fecha_actualizacion: datetime


class NotificacionEntregaPersistencia(_Persistencia):
    notificacion_id: int = Field(gt=0)
    dispositivo_id: int = Field(gt=0)


class NotificacionEntregaVista(_Persistencia):
    id: int
    notificacion_id: int
    dispositivo_id: int
    estado: str
    proveedor: str
    proveedor_mensaje_id: str | None = None
    intentos: int
    proximo_intento: datetime | None = None
    bloqueado_hasta: datetime | None = None
    codigo_ultimo_error: str | None = None
    fecha_creacion: datetime
    fecha_actualizacion: datetime
    fecha_aceptado: datetime | None = None


class PreferenciaNotificacionActualizacion(_Persistencia):
    push_habilitado: bool | None = None
    citas_creadas: bool | None = None
    citas_confirmadas: bool | None = None
    citas_reprogramadas: bool | None = None
    citas_canceladas: bool | None = None
    recordatorios: bool | None = None

    def cambios(self) -> dict[str, bool]:
        return {
            clave: valor
            for clave, valor in self.model_dump().items()
            if valor is not None
        }


class PreferenciaNotificacionVista(_Persistencia):
    id: int
    usuario_id: int
    push_habilitado: bool
    citas_creadas: bool
    citas_confirmadas: bool
    citas_reprogramadas: bool
    citas_canceladas: bool
    recordatorios: bool
    fecha_actualizacion: datetime


class CitaEventoPersistencia(_Persistencia):
    cita_id: int = Field(gt=0)
    tipo: TipoEventoCita
    estado_anterior: str | None = None
    estado_nuevo: str | None = None
    fecha_anterior: date | None = None
    hora_anterior: time | None = None
    fecha_nueva: date | None = None
    hora_nueva: time | None = None
    actor_usuario_id: int | None = Field(default=None, gt=0)
    clave_idempotencia: str = Field(min_length=1, max_length=150)

    @model_validator(mode="after")
    def _coherencia_evento(self) -> "CitaEventoPersistencia":
        if self.estado_anterior is not None and self.estado_anterior not in ESTADOS_CITA:
            raise ValueError("estado_anterior no es un estado de cita valido")
        if self.estado_nuevo is not None and self.estado_nuevo not in ESTADOS_CITA:
            raise ValueError("estado_nuevo no es un estado de cita valido")

        if self.tipo == "CITA_CREADA":
            if (
                self.estado_anterior is not None
                or self.fecha_anterior is not None
                or self.hora_anterior is not None
            ):
                raise ValueError(
                    "Un evento CITA_CREADA no registra estado ni agenda anterior"
                )
        elif self.tipo == "CITA_ESTADO_CAMBIADO":
            if self.estado_anterior is None or self.estado_nuevo is None:
                raise ValueError(
                    "Un cambio de estado exige estado_anterior y estado_nuevo"
                )
            if self.estado_anterior == self.estado_nuevo:
                raise ValueError(
                    "El cambio de estado debe modificar el estado de la cita"
                )
        elif self.tipo == "CITA_REPROGRAMADA":
            if None in (
                self.fecha_anterior,
                self.hora_anterior,
                self.fecha_nueva,
                self.hora_nueva,
            ):
                raise ValueError(
                    "Una reprogramacion exige fecha/hora anterior y nueva"
                )
            if (
                self.fecha_anterior == self.fecha_nueva
                and self.hora_anterior == self.hora_nueva
            ):
                raise ValueError(
                    "La reprogramacion debe cambiar la fecha u hora de agenda"
                )
        return self


class CitaEventoVista(_Persistencia):
    id: int
    cita_id: int
    tipo: TipoEventoCita
    estado_anterior: str | None = None
    estado_nuevo: str | None = None
    fecha_anterior: date | None = None
    hora_anterior: time | None = None
    fecha_nueva: date | None = None
    hora_nueva: time | None = None
    fecha_evento: datetime
    actor_usuario_id: int | None = None
    clave_idempotencia: str
