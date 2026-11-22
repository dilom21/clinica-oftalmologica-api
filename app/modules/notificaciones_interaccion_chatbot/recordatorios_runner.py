"""Ejecuta una sola pasada de recordatorios CU39. No se inicia con la API."""

import argparse
import logging
import os
from datetime import datetime, time
from pathlib import Path

from app.core.tenancy.registry_provider import tenant_engine_registry
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.database.session import open_control_session
from app.modules.administracion_saas.tenant_migrator import load_bundle
from app.modules.notificaciones_interaccion_chatbot.services.recordatorios import (
    ConfiguracionRecordatorios, ZONA_CLINICA, procesar_tenant,
)

logger = logging.getLogger(__name__)


def ejecutar_una_vez(
    *, control_session_factory=open_control_session,
    registry=tenant_engine_registry,
    ahora: datetime | None = None,
    configuracion: ConfiguracionRecordatorios = ConfiguracionRecordatorios(),
    version_requerida: str = "v5-sprint4-cu39",
) -> dict[str, int]:
    """Aísla cada clínica y continúa si una falla o aún no tiene CU39."""
    instante = ahora or datetime.now(ZONA_CLINICA)
    resultado = {"clinicas_ok": 0, "clinicas_omitidas": 0,
                 "clinicas_fallidas": 0, "recordatorios": 0}
    control = control_session_factory()
    try:
        repositorio = TenantControlPlaneRepository(control)
        resolver = TenantResolver(repositorio)
        for empresa in repositorio.list_active_empresas():
            db = None
            try:
                contexto = resolver.resolver_por_codigo_empresa(empresa.codigo)
                resolver.require_active_database(contexto)
                if contexto.version_schema != version_requerida:
                    resultado["clinicas_omitidas"] += 1
                    logger.warning("CU39 no está migrado para la clínica %s", empresa.codigo)
                    continue
                db = registry.get_session(contexto)
                db.info["tenant_context"] = contexto
                creados = procesar_tenant(db, ahora=instante, configuracion=configuracion)
                db.commit()
                resultado["clinicas_ok"] += 1
                resultado["recordatorios"] += creados
            except Exception:
                if db is not None:
                    db.rollback()
                resultado["clinicas_fallidas"] += 1
                logger.exception("Falló un ciclo CU39 para la clínica %s", empresa.codigo)
            finally:
                if db is not None:
                    db.close()
    finally:
        control.close()
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Una pasada de recordatorios CU39")
    parser.add_argument("--run", action="store_true", required=True)
    args = parser.parse_args(argv)
    del args
    try:
        horas = int(os.getenv("CU39_CITA_ANTELACION_HORAS", "24"))
        hora = time.fromisoformat(os.getenv("CU39_CONTROL_HORA", "09:00"))
        configuracion = ConfiguracionRecordatorios(horas_cita=horas, hora_control=hora)
        raiz = Path(__file__).resolve().parents[3] / "database" / "tenant_migrations"
        version = load_bundle(raiz).schema_version
        resultado = ejecutar_una_vez(configuracion=configuracion, version_requerida=version)
    except Exception:
        logger.exception("No se pudo iniciar el proceso CU39")
        return 2
    logger.info("CU39 recordatorios: %s", resultado)
    return 1 if resultado["clinicas_fallidas"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
