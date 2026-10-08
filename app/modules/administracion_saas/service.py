import re
from datetime import datetime, timezone

import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import hash_password, verificar_password

from .repository import SaasRepository


def crear_saas_access_token(usuario_id: int) -> str:
    from datetime import timedelta
    exp = datetime.now(timezone.utc) + timedelta(minutes=30)
    return jwt.encode({"sub": str(usuario_id), "saas_usuario_id": usuario_id,
                       "token_type": "saas_admin", "exp": exp},
                      JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def autenticar(db: Session, correo: str, password: str, ip: str | None = None):
    repo = SaasRepository(db)
    user = repo.find_user(correo.strip().casefold())
    if not user or not user.estado or not verificar_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales SaaS inválidas",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        user.ultimo_acceso = datetime.now(timezone.utc)
        repo.log(user.id, "LOGIN_SAAS", "saas_usuario", user.id, "Inicio de sesión SaaS", ip)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="No se pudo registrar el inicio de sesión SaaS",
        ) from exc
    return {"access_token": crear_saas_access_token(user.id), "token_type": "bearer",
            "saas_usuario_id": user.id}


def sanitize_error(message: str | None) -> str | None:
    if message is None:
        return None
    value = re.sub(
        r"(?i)(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis)://\S+"
        r"|\b(?:database_url|host_alias|password|secret|token|api[_ ]?key|"
        r"connection[_ ]?string)\s*[:=]\s*\S+",
                   "[REDACTED]", str(message))
    return value.replace("\n", " ")[:500]


def cambiar_estado_empresa(db: Session, empresa_id: int, estado: str, user_id: int, ip=None):
    empresa = SaasRepository(db).get_empresa(empresa_id)
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    estado = estado.upper()
    if estado not in {"ACTIVA", "SUSPENDIDA", "PENDIENTE"}:
        raise HTTPException(status_code=422, detail="Estado de empresa inválido")
    empresa.estado = estado
    SaasRepository(db).log(user_id, "CAMBIAR_ESTADO_EMPRESA", "empresa", empresa_id,
                           f"Estado cambiado a {estado}", ip)
    db.commit()
    return empresa


def cambiar_estado_suscripcion(db: Session, suscripcion_id: int, estado: str, user_id: int, ip=None):
    repo = SaasRepository(db)
    suscripcion = repo.get_suscripcion(suscripcion_id)
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")
    estado = estado.upper()
    if estado not in {"PENDIENTE", "ACTIVA", "SUSPENDIDA", "VENCIDA", "CANCELADA"}:
        raise HTTPException(status_code=422, detail="Estado de suscripción inválido")
    suscripcion.estado = estado
    repo.log(user_id, "CAMBIAR_ESTADO_SUSCRIPCION", "suscripcion", suscripcion_id,
             f"Estado cambiado a {estado}", ip)
    db.commit()
    return suscripcion
