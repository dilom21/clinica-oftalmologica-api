from datetime import date

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
