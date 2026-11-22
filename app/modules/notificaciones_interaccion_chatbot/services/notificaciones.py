from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pacientes.repositories import repository as pacientes
from app.modules.notificaciones_interaccion_chatbot.models.notificacion import NotificacionPaciente

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
    paciente = _paciente_actual(db, usuario)
    filtro = NotificacionPaciente.paciente_id == paciente.id
    total = db.scalar(select(func.count()).select_from(NotificacionPaciente).where(filtro))
    items = db.scalars(select(NotificacionPaciente).where(filtro).order_by(
        NotificacionPaciente.fecha_creacion.desc(), NotificacionPaciente.id.desc()
    ).limit(limite).offset(desplazamiento)).all()
    return {"items": items, "total": total, "limite": limite, "desplazamiento": desplazamiento}

def marcar_leida(db: Session, usuario, notificacion_id: int):
    paciente = _paciente_actual(db, usuario)
    item = db.scalar(select(NotificacionPaciente).where(
        NotificacionPaciente.id == notificacion_id,
        NotificacionPaciente.paciente_id == paciente.id,
    ).with_for_update())
    if item is None:
        raise HTTPException(404, "Notificación no encontrada")
    if not item.leida:
        item.leida = True
        item.fecha_lectura = datetime.now(timezone.utc)
        db.commit()
        db.refresh(item)
    return item

def crear_desde_evento(db: Session, *, paciente_id: int, titulo: str, mensaje: str,
                       tipo: str, clave_evento: str, fecha_envio: datetime | None = None,
                       con_estado: bool = False):
    """Uso interno. La clave identifica un evento para un paciente dentro del tenant."""
    if not clave_evento or not clave_evento.strip():
        raise ValueError("clave_evento es obligatoria")
    filtro = (NotificacionPaciente.paciente_id == paciente_id,
              NotificacionPaciente.clave_evento == clave_evento)
    existente = db.scalar(select(NotificacionPaciente).where(*filtro))
    if existente is not None:
        return (existente, False) if con_estado else existente
    try:
        with db.begin_nested():
            item = NotificacionPaciente(paciente_id=paciente_id, titulo=titulo,
                mensaje=mensaje, tipo=tipo, clave_evento=clave_evento,
                fecha_envio=fecha_envio)
            db.add(item)
            db.flush()
        return (item, True) if con_estado else item
    except IntegrityError:
        existente = db.scalar(select(NotificacionPaciente).where(*filtro))
        if existente is None:
            raise
        return (existente, False) if con_estado else existente
