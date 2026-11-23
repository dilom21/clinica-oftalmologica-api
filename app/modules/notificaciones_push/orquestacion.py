"""Orquestacion segura de un ciclo Push para UNA base tenant (Fase 3C.5.3).

Esta capa coordina los dos procesos ya existentes sin duplicar su logica:

1. productor de recordatorios (cuando recibe politica explicita);
2. ``services.worker_fcm.procesar_entregas`` (entregas FCM);
3. ``services.revocaciones.procesar_revocaciones`` (revocaciones globales).

Reglas de seguridad:

* una invocacion procesa **un unico tenant** ya resuelto por una composicion
  raiz confiable; este modulo no descubre bases, no enumera empresas y no
  acepta ``tenant_id``/``empresa_id``/DSN desde HTTP;
* el worker de entregas nunca abre el Control Plane: la sesion tenant la
  entrega la fuente de sesiones y el Control Plane (si existe) solo se pasa al
  procesador de revocaciones;
* no hay bucle infinito, ni endpoint HTTP, ni arranque desde ``app.main``: es
  una ejecucion de una sola vez pensada para un scheduler externo;
* las sesiones se cierran siempre (exito, excepcion, cancelacion o timeout);
* los errores de sesion, Control Plane o configuracion del proveedor **no** se
  registran como exito; el codigo de salida es determinista.

Orden de las etapas (documentado y probado):

1. entregas FCM (si el proveedor no esta configurado no se reclama nada);
2. revocaciones pendientes, incluidas las generadas por la etapa 1.

La telemetria es agregada: identificador de ejecucion no sensible, inicio/fin,
duracion y contadores. Nunca se registran tokens, huellas completas, PII,
payloads, DSN ni credenciales.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from app.modules.notificaciones_push.services.revocaciones import (
    ConfiguracionRevocaciones,
    ResultadoRevocaciones,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
    ResultadoWorker,
)
from app.modules.notificaciones_push.services.recordatorios import (
    ESTADO_POLITICA_INVALIDA,
    ESTADO_POLICY_NOT_CONFIGURED,
    PoliticaRecordatorios,
    ResultadoRecordatorios,
    generar_recordatorios,
)
from app.modules.notificaciones_push.worker import (
    ejecutar_revocaciones_tenant,
    ejecutar_worker_tenant,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Estados y codigos de salida deterministas
# --------------------------------------------------------------------------- #
ESTADO_COMPLETADO = "COMPLETADO"
ESTADO_SIN_TRABAJO = "SIN_TRABAJO"
ESTADO_PARCIAL = "PARCIAL"
ESTADO_PROVEEDOR_NO_CONFIGURADO = "PROVEEDOR_NO_CONFIGURADO"
ESTADO_ERROR_PROVEEDOR = "ERROR_PROVEEDOR"
ESTADO_ERROR_CONTROL_PLANE = "ERROR_CONTROL_PLANE"
ESTADO_ERROR_SESION = "ERROR_SESION"
ESTADO_CANCELADO = "CANCELADO"
ESTADO_TIEMPO_AGOTADO = "TIEMPO_AGOTADO"
ESTADO_ERROR_INESPERADO = "ERROR_INESPERADO"

# 0: ciclo correcto (con o sin trabajo). 1: trabajo completado con fallos
# individuales. 2: sesion/Control Plane/credenciales. 3: cancelacion, tiempo
# agotado o error inesperado. 4: proveedor no configurado (no se reclamo nada).
CODIGOS_SALIDA = {
    ESTADO_COMPLETADO: 0,
    ESTADO_SIN_TRABAJO: 0,
    ESTADO_PARCIAL: 1,
    ESTADO_ERROR_SESION: 2,
    ESTADO_ERROR_CONTROL_PLANE: 2,
    ESTADO_ERROR_PROVEEDOR: 2,
    ESTADO_CANCELADO: 3,
    ESTADO_TIEMPO_AGOTADO: 3,
    ESTADO_ERROR_INESPERADO: 3,
    ESTADO_PROVEEDOR_NO_CONFIGURADO: 4,
    ESTADO_POLICY_NOT_CONFIGURED: 5,
    ESTADO_POLITICA_INVALIDA: 5,
}

DURACION_MAXIMA_CICLO_SEGUNDOS = 900.0

MAXIMO_LOTE = 100


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Configuracion del ciclo
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ConfiguracionCiclo:
    """Limites del ciclo, coherentes con los limites de cada worker.

    ``duracion_maxima_segundos`` es un presupuesto blando comprobado **entre**
    etapas y antes de cada etapa: no interrumpe una llamada HTTP en curso. Si
    se agota, no se inicia la etapa siguiente y el resultado lo declara. Los
    claims ya confirmados quedan recuperables por lease, igual que en 3C.5/3C.5.2.
    """

    entregas: ConfiguracionWorker = field(default_factory=ConfiguracionWorker)
    revocaciones: ConfiguracionRevocaciones = field(
        default_factory=ConfiguracionRevocaciones,
    )
    duracion_maxima_segundos: float = 90.0
    # ``None`` conserva compatibilidad con invocadores 3C.5.3 que solo
    # coordinan colas. La CLI 3C.6 siempre inyecta una politica (incluso vacia),
    # de modo que la ausencia de anticipaciones falla cerrado.
    recordatorios: PoliticaRecordatorios | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entregas, ConfiguracionWorker):
            raise ValueError("configuracion de entregas invalida")
        if not isinstance(self.revocaciones, ConfiguracionRevocaciones):
            raise ValueError("configuracion de revocaciones invalida")
        if self.recordatorios is not None and not isinstance(
            self.recordatorios, PoliticaRecordatorios
        ):
            raise ValueError("politica de recordatorios invalida")
        duracion = self.duracion_maxima_segundos
        if isinstance(duracion, bool) or not isinstance(duracion, (int, float)):
            raise ValueError("duracion_maxima_segundos debe ser numerica")
        if not 0 < float(duracion) <= DURACION_MAXIMA_CICLO_SEGUNDOS:
            raise ValueError(
                "duracion_maxima_segundos debe estar entre 0 y "
                f"{DURACION_MAXIMA_CICLO_SEGUNDOS:g}"
            )
        # El presupuesto del ciclo no puede superar el lease menor: de lo
        # contrario un claim podria vencer antes de cerrar la etapa.
        lease_menor = min(
            self.entregas.lease_segundos,
            self.revocaciones.lease_segundos,
        )
        if float(duracion) > lease_menor:
            raise ValueError(
                "duracion_maxima_segundos no puede superar el lease menor"
            )


# --------------------------------------------------------------------------- #
# Sesiones inyectadas por la composicion raiz
# --------------------------------------------------------------------------- #
@dataclass
class SesionesCiclo:
    """Sesiones ya resueltas por la capa de despliegue confiable.

    ``db`` es la sesion de UNA base tenant; ``control`` es la sesion del
    Control Plane (puede ser ``None`` si el despliegue no la proporciona).
    El coordinador las cierra siempre; ``cerrar`` es idempotente.
    """

    db: Session | None
    tenant_database_id: int
    control: Session | None = None
    cerrada: bool = False

    def cerrar(self) -> None:
        if self.cerrada:
            return
        for sesion in (self.db, self.control):
            if sesion is None:
                continue
            try:
                sesion.rollback()
            except Exception:  # noqa: BLE001 - cierre defensivo
                pass
            try:
                sesion.close()
            except Exception:  # noqa: BLE001 - cierre defensivo
                pass
        self.cerrada = True


class FuenteSesiones(Protocol):
    """Puerto de la composicion raiz: abre sesiones para un tenant ya resuelto.

    La implementacion debe resolver el tenant con el mecanismo confiable del
    despliegue (Control Plane) y nunca a partir de datos recibidos por HTTP.
    """

    def abrir(self) -> SesionesCiclo: ...


# --------------------------------------------------------------------------- #
# Resultado sanitizado
# --------------------------------------------------------------------------- #
@dataclass
class ResultadoCiclo:
    """Resumen operativo de una invocacion. Sin tokens, huellas ni PII."""

    ejecucion_id: str
    estado: str
    codigo_salida: int
    inicio: datetime
    fin: datetime
    duracion_ms: int
    sesiones_abiertas: bool = False
    sesiones_cerradas: bool = True
    control_plane_disponible: bool = False
    error_codigo: str | None = None
    entregas: ResultadoWorker | None = None
    revocaciones: ResultadoRevocaciones | None = None
    recordatorios: ResultadoRecordatorios | None = None

    def resumen(self) -> dict:
        """Diccionario plano y sanitizado apto para logs/CLI."""
        return {
            "ejecucion_id": self.ejecucion_id,
            "estado": self.estado,
            "codigo_salida": self.codigo_salida,
            "inicio": self.inicio.isoformat(),
            "fin": self.fin.isoformat(),
            "duracion_ms": self.duracion_ms,
            "sesiones_abiertas": self.sesiones_abiertas,
            "sesiones_cerradas": self.sesiones_cerradas,
            "control_plane_disponible": self.control_plane_disponible,
            "error_codigo": self.error_codigo,
            "recordatorios": _resumen_recordatorios(self.recordatorios),
            "entregas": _resumen_entregas(self.entregas),
            "revocaciones": _resumen_revocaciones(self.revocaciones),
        }


def _resumen_recordatorios(resultado: ResultadoRecordatorios | None) -> dict | None:
    if resultado is None:
        return None
    return {
        "estado": resultado.estado,
        "examinadas": resultado.examinadas,
        "creadas": resultado.creadas,
        "existentes": resultado.existentes,
        "omitidas_sin_destinatario": resultado.omitidas_sin_destinatario,
        "omitidas_tiempo_invalido": resultado.omitidas_tiempo_invalido,
        "obsoletas_canceladas": resultado.obsoletas_canceladas,
    }


def _resumen_entregas(resultado: ResultadoWorker | None) -> dict | None:
    if resultado is None:
        return None
    return {
        "reclamadas": resultado.reclamadas,
        "aceptadas": resultado.aceptadas,
        "fallidas": resultado.fallidas,
        "invalidadas": resultado.invalidadas,
        "omitidas": resultado.omitidas,
        "reprogramadas": resultado.reprogramadas,
        "liberadas": resultado.liberadas,
        "conflicto_lease": resultado.conflicto_lease,
        "revocaciones_solicitadas": resultado.revocaciones_solicitadas,
        "proveedor_no_configurado": resultado.proveedor_no_configurado,
        "error_configuracion": resultado.error_configuracion,
    }


def _resumen_revocaciones(resultado: ResultadoRevocaciones | None) -> dict | None:
    if resultado is None:
        return None
    return {
        "reclamadas": resultado.reclamadas,
        "completadas": resultado.completadas,
        "ya_revocadas": resultado.ya_revocadas,
        "no_encontradas": resultado.no_encontradas,
        "rechazadas": resultado.rechazadas,
        "reprogramadas": resultado.reprogramadas,
        "fallidas": resultado.fallidas,
        "conflicto_lease": resultado.conflicto_lease,
        "error_control": resultado.error_control,
    }


# --------------------------------------------------------------------------- #
# Validaciones defensivas
# --------------------------------------------------------------------------- #
def _validar_tenant_database_id(valor: object) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise ValueError(
            "tenant_database_id debe ser un entero positivo de contexto interno"
        )
    return valor


def _validar_sesion_tenant(db: Session | None, tenant_database_id: int) -> None:
    """Fail closed: la sesion debe corresponder al tenant declarado."""
    if db is None:
        raise ValueError("sesion tenant no disponible")
    contexto = db.info.get("tenant_context")
    esperado = getattr(contexto, "tenant_database_id", None)
    if esperado is not None and esperado != tenant_database_id:
        raise ValueError(
            "tenant_database_id no coincide con el contexto tenant resuelto"
        )


def _calcular_estado(
    *,
    entregas: ResultadoWorker | None,
    revocaciones: ResultadoRevocaciones | None,
    control_plane_disponible: bool,
) -> tuple[str, str | None]:
    """Estado determinista y codigo de error saneado (o ``None``)."""
    if not control_plane_disponible:
        return ESTADO_ERROR_CONTROL_PLANE, "CONTROL_PLANE_NO_DISPONIBLE"
    if revocaciones is not None and revocaciones.error_control:
        return ESTADO_ERROR_CONTROL_PLANE, "CONTROL_PLANE_NO_DISPONIBLE"
    if entregas is not None and entregas.error_configuracion:
        return ESTADO_ERROR_PROVEEDOR, "PROVEEDOR_NO_AUTORIZADO"
    if entregas is not None and entregas.proveedor_no_configurado:
        return ESTADO_PROVEEDOR_NO_CONFIGURADO, "PROVEEDOR_NO_CONFIGURADO"

    fallos = 0
    conflictos = 0
    if entregas is not None:
        fallos += entregas.fallidas
        conflictos += entregas.conflicto_lease
    if revocaciones is not None:
        fallos += revocaciones.fallidas
        conflictos += revocaciones.conflicto_lease
        # Una revocacion reprogramada significa que el Control Plane no
        # confirmo en este ciclo: no es exito aunque el resto haya ido bien.
        fallos += revocaciones.reprogramadas
    if fallos or conflictos:
        return ESTADO_PARCIAL, None

    reclamadas = 0
    if entregas is not None:
        reclamadas += entregas.reclamadas
    if revocaciones is not None:
        reclamadas += revocaciones.reclamadas
    if reclamadas == 0:
        return ESTADO_SIN_TRABAJO, None
    return ESTADO_COMPLETADO, None


# --------------------------------------------------------------------------- #
# Coordinador
# --------------------------------------------------------------------------- #
def ejecutar_ciclo_tenant(
    fuente_sesiones: FuenteSesiones,
    *,
    proveedor: object,
    configuracion: ConfiguracionCiclo | None = None,
    reloj: Callable[[], datetime] | None = None,
    reloj_monotonico: Callable[[], float] | None = None,
    cancelado: Callable[[], bool] | None = None,
) -> ResultadoCiclo:
    """Ejecuta un ciclo acotado para el tenant que entregue la fuente.

    No recibe identificadores de tenant, nombres de base, DSN ni credenciales:
    esos datos solo pueden venir de la composicion raiz confiable.
    """
    config = configuracion or ConfiguracionCiclo()
    leer_reloj = reloj or _ahora
    leer_monotonico = reloj_monotonico or time.monotonic
    esta_cancelado = cancelado or (lambda: False)

    ejecucion_id = secrets.token_hex(8)
    inicio = leer_reloj()
    t0 = leer_monotonico()
    resultado = ResultadoCiclo(
        ejecucion_id=ejecucion_id,
        estado=ESTADO_ERROR_INESPERADO,
        codigo_salida=CODIGOS_SALIDA[ESTADO_ERROR_INESPERADO],
        inicio=inicio,
        fin=inicio,
        duracion_ms=0,
    )

    def cerrar_resultado() -> None:
        resultado.fin = leer_reloj()
        resultado.duracion_ms = max(0, int((leer_monotonico() - t0) * 1000))
        resultado.codigo_salida = CODIGOS_SALIDA[resultado.estado]
        logger.info(
            "ciclo_push_tenant finalizado",
            extra={"ciclo_push": resultado.resumen()},
        )

    if config.recordatorios is not None:
        error_politica = config.recordatorios.validar()
        if error_politica:
            resultado.recordatorios = ResultadoRecordatorios(estado=error_politica)
            resultado.estado = error_politica
            resultado.error_codigo = error_politica
            cerrar_resultado()
            return resultado

    # 1. Abrir sesiones con la composicion raiz. Un fallo se declara como
    #    ERROR_SESION y jamas se convierte en exito.
    sesiones: SesionesCiclo | None = None
    try:
        sesiones = fuente_sesiones.abrir()
        resultado.sesiones_abiertas = True
        tenant_id = _validar_tenant_database_id(sesiones.tenant_database_id)
        _validar_sesion_tenant(sesiones.db, tenant_id)
        resultado.control_plane_disponible = sesiones.control is not None
    except Exception:  # noqa: BLE001 - causa saneada, sin PII
        if sesiones is not None:
            sesiones.cerrar()
        resultado.estado = ESTADO_ERROR_SESION
        resultado.error_codigo = "SESION_TENANT_NO_DISPONIBLE"
        resultado.sesiones_cerradas = True
        cerrar_resultado()
        return resultado

    try:
        # 2. Cancelacion previa: no se reclama nada.
        if esta_cancelado():
            resultado.estado = ESTADO_CANCELADO
            resultado.error_codigo = "CANCELADO_ANTES_DE_INICIAR"
            return resultado

        # 3. Productor de recordatorios. Confirma su lote atomico antes de que
        #    el worker reclame las entregas creadas.
        if config.recordatorios is not None:
            resultado.recordatorios = generar_recordatorios(
                sesiones.db, politica=config.recordatorios, reloj=reloj,
            )

        # 4. Entregas FCM. El worker no abre Control Plane.
        resultado.entregas = ejecutar_worker_tenant(
            sesiones.db,
            proveedor=proveedor,
            configuracion=config.entregas,
            reloj=reloj,
        )

        if esta_cancelado():
            # Los claims ya confirmados son recuperables por lease.
            resultado.estado = ESTADO_CANCELADO
            resultado.error_codigo = "CANCELADO_TRAS_ENTREGAS"
            return resultado

        if (leer_monotonico() - t0) > float(config.duracion_maxima_segundos):
            resultado.estado = ESTADO_TIEMPO_AGOTADO
            resultado.error_codigo = "PRESUPUESTO_DE_CICLO_AGOTADO"
            return resultado

        # 5. Revocaciones pendientes (incluye las generadas por entregas).
        if sesiones.control is None:
            resultado.estado = ESTADO_ERROR_CONTROL_PLANE
            resultado.error_codigo = "CONTROL_PLANE_NO_DISPONIBLE"
            return resultado

        resultado.revocaciones = ejecutar_revocaciones_tenant(
            sesiones.db,
            sesiones.control,
            tenant_database_id=tenant_id,
            configuracion=config.revocaciones,
            reloj=reloj,
        )

        resultado.estado, resultado.error_codigo = _calcular_estado(
            entregas=resultado.entregas,
            revocaciones=resultado.revocaciones,
            control_plane_disponible=True,
        )
        return resultado

    except Exception:  # noqa: BLE001 - no se filtra la causa ni secretos
        try:
            if sesiones.db is not None:
                sesiones.db.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            if sesiones.control is not None:
                sesiones.control.rollback()
        except Exception:  # noqa: BLE001
            pass
        resultado.estado = ESTADO_ERROR_INESPERADO
        resultado.error_codigo = "ERROR_INESPERADO_EN_CICLO"
        return resultado

    finally:
        sesiones.cerrar()
        resultado.sesiones_cerradas = sesiones.cerrada
        cerrar_resultado()


__all__ = [
    "CODIGOS_SALIDA",
    "ConfiguracionCiclo",
    "DURACION_MAXIMA_CICLO_SEGUNDOS",
    "ESTADO_CANCELADO",
    "ESTADO_COMPLETADO",
    "ESTADO_ERROR_CONTROL_PLANE",
    "ESTADO_ERROR_INESPERADO",
    "ESTADO_ERROR_PROVEEDOR",
    "ESTADO_ERROR_SESION",
    "ESTADO_PARCIAL",
    "ESTADO_PROVEEDOR_NO_CONFIGURADO",
    "ESTADO_SIN_TRABAJO",
    "ESTADO_TIEMPO_AGOTADO",
    "ESTADO_POLICY_NOT_CONFIGURED",
    "ESTADO_POLITICA_INVALIDA",
    "FuenteSesiones",
    "ResultadoCiclo",
    "SesionesCiclo",
    "ejecutar_ciclo_tenant",
]
