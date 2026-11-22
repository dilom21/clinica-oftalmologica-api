import unicodedata

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import decodificar_access_token
from app.core.tenancy.registry_provider import get_tenant_engine_registry
from app.database import session as _session
from app.modules.gestion_usuarios_seguridad.models.models import (
    Accion,
    Funcion,
    Modulo,
    Rol,
    RolFuncion,
    Usuario,
)


bearer_scheme = HTTPBearer()


def get_db(
    request: Request,
    registry=Depends(get_tenant_engine_registry),
):
    """Tenant-aware session dependency shared by clinical routes and auth.

    It is intentionally a *distinct* callable from
    ``app.database.session.get_db``. The CU19 invariant requires the controles
    router, ``obtener_usuario_actual`` and ``requerir_permiso`` to resolve the
    exact same dependency object (exactly one session per request), while the
    routing implementation itself is reused from ``app.database.session``:

    * no token / legacy token -> original (legacy) database;
    * valid tenant token      -> the company's database resolved server-side;
    * SaaS admin token        -> rejected (never a clinical resource).
    """
    yield from _session.get_db(request, registry)


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


def get_tenant_claims(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Validate a *signed* tenant JWT and return its claims.

    Invalid signatures, legacy tokens and SaaS admin tokens are rejected:
    tenant identity is never taken from client-controlled input.
    """
    try:
        claims = decodificar_access_token(credentials.credentials)
        validate_tenant_claims(claims)
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return claims


def validate_tenant_claims(payload: dict) -> None:
    """Valida estructura y tipos sin confiar solo en la firma del JWT."""
    if payload.get("token_type") != "tenant" or payload.get("exp") is None:
        raise ValueError("token_type")

    subject = payload.get("sub")
    if (
        not isinstance(subject, str)
        or not subject.isdecimal()
        or int(subject) <= 0
    ):
        raise ValueError("sub")

    for claim in ("tenant_id", "empresa_id", "rol_id"):
        value = payload.get(claim)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(claim)

    empresa_codigo = payload.get("empresa_codigo")
    if not isinstance(empresa_codigo, str) or not empresa_codigo.strip():
        raise ValueError("empresa_codigo")

    rol_nombre = payload.get("rol_nombre")
    if rol_nombre is not None and (
        not isinstance(rol_nombre, str) or not rol_nombre.strip()
    ):
        raise ValueError("rol_nombre")


def obtener_usuario_actual(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Usuario:
    try:
        payload = decodificar_access_token(credentials.credentials)
        token_type = payload.get("token_type")
        if token_type == "saas_admin":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token SaaS no válido para rutas clínicas",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if token_type == "tenant":
            validate_tenant_claims(payload)
        elif token_type is not None or any(
            claim in payload
            for claim in ("tenant_id", "empresa_id", "empresa_codigo")
        ):
            raise ValueError("token_type")
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
        payload.get("token_type") == "tenant"
        and usuario.rol_id != payload["rol_id"]
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
    *,
    modulo: str | None = None,
):
    """Exige que el rol del usuario tenga asignada la función en `rol_funcion`.

    Si `nombre_accion` se omite se conserva la compatibilidad con los CU
    anteriores: basta con que exista `rol_funcion` con función y acción
    activas. Si se indica una acción mínima, la acción otorgada debe
    satisfacerla (LECTURA/ESCRITURA se satisfacen también con AMBAS).

    La función se resuelve siempre dentro de su módulo y solo si el rol y el
    módulo están activos:

    * ``modulo=`` solo debe usarse con nombres verificados en el catálogo. La
      resolución queda acotada a ese módulo y exige que exista exactamente una
      función con ese nombre.
    * Sin ``modulo=`` se aplica una guarda de unicidad global: si el nombre de
      la función existe en más de un registro del catálogo (aunque pertenezcan
      a módulos distintos) la petición se deniega con 403. Nunca se elige una
      fila arbitraria mediante ``LIMIT 1``.
    """
    acciones_suficientes = (
        _acciones_suficientes_para(nombre_accion)
        if nombre_accion is not None
        else None
    )
    modulo_esperado = (modulo or "").strip() or None
    if modulo is not None and modulo_esperado is None:
        raise ValueError("Módulo vacío")

    def validar_permiso(
        usuario: Usuario = Depends(obtener_usuario_actual),
        db: Session = Depends(get_db),
    ) -> Usuario:
        # Guarda de unicidad: el nombre debe resolver a una sola función del
        # catálogo. Con módulo explícito se acota a ese módulo; sin él se
        # exige unicidad global. Cualquier homónimo (incluido uno inactivo) es
        # una ambigüedad y se deniega.
        candidatos = (
            select(func.count(Funcion.id))
            .select_from(Funcion)
            .join(Modulo, Modulo.id == Funcion.modulo_id)
            .where(Funcion.nombre == nombre_funcion)
        )
        if modulo_esperado is not None:
            candidatos = candidatos.where(Modulo.nombre == modulo_esperado)

        consulta = (
            select(
                RolFuncion.id,
                Accion.nombre.label("accion_nombre"),
            )
            .join(Funcion, Funcion.id == RolFuncion.funcion_id)
            .join(Modulo, Modulo.id == Funcion.modulo_id)
            .join(Accion, Accion.id == RolFuncion.accion_id)
            .join(Rol, Rol.id == RolFuncion.rol_id)
            .where(
                RolFuncion.rol_id == usuario.rol_id,
                Funcion.nombre == nombre_funcion,
                Funcion.estado.is_(True),
                Modulo.estado.is_(True),
                Rol.estado.is_(True),
                Accion.estado.is_(True),
                candidatos.scalar_subquery() == 1,
            )
        )
        if modulo_esperado is not None:
            consulta = consulta.where(Modulo.nombre == modulo_esperado)

        fila = db.execute(consulta.limit(1)).first()

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
