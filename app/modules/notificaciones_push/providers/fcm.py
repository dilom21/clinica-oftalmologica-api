"""Adaptador aislado de Firebase Cloud Messaging HTTP v1 (Fase 3C.5).

Reglas de seguridad de la fase:

* **no** se leen ``.env``, service-account JSON, DSN ni secretos: el access
  token OAuth 2.0 se obtiene de un ``ProveedorAccessToken`` inyectado por el
  entorno de despliegue;
* si no hay proyecto ni proveedor de credenciales el adaptador queda
  ``NO_CONFIGURADO``: reporta esa clasificacion y **nunca** simula una
  aceptacion del proveedor;
* no se registran tokens FCM, access tokens, cabeceras ``Authorization`` ni
  cuerpos crudos de respuesta;
* toda clasificacion expone solo codigos sanitizados (``[A-Z0-9_]``, maximo
  60 caracteres) y el ``provider_message_id`` saneado.

At-least-once: FCM no ofrece clave de idempotencia ni transaccion distribuida
con la base tenant. Si FCM acepta el mensaje y el proceso cae antes de
persistir ``proveedor_mensaje_id``, el reintento puede volver a enviarlo.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import google.auth
from google.auth.transport.requests import Request

from app.core import config as app_config

FCM_OAUTH_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_MARGEN_REFRESH = timedelta(seconds=60)

# --------------------------------------------------------------------------- #
# Clasificaciones de resultado
# --------------------------------------------------------------------------- #
ACEPTADO = "ACEPTADO"
TOKEN_NO_REGISTRADO = "TOKEN_NO_REGISTRADO"
REINTENTABLE = "REINTENTABLE"
PERMANENTE = "PERMANENTE"
ERROR_CONFIGURACION = "ERROR_CONFIGURACION"
NO_CONFIGURADO = "NO_CONFIGURADO"

CLASIFICACIONES = (
    ACEPTADO,
    TOKEN_NO_REGISTRADO,
    REINTENTABLE,
    PERMANENTE,
    ERROR_CONFIGURACION,
    NO_CONFIGURADO,
)

# Codigos estructurados de FCM que identifican inequivocamente un token que ya
# no existe. Cualquier otro 400/404 se considera rechazo permanente del
# payload y NUNCA invalida el dispositivo.
_CODIGOS_TOKEN_NO_REGISTRADO = frozenset({
    "UNREGISTERED",
    "INVALID_REGISTRATION",
    "SENDER_ID_MISMATCH",
})

_RE_CODIGO = re.compile(r"[^A-Z0-9_]+")
_RE_MENSAJE_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,255}$")
_LONGITUD_CODIGO = 60
_LONGITUD_ERROR = 100


@dataclass(frozen=True, slots=True)
class ResultadoFcm:
    """Resultado normalizado de un intento de envio."""

    clasificacion: str
    codigo: str | None = None
    mensaje_id: str | None = None
    reintentar_despues_segundos: int | None = None

    def __post_init__(self) -> None:
        if self.clasificacion not in CLASIFICACIONES:
            raise ValueError("Clasificacion FCM desconocida")

    @property
    def aceptado(self) -> bool:
        return self.clasificacion == ACEPTADO and bool(self.mensaje_id)

    @property
    def token_invalido(self) -> bool:
        return self.clasificacion == TOKEN_NO_REGISTRADO

    @property
    def reintentable(self) -> bool:
        return self.clasificacion == REINTENTABLE


class ProveedorAccessToken(Protocol):
    """Fuente segura del access token OAuth 2.0 (inyectada por despliegue)."""

    def access_token(self) -> str: ...


class FcmNoConfigurado(RuntimeError):
    """El adaptador no tiene proyecto o fuente de credenciales."""


class ProveedorAccessTokenAdc:
    """Proveedor OAuth 2.0 respaldado por Application Default Credentials.

    Conserva las credenciales en memoria y serializa el refresh para que varios
    hilos reutilicen el mismo access token de corta duracion. El token nunca se
    incluye en ``repr`` ni en excepciones emitidas por esta clase.
    """

    def __init__(
        self,
        credenciales: Any,
        *,
        request_factory: Any = Request,
        reloj: Any | None = None,
        margen_refresh: timedelta = _MARGEN_REFRESH,
    ) -> None:
        if credenciales is None:
            raise FcmNoConfigurado("ADC no esta disponible")
        self._credenciales = credenciales
        self._request_factory = request_factory
        self._reloj = reloj or (lambda: datetime.now(timezone.utc))
        self._margen_refresh = margen_refresh
        self._lock = threading.Lock()

    @classmethod
    def desde_entorno(cls) -> "ProveedorAccessTokenAdc":
        """Resuelve ADC con el unico scope requerido por FCM HTTP v1."""
        try:
            credenciales, _ = google.auth.default(scopes=[FCM_OAUTH_SCOPE])
        except Exception:  # noqa: BLE001 - nunca propagar material ADC
            raise FcmNoConfigurado("ADC no esta disponible") from None
        return cls(credenciales)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(credenciales=<ocultas>)"

    def _token_reutilizable(self) -> bool:
        token = getattr(self._credenciales, "token", None)
        if not isinstance(token, str) or not token.strip():
            return False
        expiracion = getattr(self._credenciales, "expiry", None)
        if not isinstance(expiracion, datetime):
            return bool(getattr(self._credenciales, "valid", False))
        if expiracion.tzinfo is None:
            expiracion = expiracion.replace(tzinfo=timezone.utc)
        return expiracion > self._reloj() + self._margen_refresh

    def access_token(self) -> str:
        if self._token_reutilizable():
            return self._credenciales.token
        with self._lock:
            if self._token_reutilizable():
                return self._credenciales.token
            try:
                self._credenciales.refresh(self._request_factory())
            except Exception:  # noqa: BLE001 - causa puede contener ADC
                raise FcmNoConfigurado(
                    "No se pudo obtener el access token ADC"
                ) from None
            token = getattr(self._credenciales, "token", None)
            if not isinstance(token, str) or not token.strip():
                raise FcmNoConfigurado("ADC no devolvio un access token valido")
            return token


# --------------------------------------------------------------------------- #
# Saneamiento
# --------------------------------------------------------------------------- #
def sanitizar_codigo(valor: object, por_defecto: str = "PROVEEDOR_ERROR") -> str:
    """Devuelve un codigo corto en mayusculas y sin caracteres arbitrarios."""
    if not isinstance(valor, str):
        return por_defecto
    limpio = _RE_CODIGO.sub("_", valor.strip().upper()).strip("_")
    if not limpio:
        return por_defecto
    return limpio[:_LONGITUD_CODIGO]


def sanitizar_codigo_error(valor: object, por_defecto: str = "PROVEEDOR_ERROR") -> str:
    """Codigo apto para ``codigo_ultimo_error`` (VARCHAR(100))."""
    return sanitizar_codigo(valor, por_defecto)[:_LONGITUD_ERROR]


def sanitizar_mensaje_id(valor: object) -> str | None:
    """Solo acepta identificadores opacos imprimibles y acotados."""
    if not isinstance(valor, str):
        return None
    limpio = valor.strip()
    if not _RE_MENSAJE_ID.fullmatch(limpio):
        return None
    return limpio


def _texto_error(respuesta: Any) -> dict:
    try:
        cuerpo = respuesta.json()
    except (ValueError, TypeError):
        return {}
    if not isinstance(cuerpo, dict):
        return {}
    error = cuerpo.get("error")
    return error if isinstance(error, dict) else {}


def _codigo_token_no_registrado(error: dict) -> str | None:
    """Extrae el ``errorCode`` estructurado de FCM, si existe."""
    detalles = error.get("details")
    if isinstance(detalles, list):
        for detalle in detalles:
            if not isinstance(detalle, dict):
                continue
            codigo = detalle.get("errorCode")
            if isinstance(codigo, str):
                normalizado = codigo.strip().upper()
                if normalizado in _CODIGOS_TOKEN_NO_REGISTRADO:
                    return normalizado
    # Algunas respuestas antiguas exponen el motivo en ``status``.
    estado = error.get("status")
    if isinstance(estado, str) and estado.strip().upper() in _CODIGOS_TOKEN_NO_REGISTRADO:
        return estado.strip().upper()
    return None


def _intentar_retry_after(respuesta: Any, maximo_segundos: int) -> int | None:
    cabeceras = getattr(respuesta, "headers", None)
    if not cabeceras:
        return None
    try:
        crudo = cabeceras.get("Retry-After")
    except AttributeError:
        return None
    if crudo is None:
        return None
    try:
        segundos = int(str(crudo).strip())
    except (TypeError, ValueError):
        return None
    if segundos <= 0:
        return None
    return min(segundos, maximo_segundos)


# --------------------------------------------------------------------------- #
# Adaptador FCM HTTP v1
# --------------------------------------------------------------------------- #
class FcmHttpV1:
    """Cliente de ``projects/{id}/messages:send`` sin dependencias nuevas."""

    ENDPOINT = "https://fcm.googleapis.com/v1/projects/{proyecto}/messages:send"

    def __init__(
        self,
        *,
        proyecto_id: str | None = None,
        credenciales: ProveedorAccessToken | None = None,
        cliente: Any | None = None,
        timeout_segundos: float = 10.0,
        max_retry_after_segundos: int = 3600,
    ) -> None:
        self._proyecto_id = proyecto_id.strip() if isinstance(proyecto_id, str) else None
        self._credenciales = credenciales
        self._cliente = cliente
        self._timeout = timeout_segundos
        self._max_retry_after = max_retry_after_segundos

    # ----------------------------------------------------------------- #
    # Configuracion
    # ----------------------------------------------------------------- #
    @property
    def configurado(self) -> bool:
        """Solo hay envio real cuando existen proyecto y fuente de token."""
        return bool(self._proyecto_id) and self._credenciales is not None

    def validar_configuracion(self) -> None:
        if not self.configurado:
            raise FcmNoConfigurado("FCM HTTP v1 no esta configurado")

    def _url(self) -> str:
        # El project ID no es secreto, pero se valida antes de interpolarlo.
        proyecto = self._proyecto_id or ""
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", proyecto):
            raise FcmNoConfigurado("Project ID de FCM invalido")
        url = self.ENDPOINT.format(proyecto=proyecto)
        if urlsplit(url).scheme != "https":
            raise FcmNoConfigurado("El endpoint de FCM debe ser HTTPS")
        return url

    # ----------------------------------------------------------------- #
    # Envio
    # ----------------------------------------------------------------- #
    def enviar(
        self,
        *,
        token: str,
        titulo: str,
        cuerpo: str,
        datos: dict[str, str] | None = None,
    ) -> ResultadoFcm:
        """Envia un mensaje y clasifica la respuesta. Nunca lanza por red."""
        if not self.configurado:
            return ResultadoFcm(NO_CONFIGURADO, "PROVEEDOR_NO_CONFIGURADO")

        try:
            url = self._url()
            access_token = self._credenciales.access_token()  # type: ignore[union-attr]
        except FcmNoConfigurado:
            return ResultadoFcm(NO_CONFIGURADO, "PROVEEDOR_NO_CONFIGURADO")
        except Exception:  # noqa: BLE001 - la causa no debe filtrar secretos
            return ResultadoFcm(
                ERROR_CONFIGURACION, "PROVEEDOR_SIN_CREDENCIALES",
            )

        if not isinstance(access_token, str) or not access_token.strip():
            return ResultadoFcm(
                ERROR_CONFIGURACION, "PROVEEDOR_SIN_CREDENCIALES",
            )

        carga = {
            "message": {
                "token": token,
                "notification": {"title": titulo, "body": cuerpo},
                "data": {str(clave): str(valor) for clave, valor in (datos or {}).items()},
            },
        }

        cliente = self._cliente or httpx.Client()
        cerrar = self._cliente is None
        try:
            respuesta = cliente.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=carga,
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            return ResultadoFcm(REINTENTABLE, "PROVEEDOR_TIMEOUT")
        except httpx.RequestError:
            return ResultadoFcm(REINTENTABLE, "PROVEEDOR_RED")
        finally:
            if cerrar:
                cliente.close()

        return self._clasificar(respuesta)

    def _clasificar(self, respuesta: Any) -> ResultadoFcm:
        estado_http = getattr(respuesta, "status_code", None)

        if isinstance(estado_http, int) and 200 <= estado_http < 300:
            cuerpo = {}
            try:
                cuerpo = respuesta.json()
            except (ValueError, TypeError):
                cuerpo = {}
            nombre = cuerpo.get("name") if isinstance(cuerpo, dict) else None
            mensaje_id = sanitizar_mensaje_id(nombre)
            if mensaje_id is None:
                # Hubo 2xx sin identificador verificable: no se puede afirmar
                # aceptacion. Reintentar puede duplicar (at-least-once).
                return ResultadoFcm(REINTENTABLE, "RESPUESTA_SIN_ID")
            return ResultadoFcm(ACEPTADO, "ACEPTADO", mensaje_id=mensaje_id)

        if estado_http == 429:
            espera = _intentar_retry_after(respuesta, self._max_retry_after)
            return ResultadoFcm(
                REINTENTABLE, "PROVEEDOR_RATE_LIMIT",
                reintentar_despues_segundos=espera,
            )

        if isinstance(estado_http, int) and 500 <= estado_http < 600:
            return ResultadoFcm(REINTENTABLE, "PROVEEDOR_NO_DISPONIBLE")

        if estado_http in (401, 403):
            # Falla global de credenciales/proyecto: jamas invalida un token.
            return ResultadoFcm(ERROR_CONFIGURACION, "PROVEEDOR_NO_AUTORIZADO")

        error = _texto_error(respuesta)
        if estado_http in (400, 404):
            codigo_token = _codigo_token_no_registrado(error)
            if codigo_token is not None:
                return ResultadoFcm(TOKEN_NO_REGISTRADO, codigo_token)

        if estado_http == 400:
            return ResultadoFcm(PERMANENTE, "PAYLOAD_INVALIDO")

        if estado_http == 404:
            return ResultadoFcm(PERMANENTE, "DESTINO_NO_ENCONTRADO")

        if isinstance(estado_http, int) and 400 <= estado_http < 500:
            return ResultadoFcm(PERMANENTE, "PROVEEDOR_RECHAZO")

        # Sin estado HTTP interpretable: resultado incierto, nunca aceptado.
        return ResultadoFcm(REINTENTABLE, "PROVEEDOR_RESPUESTA_INVALIDA")


def obtener_proveedor_fcm() -> FcmHttpV1:
    """Construye FCM con ADC o conserva ``NO_CONFIGURADO`` (fail-closed)."""
    proyecto_id = app_config.FCM_PROJECT_ID
    if not proyecto_id:
        return FcmHttpV1()
    try:
        credenciales = ProveedorAccessTokenAdc.desde_entorno()
    except FcmNoConfigurado:
        return FcmHttpV1()
    return FcmHttpV1(proyecto_id=proyecto_id, credenciales=credenciales)
