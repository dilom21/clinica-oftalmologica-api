"""Huella determinista de tokens FCM para el Control Plane.

El Control Plane nunca guarda el token original. Solo persiste
``sha256`` en minusculas, que permite detectar que un mismo token intenta
quedar activo en dos empresas distintas sin revelar el secreto.

La huella no debe exponerse en respuestas HTTP ni en logs; tampoco se imprime
el token en los mensajes de error de este modulo.
"""

from __future__ import annotations

import hashlib

HUELLA_VERSION = 1
TOKEN_MIN_LENGTH = 20
TOKEN_MAX_LENGTH = 1024


class TokenFcmInvalido(ValueError):
    """El token no cumple el formato minimo esperado."""


def calcular_huella_token(token_fcm: str) -> str:
    """Devuelve la huella SHA-256 hexadecimal (64 caracteres) del token.

    Es determinista y no usa sal: la misma entrada produce la misma huella en
    cualquier tenant. El token no se registra en ningun mensaje.
    """
    if not isinstance(token_fcm, str):
        raise TokenFcmInvalido("El token FCM debe ser texto")
    token = token_fcm.strip()
    if len(token) < TOKEN_MIN_LENGTH or len(token) > TOKEN_MAX_LENGTH:
        raise TokenFcmInvalido("El token FCM tiene una longitud invalida")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def version_huella_actual() -> int:
    return HUELLA_VERSION
