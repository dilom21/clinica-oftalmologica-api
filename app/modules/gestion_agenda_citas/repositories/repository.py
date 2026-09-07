from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import (
    BloqueoHorario,
    Cita,
    HorarioOftalmologo,
    Oftalmologo,
)


# =========================================================
# CU09 - CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA
# =========================================================

ESTADOS_CITA_CANCELADA = ("CANCELADA",)


def obtener_oftalmologo_por_id(
    db: Session,
    oftalmologo_id: int,
):
    return db.get(Oftalmologo, oftalmologo_id)


def listar_oftalmologos_activos(db: Session):
    stmt = (
        select(Oftalmologo)
        .where(Oftalmologo.estado.is_(True))
        .order_by(
            Oftalmologo.apellidos,
            Oftalmologo.nombres,
        )
    )

    return db.scalars(stmt).all()


def obtener_oftalmologo_activo_por_id(
    db: Session,
    oftalmologo_id: int,
):
    stmt = (
        select(Oftalmologo)
        .where(
            Oftalmologo.id == oftalmologo_id,
            Oftalmologo.estado.is_(True),
        )
    )

    return db.scalar(stmt)


def obtener_horarios_por_oftalmologo_y_dia(
    db: Session,
    oftalmologo_id: int,
    dia_semana: int,
):
    stmt = (
        select(HorarioOftalmologo)
        .where(
            HorarioOftalmologo.oftalmologo_id == oftalmologo_id,
            HorarioOftalmologo.dia_semana == dia_semana,
            HorarioOftalmologo.estado.is_(True),
        )
        .order_by(
            HorarioOftalmologo.hora_inicio,
            HorarioOftalmologo.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_bloqueos_por_oftalmologo_y_fecha(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
):
    stmt = (
        select(BloqueoHorario)
        .where(
            BloqueoHorario.oftalmologo_id == oftalmologo_id,
            BloqueoHorario.fecha == fecha,
            BloqueoHorario.estado.is_(True),
        )
        .order_by(
            BloqueoHorario.hora_inicio,
            BloqueoHorario.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_citas_por_oftalmologo_y_fecha(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
):
    stmt = (
        select(Cita)
        .where(
            Cita.oftalmologo_id == oftalmologo_id,
            Cita.fecha == fecha,
            Cita.estado.notin_(ESTADOS_CITA_CANCELADA),
        )
        .order_by(
            Cita.hora_inicio,
            Cita.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_citas_futuras_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
    desde: date,
):
    """Citas desde `desde` (inclusive) cuyo estado ocupa horario (CU11)."""
    stmt = (
        select(Cita)
        .where(
            Cita.oftalmologo_id == oftalmologo_id,
            Cita.fecha >= desde,
            Cita.estado.notin_(ESTADOS_CITA_CANCELADA),
        )
        .order_by(
            Cita.fecha,
            Cita.hora_inicio,
            Cita.id,
        )
    )

    return db.scalars(stmt).all()


# =========================================================
# CU11 - CONFIGURAR DISPONIBILIDAD DEL OFTALMÓLOGO
# =========================================================

def obtener_horario_por_id(
    db: Session,
    horario_id: int,
):
    return db.get(HorarioOftalmologo, horario_id)


def obtener_bloqueo_por_id(
    db: Session,
    bloqueo_id: int,
):
    return db.get(BloqueoHorario, bloqueo_id)


def obtener_horarios_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
):
    """Todos los horarios (activos e inactivos) de un oftalmólogo."""
    stmt = (
        select(HorarioOftalmologo)
        .where(HorarioOftalmologo.oftalmologo_id == oftalmologo_id)
        .order_by(
            HorarioOftalmologo.dia_semana,
            HorarioOftalmologo.hora_inicio,
            HorarioOftalmologo.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_horarios_activos_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
):
    """Horarios activos de cualquier día (para simular cambios semanales)."""
    stmt = (
        select(HorarioOftalmologo)
        .where(
            HorarioOftalmologo.oftalmologo_id == oftalmologo_id,
            HorarioOftalmologo.estado.is_(True),
        )
        .order_by(
            HorarioOftalmologo.dia_semana,
            HorarioOftalmologo.hora_inicio,
            HorarioOftalmologo.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_bloqueos_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
):
    """Todos los bloqueos (activos e inactivos) de un oftalmólogo."""
    stmt = (
        select(BloqueoHorario)
        .where(BloqueoHorario.oftalmologo_id == oftalmologo_id)
        .order_by(
            BloqueoHorario.fecha,
            BloqueoHorario.hora_inicio,
            BloqueoHorario.id,
        )
    )

    return db.scalars(stmt).all()


def crear_horario_oftalmologo(
    db: Session,
    oftalmologo_id: int,
    dia_semana: int,
    hora_inicio,
    hora_fin,
    estado: bool = True,
):
    horario = HorarioOftalmologo(
        oftalmologo_id=oftalmologo_id,
        dia_semana=dia_semana,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        estado=estado,
    )

    db.add(horario)
    db.flush()
    db.refresh(horario)

    return horario


def actualizar_horario_oftalmologo(
    db: Session,
    horario: HorarioOftalmologo,
    dia_semana: int,
    hora_inicio,
    hora_fin,
):
    horario.dia_semana = dia_semana
    horario.hora_inicio = hora_inicio
    horario.hora_fin = hora_fin

    db.flush()
    db.refresh(horario)

    return horario


def cambiar_estado_horario_oftalmologo(
    db: Session,
    horario: HorarioOftalmologo,
    estado: bool,
):
    horario.estado = estado

    db.flush()
    db.refresh(horario)

    return horario


def crear_bloqueo_horario(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
    hora_inicio,
    hora_fin,
    motivo: str | None = None,
    estado: bool = True,
):
    bloqueo = BloqueoHorario(
        oftalmologo_id=oftalmologo_id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        motivo=motivo,
        estado=estado,
        fecha_registro=datetime.now(timezone.utc),
    )

    db.add(bloqueo)
    db.flush()
    db.refresh(bloqueo)

    return bloqueo


def actualizar_bloqueo_horario(
    db: Session,
    bloqueo: BloqueoHorario,
    fecha: date,
    hora_inicio,
    hora_fin,
    motivo: str | None = None,
):
    bloqueo.fecha = fecha
    bloqueo.hora_inicio = hora_inicio
    bloqueo.hora_fin = hora_fin
    bloqueo.motivo = motivo

    db.flush()
    db.refresh(bloqueo)

    return bloqueo


def cambiar_estado_bloqueo_horario(
    db: Session,
    bloqueo: BloqueoHorario,
    estado: bool,
):
    bloqueo.estado = estado

    db.flush()
    db.refresh(bloqueo)

    return bloqueo
