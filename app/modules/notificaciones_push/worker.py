"""Punto de entrada invocable del worker de push (Fase 3C.5).

Este modulo **no** se registra en ``app.main``, no arranca nada al importarse y
no expone un endpoint HTTP. Existe para que un proceso confiable externo
(orquestador, job manual autorizado, futuro scheduler) invoque el worker sobre
**una** base tenant ya resuelta::

    from app.modules.notificaciones_push.worker import ejecutar_worker_tenant

    resultado = ejecutar_worker_tenant(sesion_tenant, proveedor=proveedor)

Responsabilidades del despliegue (pendientes, fuera de esta fase):

* resolver la base tenant con el mecanismo confiable existente;
* construir un ``FcmHttpV1`` con ``proyecto_id`` y un ``ProveedorAccessToken``
  autorizado (sin leer secretos desde este repositorio);
* ejecutar el worker periodicamente por tenant.

Sin esa configuracion el adaptador queda ``NO_CONFIGURADO``: la invocacion no
reclama entregas, no consume intentos y nunca simula un envio exitoso.
"""

from __future__ import annotations

from typing import Callable

from sqlalchemy.orm import Session

from app.modules.notificaciones_push.providers import fcm
from app.modules.notificaciones_push.services.revocaciones import (
    ConfiguracionRevocaciones,
    ResultadoRevocaciones,
    procesar_revocaciones,
)
from app.modules.notificaciones_push.services.worker_fcm import (
    ConfiguracionWorker,
    ResultadoWorker,
    procesar_entregas,
)


def ejecutar_worker_tenant(
    db: Session,
    *,
    proveedor=None,
    configuracion: ConfiguracionWorker | None = None,
    reloj: Callable | None = None,
) -> ResultadoWorker:
    """Procesa un lote de entregas de la sesion tenant recibida.

    No resuelve tenants, no abre conexiones adicionales y no acepta
    identificadores de empresa desde el llamador.
    """
    return procesar_entregas(
        db,
        proveedor=proveedor if proveedor is not None else fcm.obtener_proveedor_fcm(),
        configuracion=configuracion,
        reloj=reloj,
    )


def ejecutar_revocaciones_tenant(
    db: Session,
    control: Session,
    *,
    tenant_database_id: int,
    configuracion: ConfiguracionRevocaciones | None = None,
    reloj: Callable | None = None,
) -> ResultadoRevocaciones:
    """Procesa las solicitudes de revocacion global de UNA base tenant.

    A diferencia del worker FCM, este punto de entrada **si** recibe una sesion
    del Control Plane, resuelta por el mismo proceso confiable que resolvio la
    base tenant. Nunca se invoca desde HTTP, nunca enumera tenants y no acepta
    identificadores del cliente.

    Las transacciones son independientes: primero se confirma el Control Plane
    y solo despues se marca la solicitud tenant como completada. Si el proceso
    cae entre ambos commits, el reintento es idempotente.
    """
    return procesar_revocaciones(
        db,
        control,
        tenant_database_id=tenant_database_id,
        configuracion=configuracion,
        reloj=reloj,
    )
