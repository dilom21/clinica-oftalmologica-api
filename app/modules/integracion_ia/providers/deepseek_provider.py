"""Encapsula el acceso a la API de DeepSeek (compatible con OpenAI).

Ninguna excepción del SDK debe escapar de este módulo: el service y el router
trabajan únicamente con las excepciones propias definidas aquí. Tampoco se
registra en logs la API key, cabeceras de autorización, el prompt clínico
completo ni la respuesta cruda del modelo.

Seguridad:
- La API key se lee de configuración (backend) y nunca se expone.
- La ausencia de clave produce `IAConfiguracionError`, que el service traduce
  a 503 "Servicio de IA no configurado".
- Los errores de red/rate limit/servicio producen
  `IAProveedorNoDisponibleError` (503).
- El contenido vacío o no válido produce `IARespuestaInvalidaError` (502).
"""

from __future__ import annotations

import json

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_TIMEOUT_SECONDS,
)


class IAConfiguracionError(Exception):
    """Falta configuración del servicio de IA (por ejemplo, la API key)."""


class IAProveedorNoDisponibleError(Exception):
    """El proveedor no respondió: timeout, conexión, rate limit o caída."""


class IARespuestaInvalidaError(Exception):
    """El proveedor respondió algo que no es JSON válido o utilizable."""


# Errores del SDK que se traducen a indisponibilidad temporal del proveedor.
_ERRORES_PROVEEDOR = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    AuthenticationError,
    APIStatusError,
    APIError,
)

# DeepSeek puede terminar con `finish_reason=length` y contenido vacío con
# 1024 tokens en respuestas JSON de reportes; 2048 permite completar el mismo
# contrato sin relajar el parsing, la validación Pydantic ni el registry.
MAX_TOKENS_POR_DEFECTO = 2048


def generar_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = MAX_TOKENS_POR_DEFECTO,
) -> dict:
    """Solicita al modelo una respuesta JSON y la devuelve ya parseada.

    No valida el esquema de negocio (eso corresponde al service con Pydantic),
    pero sí garantiza que el resultado es un objeto JSON no vacío. Si falta la
    API key lanza `IAConfiguracionError` sin crear ningún cliente ni tocar la
    red.
    """
    api_key = DEEPSEEK_API_KEY
    if not api_key:
        raise IAConfiguracionError("DEEPSEEK_API_KEY no está configurada")

    cliente = OpenAI(
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        timeout=DEEPSEEK_TIMEOUT_SECONDS,
    )

    try:
        respuesta = cliente.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except _ERRORES_PROVEEDOR as exc:
        # El detalle original del SDK puede contener datos sensibles: se
        # reemplaza por un mensaje genérico y se conserva la causa interna.
        raise IAProveedorNoDisponibleError(
            "El proveedor de IA no está disponible"
        ) from exc

    contenido = _extraer_contenido(respuesta)
    return _parsear_json(contenido)


def _extraer_contenido(respuesta) -> str:
    """Extrae el texto del mensaje del modelo o falla de forma controlada."""
    opciones = getattr(respuesta, "choices", None)
    if not opciones:
        raise IARespuestaInvalidaError("Respuesta vacía del proveedor de IA")

    mensaje = getattr(opciones[0], "message", None)
    contenido = getattr(mensaje, "content", None)

    if not isinstance(contenido, str) or not contenido.strip():
        raise IARespuestaInvalidaError("Respuesta vacía del proveedor de IA")

    return contenido


def _parsear_json(contenido: str) -> dict:
    """Convierte el string del modelo en un objeto JSON utilizable."""
    try:
        datos = json.loads(contenido)
    except (json.JSONDecodeError, TypeError) as exc:
        raise IARespuestaInvalidaError(
            "El proveedor no devolvió JSON válido"
        ) from exc

    if not isinstance(datos, dict):
        raise IARespuestaInvalidaError(
            "El proveedor no devolvió un objeto JSON"
        )

    return datos
