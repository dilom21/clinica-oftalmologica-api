"""Revocacion global de huellas FCM invalidadas (Fase 3C.5.2).

Flujo entre dos bases fisicas (sin transaccion ACID comun):

1. el **worker FCM** (sesion tenant) desactiva el dispositivo y registra una
   solicitud durable ``PENDIENTE`` en ``dispositivo_push_revocacion`` dentro de
   la misma transaccion del resultado de la entrega. **No** abre el Control
   Plane.
2. el **procesador** recibe dos sesiones ya resueltas por un proceso confiable:
   la de **una** base tenant y la del Control Plane, mas el
   ``tenant_database_id`` de esa base. Reclama un lote de solicitudes con
   lease/fencing, actualiza el Control Plane en su propia transaccion y solo
   despues confirma (ACK) la solicitud tenant.
3. si el proceso cae entre ambos commits, el reintento encuentra la fila
   global ya ``REVOCADO`` y termina de forma idempotente.

Garantias:

* el Control Plane se actualiza siempre por la pareja
  ``tenant_database_id + token_fingerprint``: una solicitud de un tenant no
  puede modificar la fila de otro;
* nunca se borra ni se reasigna la fila global ni la solicitud local;
* no se promete exactly-once: el procesamiento es idempotente y repetible;
* no se registran tokens, huellas, credenciales ni cuerpos crudos.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import (
    DispositivoPush,
    RevocacionPushGlobal,
)
from app.modules.notificaciones_push.repositories import (
    registro_global as repo_registro,
)
from app.modules.notificaciones_push.repositories import (
    revocaciones as repo_revocaciones,
)
from app.modules.notificaciones_push.seguridad import (
    TokenFcmInvalido,
    calcular_huella_token,
)

MAXIMO_LOTE = 100
MAXIMO_INTENTOS_LIMITE = 20
LEASE_MINIMO_SEGUNDOS = 5
LEASE_MAXIMO_SEGUNDOS = 3600
BACKOFF_MAXIMO_SEGUNDOS = 86400

CODIGO_CONFIRMADA = "REVOCACION_GLOBAL_CONFIRMADA"
CODIGO_YA_REVOCADA = "REVOCACION_GLOBAL_YA_CONFIRMADA"
CODIGO_NO_ENCONTRADA = "HUELLA_NO_ENCONTRADA"
CODIGO_OTRO_TENANT = "HUELLA_OTRO_TENANT"
CODIGO_CONTROL_NO_DISPONIBLE = "CONTROL_PLANE_NO_DISPONIBLE"
CODIGO_ESTADO_INESPERADO = "ESTADO_GLOBAL_INESPERADO"
CODIGO_MAXIMO_INTENTOS = "MAXIMO_INTENTOS_ALCANZADO"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConfiguracionRevocaciones:
    """Limites de lote, lease y reintentos."""

    tamano_lote: int = 20
    lease_segundos: int = 120
    max_intentos: int = 5
    backoff_base_segundos: int = 30
    backoff_maximo_segundos: int = 3600

    def __post_init__(self) -> None:
        if not 1 <= self.tamano_lote <= MAXIMO_LOTE:
            raise ValueError(f"tamano_lote debe estar entre 1 y {MAXIMO_LOTE}")
        if not LEASE_MINIMO_SEGUNDOS <= self.lease_segundos <= LEASE_MAXIMO_SEGUNDOS:
            raise ValueError("lease_segundos fuera de rango permitido")
        if not 1 <= self.max_intentos <= MAXIMO_INTENTOS_LIMITE:
            raise ValueError(
                f"max_intentos debe estar entre 1 y {MAXIMO_INTENTOS_LIMITE}"
            )
        if self.backoff_base_segundos <= 0:
            raise ValueError("backoff_base_segundos debe ser positivo")
        if not (
            self.backoff_base_segundos
            <= self.backoff_maximo_segundos
            <= BACKOFF_MAXIMO_SEGUNDOS
        ):
            raise ValueError("backoff_maximo_segundos fuera de rango")


@dataclass
class ResultadoRevocaciones:
    """Contadores de una invocacion. No contiene tokens ni huellas."""

    reclamadas: int = 0
    completadas: int = 0
    ya_revocadas: int = 0
    no_encontradas: int = 0
    rechazadas: int = 0
    reprogramadas: int = 0
    fallidas: int = 0
    conflicto_lease: int = 0
    error_control: bool = False


def calcular_backoff(
    intentos: int, configuracion: ConfiguracionRevocaciones,
) -> int:
    """Backoff exponencial acotado: base * 2^(intentos-1)."""
    exponente = max(1, min(intentos, 16)) - 1
    bruto = configuracion.backoff_base_segundos * (2 ** exponente)
    return max(1, min(bruto, configuracion.backoff_maximo_segundos))


# --------------------------------------------------------------------------- #
# Paso 1: solicitud durable creada por el worker (misma transaccion tenant)
# --------------------------------------------------------------------------- #
def registrar_solicitud_revocacion(
    db: Session,
    dispositivo: DispositivoPush | None,
    cuando: datetime,
) -> tuple[RevocacionPushGlobal | None, bool]:
    """Registra la solicitud de revocacion global del dispositivo.

    Debe invocarse dentro de la transaccion tenant del resultado del worker.
    **No hace commit.** Devuelve ``(solicitud, creada)``. Si no hay dispositivo
    o el token almacenado no produce una huella valida, devuelve
    ``(None, False)``: no existe propiedad global que revocar.
    """
    if dispositivo is None:
        return None, False
    dispositivo_id = getattr(dispositivo, "id", None)
    usuario_id = getattr(dispositivo, "usuario_id", None)
    token = getattr(dispositivo, "token_fcm", None)
    if (
        isinstance(dispositivo_id, bool)
        or not isinstance(dispositivo_id, int)
        or dispositivo_id <= 0
        or isinstance(usuario_id, bool)
        or not isinstance(usuario_id, int)
        or usuario_id <= 0
    ):
        return None, False
    try:
        huella = calcular_huella_token(token)  # type: ignore[arg-type]
    except TokenFcmInvalido:
        # Sin huella valida no hay registro global que revocar. Se conserva la
        # desactivacion local y no se inventa una solicitud.
        return None, False

    return repo_revocaciones.crear_si_no_existe(
        db,
        dispositivo_id=dispositivo_id,
        usuario_id=usuario_id,
        token_fingerprint=huella,
        cuando=cuando,
    )


# --------------------------------------------------------------------------- #
# Paso 2: procesador explicito con sesiones ya resueltas
# --------------------------------------------------------------------------- #
def _validar_tenant_database_id(valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ValueError(
            "tenant_database_id debe ser un entero positivo de contexto interno"
        )
    return valor


def _valores_retry(
    *,
    ahora: datetime,
    intentos: int,
    codigo: str,
    configuracion: ConfiguracionRevocaciones,
) -> dict:
    nuevo_intentos = intentos + 1
    if nuevo_intentos >= configuracion.max_intentos:
        return {
            "estado": "FALLIDA",
            "intentos": nuevo_intentos,
            "codigo_ultimo_error": CODIGO_MAXIMO_INTENTOS,
            "proximo_intento": None,
            "bloqueado_hasta": None,
            "lease_token": None,
            "fecha_actualizacion": ahora,
            "fecha_procesado": ahora,
        }
    return {
        "estado": "PENDIENTE",
        "intentos": nuevo_intentos,
        "codigo_ultimo_error": codigo,
        "proximo_intento": ahora + timedelta(
            seconds=calcular_backoff(nuevo_intentos, configuracion),
        ),
        "bloqueado_hasta": None,
        "lease_token": None,
        "fecha_actualizacion": ahora,
    }


def _valores_terminal(
    *,
    ahora: datetime,
    intentos: int,
    estado: str,
    codigo: str,
) -> dict:
    return {
        "estado": estado,
        "intentos": intentos,
        "codigo_ultimo_error": codigo,
        "proximo_intento": None,
        "bloqueado_hasta": None,
        "lease_token": None,
        "fecha_actualizacion": ahora,
        "fecha_procesado": ahora,
    }


def _confirmar_control_plane(
    control: Session,
    *,
    solicitud: RevocacionPushGlobal,
    tenant_database_id: int,
    ahora: datetime,
) -> tuple[str, str]:
    """Actualiza el Control Plane en su propia transaccion.

    Devuelve ``(resultado, codigo)`` donde resultado es ``COMPLETADA``,
    ``NO_ENCONTRADO``, ``RECHAZADA`` o ``REINTENTAR``. Confirma el ``commit``
    **antes** de que el llamador marque la solicitud local.
    """
    try:
        clasificacion = repo_registro.revocar_huella_acotada(
            control,
            token_fingerprint=solicitud.token_fingerprint,
            tenant_database_id=tenant_database_id,
            cuando=ahora,
        )
        if clasificacion.resultado == repo_registro.REVOCACION_APLICADA:
            control.commit()
            return "COMPLETADA", CODIGO_CONFIRMADA
        if clasificacion.resultado == repo_registro.REVOCACION_YA_REVOCADA:
            # Idempotencia: la fila ya estaba revocada; se conserva su fecha.
            control.commit()
            return "YA_REVOCADA", CODIGO_YA_REVOCADA
        if clasificacion.resultado == repo_registro.REVOCACION_NO_ENCONTRADA:
            control.rollback()
            return "NO_ENCONTRADO", CODIGO_NO_ENCONTRADA
        if clasificacion.resultado == repo_registro.REVOCACION_OTRO_TENANT:
            control.rollback()
            # Incidencia saneada: no se revela ni se toca la fila ajena.
            return "RECHAZADA", CODIGO_OTRO_TENANT
        control.rollback()
        return "REINTENTAR", CODIGO_ESTADO_INESPERADO
    except SQLAlchemyError:
        try:
            control.rollback()
        except Exception:  # pragma: no cover - proteccion defensiva
            pass
        return "REINTENTAR", CODIGO_CONTROL_NO_DISPONIBLE


def _procesar_una(
    db: Session,
    control: Session,
    *,
    solicitud_id: int,
    tenant_database_id: int,
    lease_token: str,
    configuracion: ConfiguracionRevocaciones,
    ahora: datetime,
    resultado: ResultadoRevocaciones,
) -> None:
    solicitud = repo_revocaciones.obtener_por_id(db, solicitud_id)
    if solicitud is None:
        return

    intentos_previos = int(solicitud.intentos or 0)
    clasificacion, codigo = _confirmar_control_plane(
        control,
        solicitud=solicitud,
        tenant_database_id=tenant_database_id,
        ahora=ahora,
    )

    if clasificacion in ("COMPLETADA", "YA_REVOCADA"):
        valores = _valores_terminal(
            ahora=ahora,
            intentos=intentos_previos,
            estado="COMPLETADA",
            codigo=codigo,
        )
        if repo_revocaciones.actualizar_resultado(
            db, solicitud_id, lease_token=lease_token, valores=valores,
        ):
            if clasificacion == "COMPLETADA":
                resultado.completadas += 1
            else:
                resultado.ya_revocadas += 1
        else:
            resultado.conflicto_lease += 1
        db.commit()
        return

    if clasificacion in ("NO_ENCONTRADO", "RECHAZADA"):
        valores = _valores_terminal(
            ahora=ahora,
            intentos=intentos_previos,
            estado=clasificacion,
            codigo=codigo,
        )
        if repo_revocaciones.actualizar_resultado(
            db, solicitud_id, lease_token=lease_token, valores=valores,
        ):
            if clasificacion == "NO_ENCONTRADO":
                resultado.no_encontradas += 1
            else:
                resultado.rechazadas += 1
        else:
            resultado.conflicto_lease += 1
        db.commit()
        return

    # REINTENTAR: el Control Plane no confirmo. Nunca se marca COMPLETADA.
    valores = _valores_retry(
        ahora=ahora,
        intentos=intentos_previos,
        codigo=codigo,
        configuracion=configuracion,
    )
    if repo_revocaciones.actualizar_resultado(
        db, solicitud_id, lease_token=lease_token, valores=valores,
    ):
        if valores["estado"] == "FALLIDA":
            resultado.fallidas += 1
        else:
            resultado.reprogramadas += 1
    else:
        resultado.conflicto_lease += 1
    db.commit()


def procesar_revocaciones(
    db: Session,
    control: Session,
    *,
    tenant_database_id: int,
    configuracion: ConfiguracionRevocaciones | None = None,
    reloj: Callable[[], datetime] | None = None,
) -> ResultadoRevocaciones:
    """Procesa un lote de solicitudes de UNA base tenant.

    ``db`` y ``control`` deben ser sesiones ya resueltas por un proceso
    confiable; ``tenant_database_id`` procede del contexto interno de esa base
    tenant. La funcion no descubre tenants, no enumera empresas y no acepta
    identificadores desde requests HTTP.
    """
    tenant_id = _validar_tenant_database_id(tenant_database_id)
    contexto = db.info.get("tenant_context")
    esperado = getattr(contexto, "tenant_database_id", None)
    if esperado is not None and esperado != tenant_id:
        # Fail closed: el identificador explicito debe coincidir con el
        # contexto tenant que resolvio el backend para esta sesion.
        raise ValueError(
            "tenant_database_id no coincide con el contexto tenant resuelto"
        )
    config = configuracion or ConfiguracionRevocaciones()
    ahora = (reloj or _ahora)()
    resultado = ResultadoRevocaciones()

    if control is None:
        # Sin Control Plane confiable no se reclama nada ni se finge exito.
        resultado.error_control = True
        return resultado

    lease_token = secrets.token_hex(16)
    lease_hasta = ahora + timedelta(seconds=config.lease_segundos)

    reclamadas = repo_revocaciones.reclamar_lote(
        db,
        ahora=ahora,
        lease_token=lease_token,
        lease_hasta=lease_hasta,
        limite=config.tamano_lote,
    )
    lote = [solicitud.id for solicitud in reclamadas]
    # El claim se confirma antes de tocar el Control Plane.
    db.commit()
    resultado.reclamadas = len(lote)

    for solicitud_id in lote:
        _procesar_una(
            db,
            control,
            solicitud_id=solicitud_id,
            tenant_database_id=tenant_id,
            lease_token=lease_token,
            configuracion=config,
            ahora=ahora,
            resultado=resultado,
        )

    return resultado


def registrar_y_confirmar(
    db: Session,
    control: Session,
    *,
    tenant_database_id: int,
    dispositivo: DispositivoPush | None,
    configuracion: ConfiguracionRevocaciones | None = None,
    reloj: Callable[[], datetime] | None = None,
) -> ResultadoRevocaciones:
    """Registra (si falta) y procesa la solicitud de un dispositivo.

    Utilidad para un proceso confiable que ya tiene ambas sesiones resueltas;
    no se expone por HTTP y no la invoca el worker FCM.
    """
    ahora = (reloj or _ahora)()
    registrar_solicitud_revocacion(db, dispositivo, ahora)
    db.commit()
    return procesar_revocaciones(
        db,
        control,
        tenant_database_id=tenant_database_id,
        configuracion=configuracion,
        reloj=reloj,
    )
