from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Oftalmologo
from app.modules.gestion_historial_clinico.models.controles import ControlMedico
from app.modules.gestion_historial_clinico.schemas.controles import (
    ControlMedicoActualizar,
    ControlMedicoCrear,
)


def obtener_oftalmologo_activo_por_usuario_id(db: Session, usuario_id: int):
    return db.scalar(
        select(Oftalmologo).where(
            Oftalmologo.usuario_id == usuario_id,
            Oftalmologo.estado.is_(True),
        )
    )


def obtener_control_por_id(db: Session, control_id: int):
    return db.get(ControlMedico, control_id)


def listar_controles(
    db: Session,
    *,
    paciente_id: int | None = None,
    consulta_clinica_id: int | None = None,
    estado: str | None = None,
):
    stmt = select(ControlMedico)
    if paciente_id is not None:
        stmt = stmt.where(ControlMedico.paciente_id == paciente_id)
    if consulta_clinica_id is not None:
        stmt = stmt.where(ControlMedico.consulta_clinica_id == consulta_clinica_id)
    if estado is not None:
        stmt = stmt.where(ControlMedico.estado == estado)
    return db.scalars(
        stmt.order_by(ControlMedico.fecha_programada.desc(), ControlMedico.id.desc())
    ).all()


def crear_control(
    db: Session,
    *,
    consulta_clinica_id: int,
    paciente_id: int,
    oftalmologo_id: int,
    datos: ControlMedicoCrear,
):
    # Estos IDs son obligatorios en la tabla existente y se derivan en service.
    control = ControlMedico(
        consulta_clinica_id=consulta_clinica_id,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
        fecha_programada=datos.fecha_programada,
        motivo=datos.motivo,
        observaciones=datos.observaciones,
        estado="PROGRAMADO",
    )
    db.add(control)
    db.flush()
    db.refresh(control)
    return control


def actualizar_control(
    db: Session,
    control: ControlMedico,
    datos: ControlMedicoActualizar,
):
    for campo, valor in datos.model_dump(exclude_unset=True).items():
        setattr(control, campo, valor)
    db.flush()
    db.refresh(control)
    return control
