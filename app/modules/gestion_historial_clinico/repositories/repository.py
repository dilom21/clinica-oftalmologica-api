from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import (
    AntecedenteClinico,
    HistorialClinico,
)
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
)
from app.modules.gestion_pacientes.repositories.repository import obtener_paciente_por_id


def obtener_paciente_con_historial(db: Session, paciente_id: int):
    paciente = obtener_paciente_por_id(db, paciente_id)
    if paciente is None:
        return None, None

    historial = db.scalar(
        select(HistorialClinico)
        .options(
            selectinload(
                HistorialClinico.antecedentes.and_(AntecedenteClinico.estado.is_(True))
            )
        )
        .where(
            HistorialClinico.paciente_id == paciente_id,
            HistorialClinico.estado.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    return paciente, historial


def obtener_historial_activo_por_id(db: Session, historial_clinico_id: int):
    return db.scalar(
        select(HistorialClinico).where(
            HistorialClinico.id == historial_clinico_id,
            HistorialClinico.estado.is_(True),
        )
    )


def obtener_antecedente_disponible_por_id(db: Session, antecedente_id: int):
    return db.scalar(
        select(AntecedenteClinico)
        .join(HistorialClinico, HistorialClinico.id == AntecedenteClinico.historial_clinico_id)
        .where(
            AntecedenteClinico.id == antecedente_id,
            AntecedenteClinico.estado.is_(True),
            HistorialClinico.estado.is_(True),
        )
    )


def crear_antecedente(db: Session, datos: AntecedenteClinicoCrear):
    antecedente = AntecedenteClinico(
        historial_clinico_id=datos.historial_clinico_id,
        tipo=datos.tipo,
        descripcion=datos.descripcion,
        fecha_registro=datetime.now(timezone.utc),
        estado=True,
    )
    db.add(antecedente)
    db.flush()
    db.refresh(antecedente)
    return antecedente


def actualizar_antecedente(
    db: Session,
    antecedente: AntecedenteClinico,
    datos: AntecedenteClinicoActualizar,
):
    antecedente.tipo = datos.tipo
    antecedente.descripcion = datos.descripcion
    db.flush()
    db.refresh(antecedente)
    return antecedente
