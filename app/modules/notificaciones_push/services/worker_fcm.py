"""Worker tenant-scoped de envio push por FCM HTTP v1 (Fase 3C.5).

El worker procesa **una sola sesion de base tenant ya resuelta** por el proceso
confiable que lo invoca. No abre conexiones, no descubre bases, no acepta
``tenant_id``/``empresa_id``/DSN desde payloads, no enumera empresas y no se
arranca desde ``app.main``. No existe endpoint HTTP para ejecutarlo ni
scheduler/cron en esta fase: el despliegue/orquestacion queda pendiente.

Ciclo de una invocacion:

1. reclamar un lote acotado (``PENDIENTE`` vencida o ``PROCESANDO`` con lease
   expirado) y confirmar el claim con ``commit`` antes de cualquier red;
2. revalidar preferencia push, estado de la notificacion y propiedad/actividad
   del dispositivo;
3. enviar a FCM **fuera** de la transaccion de claim;
4. persistir el resultado en otra transaccion condicionada al ``lease_token``
   vigente: un worker con lease vencido no puede sobrescribir a otro.

Semantica **at-least-once**: si FCM acepta el mensaje y el proceso cae antes de
guardar ``proveedor_mensaje_id``, el reintento puede duplicar el envio. Los
leases y la unicidad ``(notificacion_id, dispositivo_id)`` reducen duplicados,
pero no existe exactly-once.

El modulo no imprime ni registra tokens FCM, huellas, access tokens ni cuerpos
crudos del proveedor.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_pacientes.models.models import Paciente
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.notificaciones_push.models.models import (
    DispositivoPush,
    Notificacion,
)
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.repositories import (
    dispositivos as repo_dispositivos,
)
from app.modules.notificaciones_push.repositories import (
    entregas as repo_entregas,
)
from app.modules.notificaciones_push.repositories import (
    preferencias as repo_preferencias,
)
from app.modules.notificaciones_push.schemas.http import filtrar_datos_navegacion
from app.modules.notificaciones_push.services.preferencias import push_permitido
from app.modules.notificaciones_push.services.revocaciones import (
    registrar_solicitud_revocacion,
)
from app.modules.notificaciones_push.services.recordatorios import (
    huella_horario,
    interpretar_clave_recordatorio,
)

MAXIMO_LOTE = 100
MAXIMO_INTENTOS_LIMITE = 20
LEASE_MINIMO_SEGUNDOS = 5
LEASE_MAXIMO_SEGUNDOS = 3600
BACKOFF_MAXIMO_SEGUNDOS = 86400

CODIGO_MAXIMO_INTENTOS = "MAXIMO_INTENTOS_ALCANZADO"
CODIGO_NOTIFICACION_CANCELADA = "NOTIFICACION_CANCELADA"
CODIGO_DISPOSITIVO_INACTIVO = "DISPOSITIVO_INACTIVO"
CODIGO_DISPOSITIVO_AJENO = "DISPOSITIVO_NO_ELEGIBLE"
CODIGO_PREFERENCIA = "PREFERENCIA_PUSH_DESHABILITADA"
CODIGO_SIN_TOKEN = "TOKEN_AUSENTE"
CODIGO_ID_DUPLICADO = "IDENTIFICADOR_DUPLICADO"
CODIGO_NOTIFICACION_AUSENTE = "NOTIFICACION_NO_DISPONIBLE"
CODIGO_RECORDATORIO_OBSOLETO = "RECORDATORIO_OBSOLETO"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConfiguracionWorker:
    """Limites coherentes de lote, lease y proveedor."""

    tamano_lote: int = 20
    lease_segundos: int = 120
    max_intentos: int = 5
    backoff_base_segundos: int = 30
    backoff_maximo_segundos: int = 3600
    timeout_proveedor_segundos: float = 10.0

    def __post_init__(self) -> None:
        if not 1 <= self.tamano_lote <= MAXIMO_LOTE:
            raise ValueError(f"tamano_lote debe estar entre 1 y {MAXIMO_LOTE}")
        if not LEASE_MINIMO_SEGUNDOS <= self.lease_segundos <= LEASE_MAXIMO_SEGUNDOS:
            raise ValueError(
                "lease_segundos fuera de rango permitido"
            )
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
        if not 0.1 <= self.timeout_proveedor_segundos < self.lease_segundos:
            # El lease debe sobrevivir al timeout del proveedor.
            raise ValueError("timeout del proveedor incoherente con el lease")


@dataclass
class ResultadoWorker:
    """Contadores de una invocacion. No contiene tokens ni IDs sensibles."""

    reclamadas: int = 0
    aceptadas: int = 0
    fallidas: int = 0
    invalidadas: int = 0
    omitidas: int = 0
    reprogramadas: int = 0
    liberadas: int = 0
    conflicto_lease: int = 0
    revocaciones_solicitadas: int = 0
    proveedor_no_configurado: bool = False
    error_configuracion: bool = False


def calcular_backoff(intentos: int, configuracion: ConfiguracionWorker) -> int:
    """Backoff exponencial acotado: base * 2^(intentos-1)."""
    exponente = max(1, min(intentos, 16)) - 1
    bruto = configuracion.backoff_base_segundos * (2 ** exponente)
    return max(1, min(bruto, configuracion.backoff_maximo_segundos))


def _datos_navegacion(notificacion: Notificacion) -> dict[str, str]:
    """Solo claves de navegacion de la allowlist, como strings."""
    limpio = filtrar_datos_navegacion(
        notificacion.datos if isinstance(notificacion.datos, dict) else None
    )
    if not limpio:
        return {}
    return {clave: str(valor) for clave, valor in limpio.items()}


def _valores_entrega(
    *,
    ahora: datetime,
    intentos: int,
    estado: str,
    codigo: str | None = None,
    mensaje_id: str | None = None,
    proximo_intento: datetime | None = None,
) -> dict:
    """Campos de cierre de la entrega; siempre libera el lease."""
    return {
        "estado": estado,
        "intentos": intentos,
        "codigo_ultimo_error": codigo,
        "proveedor_mensaje_id": mensaje_id,
        "proximo_intento": proximo_intento,
        "bloqueado_hasta": None,
        "lease_token": None,
        "fecha_actualizacion": ahora,
        "fecha_aceptado": ahora if estado == "ACEPTADO_POR_FCM" else None,
    }


def _persistir(
    db: Session,
    *,
    entrega_id: int,
    lease_token: str,
    valores: dict,
) -> bool:
    """Guarda el resultado condicionado al lease y confirma la transaccion."""
    aplicado = repo_entregas.actualizar_resultado(
        db, entrega_id, lease_token=lease_token, valores=valores,
    )
    db.commit()
    return aplicado


def _procesar_una(
    db: Session,
    *,
    entrega_id: int,
    notificacion_id: int,
    dispositivo_id: int,
    lease_token: str,
    proveedor,
    configuracion: ConfiguracionWorker,
    ahora: datetime,
    resultado: ResultadoWorker,
) -> bool:
    """Procesa una entrega reclamada. Devuelve ``False`` si debe detenerse."""
    entrega = repo_entregas.obtener_por_id(db, entrega_id)
    if entrega is None:
        return True
    intentos_previos = int(entrega.intentos or 0)

    notificacion = db.get(Notificacion, notificacion_id)
    dispositivo = db.get(DispositivoPush, dispositivo_id)
    preferencia = (
        repo_preferencias.obtener_por_usuario(db, notificacion.usuario_id)
        if notificacion is not None else None
    )

    # Copia escalar antes de cerrar la transaccion: despues del ``commit`` los
    # objetos expiran y acceder a ellos abriria otra transaccion durante la
    # llamada de red.
    contexto = {
        "notificacion_existe": notificacion is not None,
        "usuario_notificacion": (
            notificacion.usuario_id if notificacion is not None else None
        ),
        "tipo": notificacion.tipo if notificacion is not None else None,
        "titulo": notificacion.titulo if notificacion is not None else None,
        "cuerpo": notificacion.cuerpo if notificacion is not None else None,
        "datos": (
            _datos_navegacion(notificacion) if notificacion is not None else {}
        ),
        "cancelada": (
            notificacion.cancelada_en is not None
            if notificacion is not None else False
        ),
        "dispositivo_existe": dispositivo is not None,
        "dispositivo_activo": (
            bool(dispositivo.activo) if dispositivo is not None else False
        ),
        "dispositivo_usuario": (
            dispositivo.usuario_id if dispositivo is not None else None
        ),
        "token": dispositivo.token_fcm if dispositivo is not None else None,
        "push_permitido": push_permitido(preferencia, (
            notificacion.tipo if notificacion is not None else None
        )),
    }
    if notificacion is not None and notificacion.tipo == "RECORDATORIO_CITA":
        clave = interpretar_clave_recordatorio(notificacion.evento_clave)
        cita = db.get(Cita, notificacion.cita_id) if notificacion.cita_id else None
        paciente = db.get(Paciente, cita.paciente_id) if cita is not None else None
        usuario = db.get(Usuario, paciente.usuario_id) if paciente is not None and paciente.usuario_id else None
        contexto["recordatorio_vigente"] = bool(
            clave is not None
            and cita is not None
            and clave[0] == cita.id
            and clave[2] == huella_horario(cita)
            and cita.estado in ("PROGRAMADA", "CONFIRMADA")
            and paciente is not None and bool(paciente.estado)
            and usuario is not None and bool(usuario.estado)
            and usuario.id == notificacion.usuario_id
        )
    else:
        contexto["recordatorio_vigente"] = True
    # Cierra la lectura antes de la llamada de red: FCM nunca corre dentro de
    # una transaccion de base.
    db.commit()

    def cerrar(estado: str, **extra) -> None:
        valores = _valores_entrega(
            ahora=ahora,
            intentos=intentos_previos + 1,
            estado=estado,
            **extra,
        )
        if _persistir(
            db, entrega_id=entrega_id, lease_token=lease_token, valores=valores,
        ):
            if estado == "ACEPTADO_POR_FCM":
                resultado.aceptadas += 1
            elif estado == "INVALIDO":
                resultado.invalidadas += 1
            elif estado == "OMITIDO":
                resultado.omitidas += 1
            elif estado == "FALLIDO":
                resultado.fallidas += 1
            # PENDIENTE (reintento programado) se contabiliza por separado.
        else:
            resultado.conflicto_lease += 1

    if not contexto["notificacion_existe"]:
        cerrar("FALLIDO", codigo=CODIGO_NOTIFICACION_AUSENTE)
        return True

    if contexto["cancelada"]:
        cerrar("OMITIDO", codigo=CODIGO_NOTIFICACION_CANCELADA)
        return True

    if not contexto["recordatorio_vigente"]:
        cerrar("OMITIDO", codigo=CODIGO_RECORDATORIO_OBSOLETO)
        return True

    if not contexto["dispositivo_existe"] or not contexto["dispositivo_activo"]:
        cerrar("OMITIDO", codigo=CODIGO_DISPOSITIVO_INACTIVO)
        return True

    if contexto["dispositivo_usuario"] != contexto["usuario_notificacion"]:
        cerrar("OMITIDO", codigo=CODIGO_DISPOSITIVO_AJENO)
        return True

    if not contexto["push_permitido"]:
        cerrar("OMITIDO", codigo=CODIGO_PREFERENCIA)
        return True

    if intentos_previos >= configuracion.max_intentos:
        cerrar("FALLIDO", codigo=CODIGO_MAXIMO_INTENTOS)
        return True

    token = contexto["token"]
    if not isinstance(token, str) or not token.strip():
        cerrar("FALLIDO", codigo=CODIGO_SIN_TOKEN)
        return True

    envio = proveedor.enviar(
        token=token,
        titulo=contexto["titulo"],
        cuerpo=contexto["cuerpo"],
        datos=contexto["datos"],
    )

    if envio.clasificacion == fcm.ACEPTADO and envio.aceptado:
        try:
            cerrar(
                "ACEPTADO_POR_FCM",
                mensaje_id=envio.mensaje_id,
                codigo=None,
            )
        except IntegrityError:
            # ``provider_mensaje_id`` duplicado: no se puede registrar dos
            # veces el mismo mensaje del proveedor.
            db.rollback()
            cerrar("FALLIDO", codigo=CODIGO_ID_DUPLICADO)
        return True

    if envio.clasificacion == fcm.TOKEN_NO_REGISTRADO:
        codigo = fcm.sanitizar_codigo_error(envio.codigo, "TOKEN_NO_REGISTRADO")
        valores = _valores_entrega(
            ahora=ahora,
            intentos=intentos_previos + 1,
            estado="INVALIDO",
            codigo=codigo,
        )
        aplicado = repo_entregas.actualizar_resultado(
            db, entrega_id, lease_token=lease_token, valores=valores,
        )
        if not aplicado:
            # Un worker con lease vencido no puede revocar ni solicitar nada.
            db.rollback()
            resultado.conflicto_lease += 1
            return True
        # Misma transaccion tenant: entrega INVALIDO + dispositivo desactivado
        # + solicitud durable de revocacion global. El worker NO abre el
        # Control Plane.
        if contexto["dispositivo_usuario"] == contexto["usuario_notificacion"]:
            dispositivo = db.get(DispositivoPush, dispositivo_id)
            if dispositivo is not None and bool(dispositivo.activo):
                repo_dispositivos.revocar(db, dispositivo, ahora)
            _, creada = registrar_solicitud_revocacion(db, dispositivo, ahora)
            if creada:
                resultado.revocaciones_solicitadas += 1
        db.commit()
        resultado.invalidadas += 1
        return True

    if envio.clasificacion == fcm.PERMANENTE:
        cerrar(
            "FALLIDO",
            codigo=fcm.sanitizar_codigo_error(envio.codigo, "PAYLOAD_INVALIDO"),
        )
        return True

    if envio.clasificacion in (fcm.ERROR_CONFIGURACION, fcm.NO_CONFIGURADO):
        # Error global del proveedor: no invalida tokens ni consume intentos.
        codigo = fcm.sanitizar_codigo_error(
            envio.codigo, "PROVEEDOR_NO_AUTORIZADO",
        )
        aplicado = repo_entregas.actualizar_resultado(
            db,
            entrega_id,
            lease_token=lease_token,
            valores={
                "estado": "PENDIENTE",
                "intentos": intentos_previos,
                "codigo_ultimo_error": codigo,
                "proveedor_mensaje_id": None,
                "proximo_intento": ahora + timedelta(
                    seconds=configuracion.backoff_base_segundos,
                ),
                "bloqueado_hasta": None,
                "lease_token": None,
                "fecha_actualizacion": ahora,
            },
        )
        db.commit()
        if aplicado:
            resultado.liberadas += 1
        else:
            resultado.conflicto_lease += 1
        resultado.error_configuracion = True
        return False

    # REINTENTABLE (429/5xx/timeout/red): backoff acotado y respeto de
    # Retry-After cuando el proveedor lo entrega.
    codigo = fcm.sanitizar_codigo_error(envio.codigo, "PROVEEDOR_ERROR")
    nuevo_intentos = intentos_previos + 1
    if nuevo_intentos >= configuracion.max_intentos:
        cerrar("FALLIDO", codigo=CODIGO_MAXIMO_INTENTOS)
        return True

    espera = calcular_backoff(nuevo_intentos, configuracion)
    if envio.reintentar_despues_segundos:
        espera = min(
            max(espera, int(envio.reintentar_despues_segundos)),
            configuracion.backoff_maximo_segundos,
        )
    cerrar(
        "PENDIENTE",
        codigo=codigo,
        proximo_intento=ahora + timedelta(seconds=espera),
    )
    resultado.reprogramadas += 1
    return True


def _liberar_restantes(
    db: Session,
    pendientes: list[tuple[int, int, int]],
    *,
    lease_token: str,
    ahora: datetime,
    configuracion: ConfiguracionWorker,
) -> int:
    """Devuelve el lote no procesado a la cola sin consumir intentos."""
    liberadas = 0
    for entrega_id, _, _ in pendientes:
        aplicado = repo_entregas.actualizar_resultado(
            db,
            entrega_id,
            lease_token=lease_token,
            valores={
                "estado": "PENDIENTE",
                "proximo_intento": ahora + timedelta(
                    seconds=configuracion.backoff_base_segundos,
                ),
                "bloqueado_hasta": None,
                "lease_token": None,
                "fecha_actualizacion": ahora,
            },
        )
        liberadas += int(aplicado)
    db.commit()
    return liberadas


def procesar_entregas(
    db: Session,
    *,
    proveedor,
    configuracion: ConfiguracionWorker | None = None,
    reloj: Callable[[], datetime] | None = None,
) -> ResultadoWorker:
    """Punto de entrada invocable del worker para UNA base tenant.

    ``db`` debe ser una sesion ya resuelta por el proceso confiable. El worker
    no resuelve tenants ni abre conexiones.
    """
    config = configuracion or ConfiguracionWorker()
    ahora = (reloj or _ahora)()
    resultado = ResultadoWorker()

    if not getattr(proveedor, "configurado", False):
        # Sin credenciales autorizadas no se reclama nada: no se consume cola
        # ni se simula un envio exitoso.
        resultado.proveedor_no_configurado = True
        return resultado

    lease_token = secrets.token_hex(16)
    lease_hasta = ahora + timedelta(seconds=config.lease_segundos)

    reclamadas = repo_entregas.reclamar_lote(
        db,
        ahora=ahora,
        lease_token=lease_token,
        lease_hasta=lease_hasta,
        limite=config.tamano_lote,
    )
    lote = [
        (entrega.id, entrega.notificacion_id, entrega.dispositivo_id)
        for entrega in reclamadas
    ]
    # El claim se confirma antes de llamar al proveedor.
    db.commit()
    resultado.reclamadas = len(lote)

    for indice, (entrega_id, notificacion_id, dispositivo_id) in enumerate(lote):
        continuar = _procesar_una(
            db,
            entrega_id=entrega_id,
            notificacion_id=notificacion_id,
            dispositivo_id=dispositivo_id,
            lease_token=lease_token,
            proveedor=proveedor,
            configuracion=config,
            ahora=ahora,
            resultado=resultado,
        )
        if not continuar:
            resultado.liberadas += _liberar_restantes(
                db,
                lote[indice + 1:],
                lease_token=lease_token,
                ahora=ahora,
                configuracion=config,
            )
            break

    return resultado
