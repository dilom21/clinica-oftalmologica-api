"""Punto de entrada de una sola ejecucion para un scheduler externo (3C.5.3).

Uso previsto (job programado del despliegue, **no** HTTP)::

    python -m app.modules.notificaciones_push.orquestador \
        --empresa-codigo <codigo_configurado_en_el_job>

El codigo de empresa es un parametro de **despliegue** definido en la
definicion del job. No se acepta desde HTTP, cuerpo de request ni cabecera. El
proceso resuelve el tenant con el mecanismo existente del Control Plane
(``TenantResolver`` + ``TenantEngineRegistry``) y ejecuta **un unico** ciclo
acotado; no enumera empresas.

Este modulo:

* no se importa desde ``app.main`` ni arranca nada al importarse;
* no lee ``.env``, DSN, service-account JSON ni secretos;
* no instala dependencias, no crea endpoints y no aplica migraciones;
* construye el proveedor FCM mediante ``obtener_proveedor_fcm``, que queda
  ``NO_CONFIGURADO`` hasta que el despliegue autorizado inyecte proyecto y un
  ``ProveedorAccessToken``;
* imprime solo el resumen agregado y sanitizado del ciclo;
* devuelve un codigo de salida determinista (ver ``CODIGOS_SALIDA``).

La integracion real con un scheduler productivo queda **BLOQUEADA** hasta que
exista (a) un host/job autorizado y (b) un gestor de credenciales OAuth
aprobado. El codigo de coordinacion ya esta disponible y probado con dobles.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import signal
import sys

from app.core.tenancy.registry_provider import tenant_engine_registry
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.database import session as database_session
from app.modules.notificaciones_push.orquestacion import (
    ConfiguracionCiclo,
    SesionesCiclo,
    ejecutar_ciclo_tenant,
)
from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.services.revocaciones import (
    ConfiguracionRevocaciones,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
)
from app.modules.notificaciones_push.services.recordatorios import PoliticaRecordatorios

logger = logging.getLogger(__name__)

_EMPRESA_CODIGO_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


class FuenteSesionesControlPlane:
    """Composicion raiz: resuelve UN tenant con el Control Plane existente.

    Reutiliza ``TenantResolver`` y ``TenantEngineRegistry`` sin modificarlos.
    No recibe nombre de base, DSN ni credenciales; solo el codigo de empresa
    que el despliegue definio para este job.
    """

    def __init__(self, empresa_codigo: str) -> None:
        if not isinstance(empresa_codigo, str) or not _EMPRESA_CODIGO_PATTERN.fullmatch(
            empresa_codigo.strip()
        ):
            raise ValueError("empresa_codigo de despliegue invalido")
        self._empresa_codigo = empresa_codigo.strip()

    def abrir(self) -> SesionesCiclo:
        control = database_session.open_control_session()
        try:
            resolver = TenantResolver(TenantControlPlaneRepository(control))
            contexto = resolver.resolver_por_codigo_empresa(self._empresa_codigo)
            resolver.require_active_database(contexto)
            db = tenant_engine_registry.get_session(contexto)
        except Exception:
            control.close()
            raise
        db.info["tenant_context"] = contexto
        return SesionesCiclo(
            db=db,
            control=control,
            tenant_database_id=contexto.tenant_database_id,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.modules.notificaciones_push.orquestador",
        description=(
            "Ejecuta un ciclo Push acotado para un tenant definido por el job "
            "de despliegue. No es una API HTTP ni un daemon."
        ),
    )
    parser.add_argument(
        "--empresa-codigo",
        required=True,
        help=(
            "Codigo de empresa definido en la configuracion del job. Se resuelve "
            "contra el Control Plane; no se acepta desde HTTP."
        ),
    )
    parser.add_argument(
        "--lote-entregas",
        type=int,
        default=20,
        help="Entregas FCM por ciclo (1-100).",
    )
    parser.add_argument(
        "--lote-revocaciones",
        type=int,
        default=20,
        help="Revocaciones globales por ciclo (1-100).",
    )
    parser.add_argument(
        "--lease-segundos",
        type=int,
        default=120,
        help="Duracion del lease de ambos outboxes (5-3600).",
    )
    parser.add_argument(
        "--timeout-proveedor-segundos",
        type=float,
        default=10.0,
        help="Timeout HTTP del proveedor FCM; debe ser menor que el lease.",
    )
    parser.add_argument(
        "--duracion-maxima-segundos",
        type=float,
        default=90.0,
        help="Presupuesto del ciclo; no puede superar el lease.",
    )
    parser.add_argument(
        "--recordatorio-anticipacion-minutos",
        action="append",
        type=int,
        default=[],
        help=("Anticipacion explicita en minutos; repetible. Sin valores el "
              "productor falla cerrado (POLICY_NOT_CONFIGURED)."),
    )
    parser.add_argument(
        "--recordatorio-gracia-minutos",
        type=int,
        default=None,
        help="Ventana de gracia explicita y acotada (0-1440 minutos).",
    )
    return parser


def _configuracion_desde_args(args: argparse.Namespace) -> ConfiguracionCiclo:
    return ConfiguracionCiclo(
        entregas=ConfiguracionWorker(
            tamano_lote=args.lote_entregas,
            lease_segundos=args.lease_segundos,
            timeout_proveedor_segundos=args.timeout_proveedor_segundos,
        ),
        revocaciones=ConfiguracionRevocaciones(
            tamano_lote=args.lote_revocaciones,
            lease_segundos=args.lease_segundos,
        ),
        duracion_maxima_segundos=args.duracion_maxima_segundos,
        recordatorios=PoliticaRecordatorios(
            anticipaciones_minutos=tuple(args.recordatorio_anticipacion_minutos),
            gracia_minutos=args.recordatorio_gracia_minutos,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # La configuracion se valida ANTES de abrir cualquier sesion o credencial.
    try:
        configuracion = _configuracion_desde_args(args)
        fuente = FuenteSesionesControlPlane(args.empresa_codigo)
    except ValueError:
        print(
            json.dumps(
                {"estado": "CONFIGURACION_INVALIDA", "codigo_salida": 2},
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        return 2

    detener = {"valor": False}

    def solicitar_detencion(_senal, _frame) -> None:
        detener["valor"] = True

    try:
        signal.signal(signal.SIGTERM, solicitar_detencion)
        signal.signal(signal.SIGINT, solicitar_detencion)
    except ValueError:  # pragma: no cover - hilo no principal
        pass

    resultado = ejecutar_ciclo_tenant(
        fuente,
        proveedor=fcm.obtener_proveedor_fcm(),
        configuracion=configuracion,
        cancelado=lambda: detener["valor"],
    )
    print(json.dumps(resultado.resumen(), ensure_ascii=True, sort_keys=True))
    return resultado.codigo_salida


__all__ = ["FuenteSesionesControlPlane", "main"]


if __name__ == "__main__":  # pragma: no cover - invocacion del scheduler
    raise SystemExit(main())
