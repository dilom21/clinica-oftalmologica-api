"""Worker durable de comprobantes CU31.

Comando Render Background Worker:
    python -m app.modules.gestion_pagos.notificacion_runner

El proceso recorre legacy y tenants activos. Cada entrega se reclama mediante
FOR UPDATE SKIP LOCKED y se confirma antes de llamar a Resend, por lo que no
mantiene locks de PostgreSQL durante I/O de red.
"""

import logging
import signal
import time

from app.core import config
from app.core.tenancy.registry_provider import tenant_engine_registry
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.database import session as database_session
from app.modules.gestion_pagos.providers.resend_provider import ResendProvider
from app.modules.gestion_pagos.services.notificaciones import procesar_siguiente


logger = logging.getLogger(__name__)


def procesar_base(session_factory, proveedor: ResendProvider, limite: int) -> int:
    procesadas = 0
    for _ in range(limite):
        db = session_factory()
        try:
            if not procesar_siguiente(db, proveedor):
                break
            procesadas += 1
        finally:
            db.close()
    return procesadas


def ejecutar_ciclo(proveedor: ResendProvider | None = None, limite: int | None = None) -> int:
    proveedor = proveedor or ResendProvider()
    proveedor.validar_configuracion()
    limite = limite or config.RESEND_WORKER_BATCH_SIZE
    total = procesar_base(database_session.SessionLocal, proveedor, limite)

    control = database_session.open_control_session()
    try:
        repo = TenantControlPlaneRepository(control)
        resolver = TenantResolver(repo)
        for empresa in repo.list_active_empresas():
            try:
                contexto = resolver.resolver_por_codigo_empresa(empresa.codigo)
                resolver.require_active_database(contexto)
                def tenant_factory(contexto=contexto):
                    db = tenant_engine_registry.get_session(contexto)
                    db.info["tenant_context"] = contexto
                    return db
                total += procesar_base(tenant_factory, proveedor, limite)
            except Exception:
                # Un tenant no debe detener la cola de las otras clinicas.
                logger.exception(
                    "No se pudo procesar la cola de un tenant activo",
                    extra={"empresa_codigo": empresa.codigo},
                )
    finally:
        control.close()
    return total


def main() -> None:
    detener = False

    def solicitar_detencion(_senal, _frame):
        nonlocal detener
        detener = True

    signal.signal(signal.SIGTERM, solicitar_detencion)
    signal.signal(signal.SIGINT, solicitar_detencion)
    while not detener:
        try:
            ejecutar_ciclo()
        except Exception:
            logger.exception("Fallo el ciclo del worker de comprobantes")
        if not detener:
            time.sleep(config.RESEND_WORKER_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
