"""Fanout de notificaciones in-app a entregas push (Fase 3C.5).

Este modulo completa el hueco deliberado de 3C.4: al crear una notificacion en
la bandeja se encola **una entrega PENDIENTE por dispositivo activo elegible**
del usuario destinatario, dentro de la misma sesion/transaccion tenant.

Reglas:

* el destinatario es ``notificacion.usuario_id`` (derivado en 3C.4 desde la
  cita y el paciente, nunca del request);
* solo se consultan dispositivos activos de ese mismo usuario en la base
  tenant actual: no hay conexiones a otras bases ni enumeracion de tenants;
* la preferencia push se evalua con el contrato real de
  ``preferencia_notificacion`` (``push_permitido``);
* la unicidad ``(notificacion_id, dispositivo_id)`` y la relectura de
  entregas existentes hacen el fanout idempotente;
* si no hay dispositivos o push esta deshabilitado se conserva la notificacion
  in-app y no se crea ninguna entrega;
* el modulo **no hace commit**: la transaccion pertenece al caso de uso de
  citas. Un fallo inesperado se propaga para provocar su rollback.
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.models.models import (
    Notificacion,
    NotificacionEntrega,
)
from app.modules.notificaciones_push.repositories import (
    dispositivos as repo_dispositivos,
)
from app.modules.notificaciones_push.repositories import (
    entregas as repo_entregas,
)
from app.modules.notificaciones_push.repositories import (
    preferencias as repo_preferencias,
)
from app.modules.notificaciones_push.schemas.schemas import (
    NotificacionEntregaPersistencia,
)
from app.modules.notificaciones_push.services.preferencias import push_permitido


def _es_entero_positivo(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and valor > 0


def encolar_entregas(
    db: Session,
    notificacion: Notificacion | None,
) -> list[NotificacionEntrega]:
    """Crea las entregas que falten para una notificacion ya persistida."""
    if notificacion is None:
        return []
    if not _es_entero_positivo(getattr(notificacion, "id", None)):
        return []

    usuario_id = getattr(notificacion, "usuario_id", None)
    if not _es_entero_positivo(usuario_id):
        return []

    preferencia = repo_preferencias.obtener_por_usuario(db, usuario_id)
    if not push_permitido(preferencia, getattr(notificacion, "tipo", None)):
        # Bandeja in-app intacta; no se encola push.
        return []

    dispositivos = repo_dispositivos.listar_activos_por_usuario(db, usuario_id)
    if not dispositivos:
        return []

    ya_encolados = {
        entrega.dispositivo_id
        for entrega in repo_entregas.listar_por_notificacion(db, notificacion.id)
    }

    creadas: list[NotificacionEntrega] = []
    for dispositivo in dispositivos:
        dispositivo_id = getattr(dispositivo, "id", None)
        if not _es_entero_positivo(dispositivo_id):
            continue
        if not getattr(dispositivo, "activo", False):
            continue
        if getattr(dispositivo, "usuario_id", None) != usuario_id:
            # Defensa en profundidad: el repositorio ya filtra por usuario.
            continue
        if dispositivo_id in ya_encolados:
            continue
        try:
            with db.begin_nested():
                creadas.append(repo_entregas.crear(
                    db,
                    NotificacionEntregaPersistencia(
                        notificacion_id=notificacion.id,
                        dispositivo_id=dispositivo_id,
                    ),
                ))
        except IntegrityError:
            # Otra transaccion gano la carrera por la misma unicidad.
            continue

    return creadas
