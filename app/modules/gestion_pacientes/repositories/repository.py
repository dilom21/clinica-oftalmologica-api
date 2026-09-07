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

from app.modules.gestion_pacientes.models.models import (
    HistorialClinico,
    AntecedenteClinico,
)
from app.modules.gestion_pacientes.schemas.schemas import (
    AntecedenteClinicoCrear,
    AntecedenteClinicoActualizar,
)


def obtener_historial_por_paciente_id(
    db: Session,
    paciente_id: int,
):
    stmt = select(HistorialClinico).where(
        HistorialClinico.paciente_id == paciente_id
    )
    return db.scalar(stmt)


def listar_antecedentes_por_historial(
    db: Session,
    historial_clinico_id: int,
):
    stmt = select(AntecedenteClinico).where(
        AntecedenteClinico.historial_clinico_id == historial_clinico_id,
        AntecedenteClinico.estado == True
    ).order_by(AntecedenteClinico.fecha_registro.desc())

    return db.scalars(stmt).all()


def obtener_antecedente_por_id(
    db: Session,
    antecedente_id: int,
):
    return db.get(AntecedenteClinico, antecedente_id)


def crear_antecedente(
    db: Session,
    datos: AntecedenteClinicoCrear,
):
    antecedente = AntecedenteClinico(
        **datos.model_dump(),
        fecha_registro=datetime.now(timezone.utc),
    )
    if antecedente.tipo:
        antecedente.tipo = antecedente.tipo.upper()

    db.add(antecedente)
    db.flush()
    db.refresh(antecedente)

    return antecedente


def actualizar_antecedente(
    db: Session,
    antecedente: AntecedenteClinico,
    datos: AntecedenteClinicoActualizar,
):
    cambios = datos.model_dump(exclude_unset=True)

    if "tipo" in cambios and cambios["tipo"] is not None:
        cambios["tipo"] = cambios["tipo"].upper()

    for campo, valor in cambios.items():
        setattr(antecedente, campo, valor)

    db.flush()
    db.refresh(antecedente)

    return antecedente