import unicodedata

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.database.session import get_db
from app.modules.gestion_usuarios_seguridad.models.models import (
    Accion,
    Funcion,
    RolFuncion,
    Usuario,
)


bearer_scheme = HTTPBearer()


def get_tenant_claims(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Validate only signed tenant JWTs; legacy tokens are rejected here."""
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
        validate_tenant_claims(payload)
        return payload
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tenant inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )


def validate_tenant_claims(payload: dict) -> None:
    if payload.get("token_type") != "tenant":
        raise ValueError("token type")
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdecimal() or int(subject) <= 0:
        raise ValueError("sub")
    for claim in ("tenant_id", "empresa_id", "rol_id"):
        value = payload.get(claim)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(claim)
    codigo = payload.get("empresa_codigo")
    if not isinstance(codigo, str) or not codigo.strip():
        raise ValueError("empresa_codigo")

# =========================================================
# ACCIONES
# Se resuelven por nombre contra la tabla `accion`.
# `AMBAS` satisface tanto LECTURA como ESCRITURA.
# =========================================================

ACCION_LECTURA = "LECTURA"
ACCION_ESCRITURA = "ESCRITURA"
ACCION_AMBAS = "AMBAS"

_ACCIONES_SUFICIENTES = {
    ACCION_LECTURA: {ACCION_LECTURA, ACCION_AMBAS},
    ACCION_ESCRITURA: {ACCION_ESCRITURA, ACCION_AMBAS},
    ACCION_AMBAS: {ACCION_AMBAS},
}


def _normalizar_accion(nombre: str) -> str:
    return (nombre or "").strip().upper()


def _acciones_suficientes_para(nombre_accion: str) -> set[str]:
    clave = _normalizar_accion(nombre_accion)
    acciones = _ACCIONES_SUFICIENTES.get(clave)
    if acciones is None:
        raise ValueError(f"Acción desconocida: {nombre_accion}")
    return acciones


def _normalizar_rol(nombre: str | None) -> str:
    """Normaliza un nombre de rol: minúsculas y sin tildes."""
    if not nombre:
        return ""
    normalizado = unicodedata.normalize("NFKD", str(nombre))
    sin_tildes = "".join(
        c for c in normalizado if not unicodedata.combining(c)
    )
    return sin_tildes.strip().lower()


def nombre_rol_actual(usuario: Usuario) -> str:
    """Nombre del rol del usuario normalizado (o cadena vacía si no tiene rol)."""
    rol = getattr(usuario, "rol", None)
    nombre = getattr(rol, "nombre", None) if rol is not None else None
    return _normalizar_rol(nombre)


def obtener_usuario_actual(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
        )
        if payload.get("token_type") == "saas_admin":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token SaaS no válido para rutas clínicas",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if payload.get("token_type") == "tenant":
            validate_tenant_claims(payload)
        elif payload.get("token_type") is not None or any(
            key in payload for key in ("tenant_id", "empresa_id", "empresa_codigo")
        ):
            raise ValueError("unexpected token type")
        usuario_id = int(payload["sub"])
    except HTTPException:
        raise
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    usuario = db.get(Usuario, usuario_id)
    if not usuario or not usuario.estado or (
        payload.get("token_type") == "tenant" and usuario.rol_id != payload["rol_id"]
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return usuario


def obtener_administrador_actual(
    usuario: Usuario = Depends(obtener_usuario_actual),
) -> Usuario:
    if nombre_rol_actual(usuario) not in {"admin", "administrador"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere un rol administrador",
        )

    return usuario


def requerir_permiso(
    nombre_funcion: str,
    nombre_accion: str | None = None,
):
    """Exige que el rol del usuario tenga asignada la función en `rol_funcion`.

    Si `nombre_accion` se omite se conserva la compatibilidad con los CU
    anteriores: basta con que exista `rol_funcion` con función y acción
    activas. Si se indica una acción mínima, la acción otorgada debe
    satisfacerla (LECTURA/ESCRITURA se satisfacen también con AMBAS).
    """
    acciones_suficientes = (
        _acciones_suficientes_para(nombre_accion)
        if nombre_accion is not None
        else None
    )

    def validar_permiso(
        usuario: Usuario = Depends(obtener_usuario_actual),
        db: Session = Depends(get_db),
    ) -> Usuario:
        fila = db.execute(
            select(
                RolFuncion.id,
                Accion.nombre.label("accion_nombre"),
            )
            .join(Funcion, Funcion.id == RolFuncion.funcion_id)
            .join(Accion, Accion.id == RolFuncion.accion_id)
            .where(
                RolFuncion.rol_id == usuario.rol_id,
                Funcion.nombre == nombre_funcion,
                Funcion.estado.is_(True),
                Accion.estado.is_(True),
            )
            .limit(1)
        ).first()

        if fila is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tiene permiso para {nombre_funcion}",
            )

        if acciones_suficientes is not None:
            accion_otorgada = _normalizar_accion(fila.accion_nombre)
            if accion_otorgada not in acciones_suficientes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"No tiene permiso de {nombre_accion} "
                        f"para {nombre_funcion}"
                    ),
                )

        return usuario

    return validar_permiso
