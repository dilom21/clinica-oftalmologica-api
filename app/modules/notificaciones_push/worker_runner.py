
"""Worker periodico multitenant para notificaciones FCM.

Consulta las empresas activas del Control Plane y ejecuta un ciclo
independiente para cada tenant mediante el orquestador existente.
"""

from __future__ import annotations

import logging
import math
import os
import signal
import threading

from dataclasses import dataclass

from app.database import session as database_session
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.modules.notificaciones_push.orquestador import (
    FuenteSesionesControlPlane,
)
from app.modules.notificaciones_push.orquestacion import (
    ConfiguracionCiclo,
    ejecutar_ciclo_tenant,
)
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.services.recordatorios import (
    PoliticaRecordatorios,
)
from app.modules.notificaciones_push.services.revocaciones import (
    ConfiguracionRevocaciones,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfiguracionRunner:
    intervalo_segundos: int
    configuracion_ciclo: ConfiguracionCiclo


def _entero_env(
    nombre: str,
    defecto: int,
    minimo: int,
    maximo: int,
) -> int:
    valor = os.getenv(nombre)

    try:
        resultado = defecto if valor is None or not valor.strip() else int(valor)
    except ValueError:
        raise RuntimeError(f"{nombre} debe ser un entero valido") from None

    if not minimo <= resultado <= maximo:
        raise RuntimeError(f"{nombre} fuera del rango permitido")

    return resultado


def _float_env(
    nombre: str,
    defecto: float,
    minimo: float,
    maximo: float,
) -> float:
    valor = os.getenv(nombre)

    try:
        resultado = defecto if valor is None or not valor.strip() else float(valor)
    except ValueError:
        raise RuntimeError(f"{nombre} debe ser numerico") from None

    if not math.isfinite(resultado) or not minimo <= resultado <= maximo:
        raise RuntimeError(f"{nombre} fuera del rango permitido")

    return resultado


def _anticipaciones_env() -> tuple[int, ...]:
    valor = os.getenv(
        "PUSH_REMINDER_ANTICIPATIONS_MINUTES",
        "1440,60",
    )

    try:
        anticipaciones = tuple(
            int(parte.strip())
            for parte in valor.split(",")
        )
    except ValueError:
        raise RuntimeError(
            "PUSH_REMINDER_ANTICIPATIONS_MINUTES invalido"
        ) from None

    if (
        not anticipaciones
        or len(set(anticipaciones)) != len(anticipaciones)
        or any(item <= 0 or item > 525600 for item in anticipaciones)
    ):
        raise RuntimeError(
            "PUSH_REMINDER_ANTICIPATIONS_MINUTES fuera de rango"
        )

    return anticipaciones


def cargar_configuracion() -> ConfiguracionRunner:
    """Carga y valida la configuracion antes de abrir conexiones."""

    intervalo = _entero_env(
        "PUSH_WORKER_INTERVAL_SECONDS", 60, 5, 3600,
    )
    lote = _entero_env(
        "PUSH_WORKER_BATCH_SIZE", 20, 1, 100,
    )
    lease = _entero_env(
        "PUSH_WORKER_LEASE_SECONDS", 120, 5, 3600,
    )
    timeout = _float_env(
        "PUSH_WORKER_TIMEOUT_SECONDS", 10.0, 0.1, 3600.0,
    )
    duracion = _float_env(
        "PUSH_WORKER_MAX_CYCLE_SECONDS", 90.0, 0.1, 900.0,
    )

    gracia = _entero_env(
        "PUSH_REMINDER_GRACE_MINUTES", 10, 0, 1440,
    )
    anticipaciones = _anticipaciones_env()

    zona = (
        os.getenv("PUSH_REMINDER_TIMEZONE")
        or "America/La_Paz"
    ).strip()

    politica = PoliticaRecordatorios(
        anticipaciones_minutos=anticipaciones,
        gracia_minutos=gracia,
        zona_horaria=zona,
        limite_citas=100,
    )

    if politica.validar() is not None:
        raise RuntimeError("La politica de recordatorios es invalida")

    try:
        ciclo = ConfiguracionCiclo(
            entregas=ConfiguracionWorker(
                tamano_lote=lote,
                lease_segundos=lease,
                timeout_proveedor_segundos=timeout,
            ),
            revocaciones=ConfiguracionRevocaciones(
                tamano_lote=lote,
                lease_segundos=lease,
            ),
            duracion_maxima_segundos=duracion,
            recordatorios=politica,
        )
    except ValueError:
        raise RuntimeError(
            "La configuracion del ciclo FCM es invalida"
        ) from None

    return ConfiguracionRunner(
        intervalo_segundos=intervalo,
        configuracion_ciclo=ciclo,
    )


def listar_empresas_activas() -> list[str]:
    """Obtiene unicamente los codigos del Control Plane."""

    control = database_session.open_control_session()

    try:
        repository = TenantControlPlaneRepository(control)
        empresas = repository.list_active_empresas()

        return [
            empresa.codigo
            for empresa in empresas
        ]
    finally:
        control.close()


def ejecutar_ciclo_multitenant(
    *,
    proveedor=None,
    configuracion: ConfiguracionCiclo | None = None,
) -> dict:
    """Ejecuta un ciclo independiente por cada empresa activa."""

    proveedor = (
        proveedor
        if proveedor is not None
        else fcm.obtener_proveedor_fcm()
    )

    configuracion = configuracion or cargar_configuracion().configuracion_ciclo

    codigos = listar_empresas_activas()

    estados: dict[str, int] = {}
    ciclos = 0
    fallos = 0

    for codigo in codigos:
        try:
            fuente = FuenteSesionesControlPlane(codigo)

            resultado = ejecutar_ciclo_tenant(
                fuente,
                proveedor=proveedor,
                configuracion=configuracion,
            )

            ciclos += 1
            estados[resultado.estado] = (
                estados.get(resultado.estado, 0) + 1
            )

            if resultado.codigo_salida != 0:
                fallos += 1

            logger.info(
                "Ciclo FCM tenant finalizado",
                extra={
                    "empresa_codigo": codigo,
                    "ciclo_push": resultado.resumen(),
                },
            )

        except Exception as exc:
            # Una empresa con problemas no detiene las demas.
            fallos += 1
            logger.error(
                "Error procesando tenant FCM; tipo=%s",
                type(exc).__name__,
                extra={"empresa_codigo": codigo},
            )

    resumen = {
        "tenants_detectados": len(codigos),
        "ciclos_ejecutados": ciclos,
        "ciclos_con_error": fallos,
        "estados": estados,
    }

    logger.info(
        "Ciclo multitenant FCM finalizado",
        extra={"worker_push": resumen},
    )

    return resumen


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    configuracion = cargar_configuracion()
    detener = threading.Event()

    def solicitar_detencion(_senal, _frame):
        detener.set()

    signal.signal(signal.SIGTERM, solicitar_detencion)
    signal.signal(signal.SIGINT, solicitar_detencion)

    proveedor = fcm.obtener_proveedor_fcm()

    logger.info(
        "Worker FCM iniciado",
        extra={
            "intervalo_segundos": configuracion.intervalo_segundos,
            "recordatorios_minutos": (
                configuracion.configuracion_ciclo
                .recordatorios.anticipaciones_minutos
            ),
            "zona_horaria": "America/La_Paz",
        },
    )

    while not detener.is_set():
        try:
            # Reintenta resolver ADC si el proveedor sigue sin configurar.
            if not proveedor.configurado:
                proveedor = fcm.obtener_proveedor_fcm()

            ejecutar_ciclo_multitenant(
                proveedor=proveedor,
                configuracion=configuracion.configuracion_ciclo,
            )

        except Exception as exc:
            logger.error(
                "Error general del worker FCM; tipo=%s",
                type(exc).__name__,
            )

        detener.wait(configuracion.intervalo_segundos)

    logger.info("Worker FCM detenido correctamente")


if __name__ == "__main__":
    main()
