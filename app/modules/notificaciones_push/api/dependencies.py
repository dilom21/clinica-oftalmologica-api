"""Dependencias HTTP de la API movil de notificaciones push.

Puerta tenant (obligatoria):

* el JWT debe ser un token *tenant* firmado y vigente; los tokens legacy, los
  de SaaS y los manipulados se rechazan antes de abrir cualquier sesion;
* la sesion de datos es la que resuelve ``app.core.dependencies.get_db`` a
  partir del mismo token (jamas una base elegida por el cliente);
* el tenant se lee del contexto resuelto por el backend (``db.info``), no de
  claims enviados por el cliente ni de parametros HTTP;
* solo se admite el rol ``Paciente``.

No se modifica login, JWT, ``get_db`` ni RBAC: solo se reutilizan.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    get_db as get_tenant_db,
    get_tenant_claims,
    nombre_rol_actual,
)
from app.database import session as database_session
from app.modules.gestion_usuarios_seguridad.models.models import Usuario


ROL_PACIENTE = "paciente"


@dataclass(frozen=True, slots=True)
class ContextoPaciente:
    """Identidad y sesion confiables derivadas del JWT tenant validado."""

    usuario: Usuario
    db: Session
    tenant_database_id: int


def get_control_plane_db() -> Generator[Session, None, None]:
    """Sesion del Control Plane usando el factory existente del backend.

    No abre conexiones propias ni recibe credenciales: reutiliza
    ``open_control_session`` (el mismo mecanismo que resuelve tenants) y se
    puede sustituir en pruebas.
    """
    db = database_session.open_control_session()
    try:
        yield db
    finally:
        db.close()


def obtener_contexto_paciente(
    claims: dict = Depends(get_tenant_claims),
    db: Session = Depends(get_tenant_db),
) -> ContextoPaciente:
    usuario = db.get(Usuario, int(claims["sub"]))
    if usuario is None or not usuario.estado:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado o inactivo",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if usuario.rol_id != claims.get("rol_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no válido para recursos del paciente",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if nombre_rol_actual(usuario) != ROL_PACIENTE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere el rol Paciente",
        )

    contexto = db.info.get("tenant_context")
    tenant_database_id = getattr(contexto, "tenant_database_id", None)
    if (
        tenant_database_id is None
        or isinstance(tenant_database_id, bool)
        or not isinstance(tenant_database_id, int)
        or tenant_database_id <= 0
    ):
        # Fail closed: sin contexto tenant resuelto por el backend no se opera.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El contexto tenant no está disponible",
        )

    if claims.get("tenant_id") != tenant_database_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="El contexto tenant no coincide con el token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return ContextoPaciente(
        usuario=usuario,
        db=db,
        tenant_database_id=tenant_database_id,
    )
