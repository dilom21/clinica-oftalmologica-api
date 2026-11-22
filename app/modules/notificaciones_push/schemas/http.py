"""Contratos HTTP de la API movil de notificaciones push (Fase 3C.3).

Reglas de privacidad:

* ningun schema de salida contiene ``token_fcm``, huellas ni IDs de otros
  usuarios;
* ``datos`` solo se publica a traves de una allowlist de claves de navegacion
  no sensibles (``filtrar_datos_navegacion``);
* ``token_fcm`` se declara con ``repr=False`` y sin restricciones de longitud
  en Pydantic: los limites se validan en el servicio para que un 422 no
  devuelva el valor recibido en el cuerpo del error;
* los booleanos de preferencias son estrictos: no se aceptan ``"true"``, ``1``
  ni equivalentes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


PlataformaPush = Literal["ANDROID", "IOS", "WEB"]

# Claves de navegacion admitidas en ``notificacion.datos``. Cualquier otra
# clave (incluido texto clinico) se descarta antes de responder.
CLAVES_DATOS_PERMITIDAS = frozenset({
    "ruta",
    "pantalla",
    "seccion",
    "accion",
    "cita_id",
})

_LONGITUD_MAXIMA_VALOR_NAVEGACION = 200


class _Http(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistroDispositivoRequest(_Http):
    """Alta/actualizacion de un dispositivo del paciente autenticado.

    El cliente nunca envia ``usuario_id``, ``tenant_id``, ``empresa_id``,
    ``empresa_codigo``, ``database_name``, DSN, huella ni estado: el backend
    los deriva del JWT tenant y de la sesion resuelta. ``extra="forbid"``
    rechaza cualquier intento de incluirlos.
    """

    token_fcm: str = Field(
        repr=False,
        description=(
            "Token FCM opaco. Se valida en el backend (20-1024 caracteres) y "
            "nunca se devuelve, registra ni refleja en errores."
        ),
    )
    plataforma: PlataformaPush
    instalacion_id: str | None = Field(default=None, max_length=100)
    app_version: str | None = Field(default=None, max_length=50)
    nombre_dispositivo: str | None = Field(default=None, max_length=120)


class DispositivoRespuesta(_Http):
    id: int
    plataforma: PlataformaPush
    activo: bool
    fecha_registro: datetime
    fecha_actualizacion: datetime


class NotificacionBandejaItem(_Http):
    id: int
    tipo: str
    titulo: str
    cuerpo: str
    datos: dict | None = None
    cita_id: int | None = None
    leida: bool
    fecha_lectura: datetime | None = None
    fecha_creacion: datetime


class BandejaRespuesta(_Http):
    items: list[NotificacionBandejaItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class NotificacionLeidaRespuesta(_Http):
    id: int
    leida: bool
    fecha_lectura: datetime


class PreferenciasRespuesta(_Http):
    push_habilitado: bool
    citas_creadas: bool
    citas_confirmadas: bool
    citas_reprogramadas: bool
    citas_canceladas: bool
    recordatorios: bool
    fecha_actualizacion: datetime | None = None


class PreferenciasActualizacionRequest(_Http):
    """Solo campos de preferencia. Todo campo extra es rechazado."""

    push_habilitado: StrictBool | None = None
    citas_creadas: StrictBool | None = None
    citas_confirmadas: StrictBool | None = None
    citas_reprogramadas: StrictBool | None = None
    citas_canceladas: StrictBool | None = None
    recordatorios: StrictBool | None = None

    def cambios(self) -> dict[str, bool]:
        return {
            clave: valor
            for clave, valor in self.model_dump().items()
            if valor is not None
        }


def filtrar_datos_navegacion(datos: object) -> dict | None:
    """Devuelve solo claves de navegacion permitidas con valores escalares.

    No se serializa el JSON almacenado de forma arbitraria: se descartan
    diccionarios/listas anidadas y cualquier clave desconocida.
    """
    if not isinstance(datos, dict):
        return None

    limpio: dict[str, object] = {}
    for clave, valor in datos.items():
        if not isinstance(clave, str):
            continue
        nombre = clave.strip().lower()
        if nombre not in CLAVES_DATOS_PERMITIDAS:
            continue
        if isinstance(valor, bool) or isinstance(valor, int):
            limpio[nombre] = valor
        elif isinstance(valor, str):
            texto = valor.strip()
            if texto and len(texto) <= _LONGITUD_MAXIMA_VALOR_NAVEGACION:
                limpio[nombre] = texto
        elif valor is None:
            continue
        else:
            continue
    return limpio or None
