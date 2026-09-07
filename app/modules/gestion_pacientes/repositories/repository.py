from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_pacientes.models.models import Paciente

from app.modules.gestion_pacientes.schemas.schemas import (
    PacienteCrear,
    PacienteActualizar,
)


def obtener_paciente_por_id(
    db: Session,
    paciente_id: int,
):
    return db.get(Paciente, paciente_id)


def obtener_paciente_por_usuario_id(
    db: Session,
    usuario_id: int,
):
    stmt = select(Paciente).where(Paciente.usuario_id == usuario_id)
    return db.scalar(stmt)


def obtener_paciente_por_ci(
    db: Session,
    ci: str,
):
    stmt = select(Paciente).where(
        Paciente.ci == ci
    )

    return db.scalar(stmt)


def listar_pacientes(db: Session):
    stmt = select(Paciente).order_by(
        Paciente.apellidos,
        Paciente.nombres,
    )

    return db.scalars(stmt).all()


def crear_paciente(
    db: Session,
    datos: PacienteCrear,
):
    paciente = Paciente(
        **datos.model_dump(),
        fecha_registro=datetime.now(timezone.utc),
    )

    db.add(paciente)
    db.flush()
    db.refresh(paciente)

    return paciente


def actualizar_paciente(
    db: Session,
    paciente: Paciente,
    datos: PacienteActualizar,
):
    cambios = datos.model_dump(
        exclude_unset=True
    )

    for campo, valor in cambios.items():
        setattr(paciente, campo, valor)

    db.flush()
    db.refresh(paciente)

    return paciente


def eliminar_logicamente_paciente(
    db: Session,
    paciente: Paciente,
):
    paciente.estado = False

    db.flush()
    db.refresh(paciente)

    return paciente