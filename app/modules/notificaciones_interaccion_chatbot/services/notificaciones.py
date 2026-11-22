from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pacientes.repositories import repository as pacientes
from app.modules.notificaciones_interaccion_chatbot.models.notificacion import Notificacion


TIPOS_V9 = frozenset({
    "CITA_CREADA", "CITA_CONFIRMADA", "CITA_REPROGRAMADA", "CITA_CANCELADA",
    "RECORDATORIO_CITA", "ACTUALIZACION_OPERATIVA",
})
SUBTIPOS_OPERATIVOS = frozenset({"CONTROL_PROGRAMADO", "RECORDATORIO_CONTROL"})


def _paciente_actual(db: Session, usuario):
    if nombre_rol_actual(usuario) != "paciente":
        raise HTTPException(403, "Se requiere rol Paciente")
    paciente = pacientes.obtener_paciente_por_usuario_id(db, usuario.id)
    if paciente is None:
        raise HTTPException(404, "Perfil de paciente no encontrado")
    if not paciente.estado:
        raise HTTPException(403, "Perfil de paciente inactivo")
    return paciente


def listar_propias(db: Session, usuario, limite: int, desplazamiento: int):
    _paciente_actual(db, usuario)
    filtro = Notificacion.usuario_id == usuario.id
    total = db.scalar(select(func.count()).select_from(Notificacion).where(filtro))
    items = db.scalars(select(Notificacion).where(filtro).order_by(
        Notificacion.fecha_creacion.desc(), Notificacion.id.desc()
    ).limit(limite).offset(desplazamiento)).all()
    return {"items": items, "total": total, "limite": limite, "desplazamiento": desplazamiento}


def marcar_leida(db: Session, usuario, notificacion_id: int):
    _paciente_actual(db, usuario)
    item = db.scalar(select(Notificacion).where(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario.id,
    ).with_for_update())
    if item is None:
        raise HTTPException(404, "Notificación no encontrada")
    if not item.leida:
        ahora = datetime.now(timezone.utc)
        item.leida = True
        item.fecha_lectura = ahora
        item.fecha_actualizacion = ahora
        db.commit()
        db.refresh(item)
    return item


def crear_desde_evento(db: Session, *, paciente_id: int, titulo: str, mensaje: str,
                       tipo: str, clave_evento: str, fecha_envio: datetime | None = None,
                       con_estado: bool = False, cita_id: int | None = None):
    """Crea en la sesión del tenant; el commit pertenece a la operación llamadora."""
    del fecha_envio  # v9 no almacena fecha de envío.
    if not clave_evento or len(clave_evento) > 150 or not clave_evento.strip():
        raise ValueError("clave_evento debe tener entre 1 y 150 caracteres")
    if not titulo or len(titulo) > 150 or not mensaje:
        raise ValueError("titulo y mensaje son obligatorios; titulo admite hasta 150 caracteres")
    if tipo in SUBTIPOS_OPERATIVOS:
        datos = {"subtipo": tipo}
        tipo_v9 = "ACTUALIZACION_OPERATIVA"
    elif tipo in TIPOS_V9:
        datos = None
        tipo_v9 = tipo
    else:
        raise ValueError("tipo de notificación no admitido por v9")
    paciente = pacientes.obtener_paciente_por_id(db, paciente_id)
    if paciente is None:
        raise ValueError("Paciente destinatario inexistente")
    if not paciente.estado or paciente.usuario_id is None:
        # Un perfil sin cuenta móvil no puede recibir avisos en la tabla v9.
        return (None, False) if con_estado else None
    usuario_id = paciente.usuario_id
    filtro = Notificacion.evento_clave == clave_evento
    existente = db.scalar(select(Notificacion).where(filtro))
    if existente is not None:
        if existente.usuario_id != usuario_id:
            raise ValueError("La clave del evento pertenece a otro usuario")
        return (existente, False) if con_estado else existente
    try:
        with db.begin_nested():
            item = Notificacion(usuario_id=usuario_id, titulo=titulo,
                cuerpo=mensaje, tipo=tipo_v9, evento_clave=clave_evento,
                datos=datos, cita_id=cita_id)
            db.add(item)
            db.flush()
        return (item, True) if con_estado else item
    except IntegrityError:
        existente = db.scalar(select(Notificacion).where(filtro))
        if existente is None or existente.usuario_id != usuario_id:
            raise
        return (existente, False) if con_estado else existente
