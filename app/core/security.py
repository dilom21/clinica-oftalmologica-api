from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import (
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
)

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verificar_password(password: str, password_guardada: str) -> bool:
    return password_hash.verify(password, password_guardada)


def crear_access_token(usuario_id: int, rol_id: int) -> str:
    fecha_expiracion = datetime.now(timezone.utc) + timedelta(
        minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(usuario_id),
        "rol_id": rol_id,
        "exp": fecha_expiracion,
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def crear_tenant_access_token(
    usuario_id: int,
    rol_id: int,
    tenant_id: int,
    empresa_id: int,
    empresa_codigo: str,
) -> str:
    """Emite un JWT de tenant con identidad firmada.

    Los claims solo contienen referencias lógicas (empresa, suscripción) nunca
    credenciales ni nombres físicos de bases de datos, de forma que la
    resolución de la conexión ocurra siempre en el servidor.
    """
    fecha_expiracion = datetime.now(timezone.utc) + timedelta(
        minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(usuario_id),
        "rol_id": rol_id,
        "tenant_id": tenant_id,
        "empresa_id": empresa_id,
        "empresa_codigo": empresa_codigo,
        "token_type": "tenant",
        "exp": fecha_expiracion,
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def crear_saas_access_token(usuario_id: int) -> str:
    """JWT del plano de control SaaS, separado del JWT clínico o de tenant."""
    fecha_expiracion = datetime.now(timezone.utc) + timedelta(
        minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(usuario_id),
        "saas_usuario_id": usuario_id,
        "token_type": "saas_admin",
        "exp": fecha_expiracion,
    }

    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
