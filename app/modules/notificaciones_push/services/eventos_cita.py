"""Eventos estructurados de cita y notificaciones in-app (Fase 3C.4).

Este modulo es el limite central que usan los casos de uso de citas para
registrar ``cita_evento`` y crear la notificacion de bandeja del paciente.

Reglas de la fase:

* se ejecuta dentro de la sesion tenant y la transaccion que ya recibio el
  caso de uso: nunca abre otra sesion, no usa Control Plane y **no hace
  ``commit``**;
* el destinatario se deriva exclusivamente de
  ``Cita.paciente_id -> Paciente.usuario_id -> Usuario activo``; nunca del
  request ni de claims;
* si el paciente no tiene una cuenta activa se conserva el evento y no se
  inventa destinatario;
* los textos son genericos y ``datos`` respeta la allowlist de navegacion de
  3C.3 (``ruta``, ``pantalla``, ``seccion``, ``accion``, ``cita_id``);
* la idempotencia usa las constraints de 3C.2: ``cita_evento.clave_idempotencia``
  y ``notificacion.evento_clave``.

No se llama FCM/APNs ni se simula ningun envio. El fanout a
``notificacion_entrega`` (una fila ``PENDIENTE`` por dispositivo activo
elegible) lo agrega 3C.5 en la misma transaccion tenant.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.gestion_pacientes.repositories.repository import (
    obtener_paciente_por_id,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.notificaciones_push.repositories import (
    eventos_cita as repo_eventos,
)
from app.modules.notificaciones_push.repositories import (
    notificaciones as repo_notificaciones,
)
from app.modules.notificaciones_push.schemas.schemas import (
    CitaEventoPersistencia,
    NotificacionPersistencia,
)
from app.modules.notificaciones_push.services import (
    entregas as entregas_push,
)

TIPO_EVENTO_CREADA = "CITA_CREADA"
TIPO_EVENTO_ESTADO = "CITA_ESTADO_CAMBIADO"
TIPO_EVENTO_REPROGRAMADA = "CITA_REPROGRAMADA"

TITULO_CITA_CREADA = "Cita registrada"
TITULO_CITA_REPROGRAMADA = "Cita reprogramada"
TITULO_ESTADO_CITA = "Estado de cita actualizado"

CUERPO_CITA_CREADA = "Tu cita fue registrada."
CUERPO_CITA_REPROGRAMADA = "Tu cita fue reprogramada."
CUERPO_ESTADO_CITA = "El estado de tu cita fue actualizado."

_RUTA_NAVEGACION = "/citas"
_PANTALLA_NAVEGACION = "detalle_cita"
_SECCION_NAVEGACION = "citas"
_ACCION_NAVEGACION = "ver"


def _es_entero_positivo(valor) -> bool:
    """Solo acepta IDs enteros reales (no bool, no mocks, no strings)."""
    return (
        isinstance(valor, int)
        and not isinstance(valor, bool)
        and valor > 0
    )


def _texto_instante(valor) -> str:
    """Normaliza un instante/ fecha para la clave canonica, sin PII."""
    if isinstance(valor, datetime):
        if valor.tzinfo is not None:
            valor = valor.astimezone(timezone.utc).replace(tzinfo=None)
        return valor.isoformat(timespec="microseconds")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, time):
        return valor.replace(microsecond=0).isoformat()
    return ""


def _clave_idempotencia(
    tipo: str,
    cita_id: int,
    partes: tuple[object, ...],
) -> str:
    """Clave determinista de tipo + cita + hash de la transicion.

    Se almacena solo la forma permitida por ``String(150)``: nunca nombres,
    documentos, tokens ni texto clinico. Incluye la marca temporal efectiva de
    la transicion para distinguir transiciones legitimas posteriores.
    """
    canonica = "|".join(str(parte) for parte in partes)
    digest = hashlib.sha256(canonica.encode("utf-8")).hexdigest()
    return f"{tipo}:{int(cita_id)}:{digest}"


def _persistir_evento(
    db: Session,
    datos: CitaEventoPersistencia,
):
    """Inserta el evento una sola vez, respetando la constraint unica.

    Un ``IntegrityError`` solo se considera idempotente si la clave ya existe;
    cualquier otra violacion (FK, CHECK, etc.) se propaga para provocar el
    rollback de toda la operacion de citas.
    """
    existente = repo_eventos.obtener_por_clave(db, datos.clave_idempotencia)
    if existente is not None:
        return existente

    try:
        with db.begin_nested():
            return repo_eventos.registrar(db, datos)
    except IntegrityError:
        existente = repo_eventos.obtener_por_clave(
            db, datos.clave_idempotencia,
        )
        if existente is None:
            raise
        return existente


def _resolver_usuario_destinatario(db: Session, cita) -> int | None:
    """Deriva el destinatario desde Cita -> Paciente -> Usuario activo.

    Nunca acepta IDs del request. Devuelve ``None`` cuando no existe perfil de
    paciente, no tiene usuario asociado o la cuenta esta inactiva.
    """
    paciente_id = getattr(cita, "paciente_id", None)
    if not _es_entero_positivo(paciente_id):
        return None

    paciente = obtener_paciente_por_id(db, paciente_id)
    if paciente is None or not getattr(paciente, "estado", False):
        return None

    usuario_id = getattr(paciente, "usuario_id", None)
    if not _es_entero_positivo(usuario_id):
        return None

    usuario = db.get(Usuario, usuario_id)
    if usuario is None or not getattr(usuario, "estado", False):
        return None

    return usuario_id


def resolver_usuario_destinatario(db: Session, cita) -> int | None:
    """Puerto publico reutilizado por productores internos como 3C.6."""
    return _resolver_usuario_destinatario(db, cita)


def _crear_notificacion(
    db: Session,
    *,
    cita,
    usuario_id: int,
    tipo: str,
    titulo: str,
    cuerpo: str,
    evento_clave: str,
):
    """Crea la notificacion una sola vez por evento (constraint de 3C.2)."""
    existente = repo_notificaciones.obtener_por_evento_clave(
        db, evento_clave,
    )
    if existente is not None:
        return existente

    datos = NotificacionPersistencia(
        usuario_id=usuario_id,
        tipo=tipo,
        evento_clave=evento_clave,
        titulo=titulo,
        cuerpo=cuerpo,
        datos={
            "ruta": _RUTA_NAVEGACION,
            "pantalla": _PANTALLA_NAVEGACION,
            "seccion": _SECCION_NAVEGACION,
            "accion": _ACCION_NAVEGACION,
            "cita_id": cita.id,
        },
        cita_id=cita.id,
    )

    try:
        with db.begin_nested():
            return repo_notificaciones.crear(db, datos)
    except IntegrityError:
        existente = repo_notificaciones.obtener_por_evento_clave(
            db, evento_clave,
        )
        if existente is None:
            raise
        return existente


def _notificar_si_cuenta_activa(
    db: Session,
    *,
    cita,
    tipo: str,
    titulo: str,
    cuerpo: str,
    evento_clave: str,
):
    usuario_id = _resolver_usuario_destinatario(db, cita)
    if usuario_id is None:
        # Paciente sin cuenta activa: se conserva el evento, sin destinatario.
        return None
    notificacion = _crear_notificacion(
        db,
        cita=cita,
        usuario_id=usuario_id,
        tipo=tipo,
        titulo=titulo,
        cuerpo=cuerpo,
        evento_clave=evento_clave,
    )
    # Fase 3C.5: outbox por dispositivo activo elegible, en la misma
    # transaccion tenant. Si la persistencia falla, la excepcion se propaga y
    # el caso de uso revierte todo el evento.
    entregas_push.encolar_entregas(db, notificacion)
    return notificacion


def registrar_evento_cita_creada(db: Session, cita, usuario):
    """Registra ``CITA_CREADA`` y la notificacion de bandeja."""
    clave = _clave_idempotencia(
        TIPO_EVENTO_CREADA,
        cita.id,
        (
            "creada",
            _texto_instante(getattr(cita, "fecha_registro", None)),
        ),
    )
    evento = _persistir_evento(
        db,
        CitaEventoPersistencia(
            cita_id=cita.id,
            tipo=TIPO_EVENTO_CREADA,
            estado_nuevo=getattr(cita, "estado", None),
            actor_usuario_id=getattr(usuario, "id", None),
            clave_idempotencia=clave,
        ),
    )
    _notificar_si_cuenta_activa(
        db,
        cita=cita,
        tipo=TIPO_EVENTO_CREADA,
        titulo=TITULO_CITA_CREADA,
        cuerpo=CUERPO_CITA_CREADA,
        evento_clave=evento.clave_idempotencia,
    )
    return evento


def registrar_evento_cita_reprogramada(
    db: Session,
    cita,
    *,
    fecha_anterior: date,
    hora_anterior: time,
    fecha_nueva: date,
    hora_nueva: time,
    usuario,
):
    """Registra ``CITA_REPROGRAMADA`` solo cuando cambia fecha u hora."""
    clave = _clave_idempotencia(
        TIPO_EVENTO_REPROGRAMADA,
        cita.id,
        (
            "reprogramada",
            _texto_instante(fecha_anterior),
            _texto_instante(hora_anterior),
            _texto_instante(fecha_nueva),
            _texto_instante(hora_nueva),
            _texto_instante(getattr(cita, "fecha_actualizacion", None)),
        ),
    )
    evento = _persistir_evento(
        db,
        CitaEventoPersistencia(
            cita_id=cita.id,
            tipo=TIPO_EVENTO_REPROGRAMADA,
            fecha_anterior=fecha_anterior,
            hora_anterior=hora_anterior,
            fecha_nueva=fecha_nueva,
            hora_nueva=hora_nueva,
            actor_usuario_id=getattr(usuario, "id", None),
            clave_idempotencia=clave,
        ),
    )
    _notificar_si_cuenta_activa(
        db,
        cita=cita,
        tipo=TIPO_EVENTO_REPROGRAMADA,
        titulo=TITULO_CITA_REPROGRAMADA,
        cuerpo=CUERPO_CITA_REPROGRAMADA,
        evento_clave=evento.clave_idempotencia,
    )
    return evento


def _tipo_notificacion_estado(estado_nuevo: str) -> str:
    if estado_nuevo == "CANCELADA":
        return "CITA_CANCELADA"
    if estado_nuevo == "CONFIRMADA":
        return "CITA_CONFIRMADA"
    return "ACTUALIZACION_OPERATIVA"


def registrar_evento_cita_estado(
    db: Session,
    cita,
    *,
    estado_anterior: str,
    estado_nuevo: str,
    usuario,
):
    """Registra ``CITA_ESTADO_CAMBIADO`` y la notificacion generica."""
    clave = _clave_idempotencia(
        TIPO_EVENTO_ESTADO,
        cita.id,
        (
            "estado",
            estado_anterior,
            estado_nuevo,
            _texto_instante(getattr(cita, "fecha_actualizacion", None)),
        ),
    )
    evento = _persistir_evento(
        db,
        CitaEventoPersistencia(
            cita_id=cita.id,
            tipo=TIPO_EVENTO_ESTADO,
            estado_anterior=estado_anterior,
            estado_nuevo=estado_nuevo,
            actor_usuario_id=getattr(usuario, "id", None),
            clave_idempotencia=clave,
        ),
    )
    _notificar_si_cuenta_activa(
        db,
        cita=cita,
        tipo=_tipo_notificacion_estado(estado_nuevo),
        titulo=TITULO_ESTADO_CITA,
        cuerpo=CUERPO_ESTADO_CITA,
        evento_clave=evento.clave_idempotencia,
    )
    return evento
