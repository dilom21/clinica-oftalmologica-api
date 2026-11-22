"""Repositorio del registro global de propiedad de huellas FCM.

Opera sobre la sesion del Control Plane que recibe desde la capa superior; no
abre conexiones ni resuelve tenants. Nunca guarda ni devuelve el token original:
solo la huella SHA-256 calculada por ``seguridad.calcular_huella_token``.

La reasignacion de un token entre empresas no es atomica entre dos bases: debera
implementarse en una fase posterior como claim/reserva + confirmacion o
compensacion (saga). Aqui solo se prepara la persistencia.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import (
    DispositivoPushRegistry,
)
from app.modules.notificaciones_push.seguridad import (
    TOKEN_MAX_LENGTH,
    TOKEN_MIN_LENGTH,
    calcular_huella_token,
    version_huella_actual,
)

# Resultados posibles de una revocacion global acotada (Fase 3C.5.2).
REVOCACION_APLICADA = "REVOCADA"
REVOCACION_YA_REVOCADA = "YA_REVOCADA"
REVOCACION_NO_ENCONTRADA = "NO_ENCONTRADA"
REVOCACION_OTRO_TENANT = "OTRO_TENANT"
REVOCACION_ESTADO_INESPERADO = "ESTADO_INESPERADO"


@dataclass(frozen=True)
class ResultadoRevocacionGlobal:
    """Clasificacion segura de la revocacion en el Control Plane."""

    resultado: str
    estado_actual: str | None = None


def obtener_por_huella(
    db: Session, token_fingerprint: str,
) -> DispositivoPushRegistry | None:
    return db.scalar(
        select(DispositivoPushRegistry).where(
            DispositivoPushRegistry.token_fingerprint == token_fingerprint,
        )
    )


def listar_por_tenant(
    db: Session, tenant_database_id: int,
) -> list[DispositivoPushRegistry]:
    return list(db.scalars(
        select(DispositivoPushRegistry)
        .where(DispositivoPushRegistry.tenant_database_id == tenant_database_id)
        .order_by(DispositivoPushRegistry.id)
    ))


def registrar_propiedad(
    db: Session,
    *,
    token_fcm: str,
    tenant_database_id: int,
    usuario_id: int,
) -> DispositivoPushRegistry:
    """Registra la huella para un tenant/usuario local.

    Si la huella ya existe se devuelve el registro actual; decidir si se
    reclama, reasigna o rechaza pertenece al futuro servicio transaccional.
    """
    huella = calcular_huella_token(token_fcm)
    existente = obtener_por_huella(db, huella)
    if existente is not None:
        return existente
    registro = DispositivoPushRegistry(
        token_fingerprint=huella,
        tenant_database_id=tenant_database_id,
        usuario_id=usuario_id,
        estado="ACTIVO",
        huella_version=version_huella_actual(),
    )
    db.add(registro)
    db.flush()
    return registro


def crear_pendiente(
    db: Session,
    *,
    token_fingerprint: str,
    tenant_database_id: int,
    usuario_id: int,
) -> DispositivoPushRegistry:
    """Reserva una huella en estado ``PENDIENTE`` (fase 1 del protocolo).

    No confirma la propiedad: el servicio la marca ``ACTIVO`` solo despues de
    persistir el dispositivo en la base tenant. No hace ``commit``.
    """
    registro = DispositivoPushRegistry(
        token_fingerprint=token_fingerprint,
        tenant_database_id=tenant_database_id,
        usuario_id=usuario_id,
        estado="PENDIENTE",
        huella_version=version_huella_actual(),
    )
    db.add(registro)
    db.flush()
    return registro


def marcar_pendiente(
    db: Session, registro: DispositivoPushRegistry, cuando: datetime,
) -> DispositivoPushRegistry:
    """Reabre una reclamacion propia previamente revocada."""
    registro.estado = "PENDIENTE"
    registro.fecha_revocacion = None
    registro.fecha_actualizacion = cuando
    db.flush()
    return registro


def marcar_activo(
    db: Session, registro: DispositivoPushRegistry, cuando: datetime,
) -> DispositivoPushRegistry:
    registro.estado = "ACTIVO"
    registro.fecha_revocacion = None
    registro.fecha_actualizacion = cuando
    db.flush()
    return registro


def marcar_revocado(
    db: Session, registro: DispositivoPushRegistry, cuando: datetime,
) -> DispositivoPushRegistry:
    registro.estado = "REVOCADO"
    registro.fecha_revocacion = cuando
    registro.fecha_actualizacion = cuando
    db.flush()
    return registro


def revocar(
    db: Session,
    registro: DispositivoPushRegistry,
    cuando: datetime,
) -> DispositivoPushRegistry:
    registro.estado = "REVOCADO"
    registro.fecha_revocacion = cuando
    registro.fecha_actualizacion = cuando
    db.flush()
    return registro


def limites_token() -> tuple[int, int]:
    return TOKEN_MIN_LENGTH, TOKEN_MAX_LENGTH


# --------------------------------------------------------------------------- #
# Revocacion global acotada (Fase 3C.5.2)
# --------------------------------------------------------------------------- #
def revocar_huella_acotada(
    db: Session,
    *,
    token_fingerprint: str,
    tenant_database_id: int,
    cuando: datetime,
) -> ResultadoRevocacionGlobal:
    """Revoca una huella solo si pertenece al tenant indicado.

    La actualizacion es un unico ``UPDATE`` condicionado por
    ``token_fingerprint`` **y** ``tenant_database_id``: aunque la huella sea
    unica globalmente, nunca se modifica una fila de otro tenant.

    - fila ``PENDIENTE``/``ACTIVO`` del mismo tenant -> ``REVOCADA``;
    - fila ya ``REVOCADO`` del mismo tenant -> ``YA_REVOCADA`` (idempotente,
      conserva ``fecha_revocacion`` original);
    - fila inexistente -> ``NO_ENCONTRADA`` (nunca se inserta ni se busca en
      otros tenants);
    - huella de otro tenant -> ``OTRO_TENANT`` sin modificar la fila;
    - estado desconocido -> ``ESTADO_INESPERADO`` sin modificar.

    No hace ``commit``: la transaccion del Control Plane pertenece al proceso
    que invoca esta funcion.
    """
    aplicado = db.execute(
        update(DispositivoPushRegistry)
        .where(
            DispositivoPushRegistry.token_fingerprint == token_fingerprint,
            DispositivoPushRegistry.tenant_database_id == tenant_database_id,
            DispositivoPushRegistry.estado.in_(("PENDIENTE", "ACTIVO")),
        )
        .values(
            estado="REVOCADO",
            fecha_revocacion=cuando,
            fecha_actualizacion=cuando,
        )
        .execution_options(synchronize_session=False)
    )
    if aplicado.rowcount == 1:
        return ResultadoRevocacionGlobal(REVOCACION_APLICADA, "REVOCADO")

    # Clasificacion de solo lectura cuando no hubo actualizacion.
    db.expire_all()
    registro = obtener_por_huella(db, token_fingerprint)
    if registro is None:
        return ResultadoRevocacionGlobal(REVOCACION_NO_ENCONTRADA)
    if registro.tenant_database_id != tenant_database_id:
        # No se revela ni se toca la fila ajena.
        return ResultadoRevocacionGlobal(
            REVOCACION_OTRO_TENANT, registro.estado,
        )
    if registro.estado == "REVOCADO":
        return ResultadoRevocacionGlobal(
            REVOCACION_YA_REVOCADA, registro.estado,
        )
    return ResultadoRevocacionGlobal(
        REVOCACION_ESTADO_INESPERADO, registro.estado,
    )
