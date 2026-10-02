from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import (
    AntecedenteClinico,
    ConsultaClinica,
    Diagnostico,
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


def obtener_paciente_activo_por_id(db: Session, paciente_id: int):
    """Paciente existente y activo (o `None`).

    Reutiliza `obtener_paciente_por_id` del módulo de pacientes para no
    duplicar la resolución del registro.
    """
    paciente = obtener_paciente_por_id(db, paciente_id)
    if paciente is None or not paciente.estado:
        return None
    return paciente


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


# =========================================================
# CU15 - REGISTRAR CONSULTA CLÍNICA
# =========================================================


def crear_consulta_clinica(
    db: Session,
    *,
    historial_clinico_id: int,
    cita_id: int | None,
    oftalmologo_id: int,
    motivo_consulta: str | None,
    anamnesis: str | None,
    observaciones: str | None,
):
    consulta = ConsultaClinica(
        historial_clinico_id=historial_clinico_id,
        cita_id=cita_id,
        oftalmologo_id=oftalmologo_id,
        fecha_consulta=datetime.now(timezone.utc),
        motivo_consulta=motivo_consulta,
        anamnesis=anamnesis,
        observaciones=observaciones,
        estado=True,
    )
    db.add(consulta)
    db.flush()
    db.refresh(consulta)
    return consulta


def obtener_consulta_por_id(db: Session, consulta_id: int):
    return db.scalar(
        select(ConsultaClinica)
        .options(selectinload(ConsultaClinica.oftalmologo))
        .where(ConsultaClinica.id == consulta_id)
    )


def obtener_consulta_por_cita_id(db: Session, cita_id: int):
    return db.scalar(
        select(ConsultaClinica).where(ConsultaClinica.cita_id == cita_id)
    )


def listar_consultas_clinicas(
    db: Session,
    *,
    historial_clinico_id: int | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
):
    stmt = select(ConsultaClinica).options(
        selectinload(ConsultaClinica.oftalmologo)
    )

    if paciente_id is not None:
        stmt = stmt.join(
            HistorialClinico,
            HistorialClinico.id == ConsultaClinica.historial_clinico_id,
        ).where(HistorialClinico.paciente_id == paciente_id)

    if historial_clinico_id is not None:
        stmt = stmt.where(
            ConsultaClinica.historial_clinico_id == historial_clinico_id
        )

    if oftalmologo_id is not None:
        stmt = stmt.where(ConsultaClinica.oftalmologo_id == oftalmologo_id)

    stmt = stmt.order_by(
        ConsultaClinica.fecha_consulta.desc(),
        ConsultaClinica.id.desc(),
    )

    return db.scalars(stmt).all()


# =========================================================
# CU16 - REGISTRAR DIAGNÓSTICO
# =========================================================


def obtener_consulta_activa_por_id(db: Session, consulta_id: int):
    return db.scalar(
        select(ConsultaClinica)
        .options(selectinload(ConsultaClinica.oftalmologo))
        .where(
            ConsultaClinica.id == consulta_id,
            ConsultaClinica.estado.is_(True),
        )
    )


def crear_diagnostico(
    db: Session,
    *,
    consulta_clinica_id: int,
    nombre: str,
    descripcion: str | None,
):
    diagnostico = Diagnostico(
        consulta_clinica_id=consulta_clinica_id,
        nombre=nombre,
        descripcion=descripcion,
        fecha_diagnostico=datetime.now(timezone.utc),
        estado=True,
    )
    db.add(diagnostico)
    db.flush()
    db.refresh(diagnostico)
    return diagnostico


def listar_diagnosticos_por_consulta(db: Session, consulta_clinica_id: int):
    stmt = (
        select(Diagnostico)
        .where(
            Diagnostico.consulta_clinica_id == consulta_clinica_id,
            Diagnostico.estado.is_(True),
        )
        .order_by(
            Diagnostico.fecha_diagnostico.desc(),
            Diagnostico.id.desc(),
        )
    )
    return db.scalars(stmt).all()
