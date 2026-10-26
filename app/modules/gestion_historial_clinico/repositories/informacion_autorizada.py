"""Consultas de lectura de la información clínica que el paciente puede ver."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_agenda_citas.models.models import Oftalmologo
from app.modules.gestion_historial_clinico.models.controles import ControlMedico
from app.modules.gestion_historial_clinico.models.models import (
    ConsultaClinica,
    Diagnostico,
    ExamenOftalmologico,
    HistorialClinico,
    Receta,
    ResultadoExamen,
    Tratamiento,
)
from app.modules.gestion_historial_clinico.repositories.repository import (
    obtener_paciente_con_historial,
)

# Un control cancelado ya no forma parte de la información autorizada.
_ESTADOS_CONTROL_VISIBLES = ("PROGRAMADO", "REALIZADO")


def obtener_informacion_clinica_autorizada(db: Session, paciente_id: int):
    """Devuelve el historial activo del paciente y sus registros vigentes.

    Las consultas inactivas, los diagnósticos, tratamientos, recetas, exámenes
    y resultados inactivos, y los controles cancelados no se incluyen.
    """
    _paciente, historial = obtener_paciente_con_historial(db, paciente_id)
    if historial is None:
        return None, [], [], []

    consultas = db.scalars(
        select(ConsultaClinica)
        .options(
            selectinload(ConsultaClinica.oftalmologo),
            selectinload(
                ConsultaClinica.tratamientos.and_(Tratamiento.estado.is_(True))
            ),
            selectinload(ConsultaClinica.indicaciones),
            selectinload(
                ConsultaClinica.recetas.and_(Receta.estado.is_(True))
            ).selectinload(Receta.detalles),
            selectinload(
                ConsultaClinica.examenes.and_(
                    ExamenOftalmologico.estado.is_(True)
                )
            ).selectinload(
                ExamenOftalmologico.resultados.and_(
                    ResultadoExamen.estado.is_(True)
                )
            ),
        )
        .where(
            ConsultaClinica.historial_clinico_id == historial.id,
            ConsultaClinica.estado.is_(True),
        )
        .order_by(
            ConsultaClinica.fecha_consulta.desc(),
            ConsultaClinica.id.desc(),
        )
        .execution_options(populate_existing=True)
    ).all()

    consulta_ids = [consulta.id for consulta in consultas]
    diagnosticos = []
    if consulta_ids:
        diagnosticos = db.scalars(
            select(Diagnostico)
            .where(
                Diagnostico.consulta_clinica_id.in_(consulta_ids),
                Diagnostico.estado.is_(True),
            )
            .order_by(
                Diagnostico.fecha_diagnostico.desc(),
                Diagnostico.id.desc(),
            )
        ).all()

    controles = db.execute(
        select(ControlMedico, Oftalmologo)
        .join(Oftalmologo, Oftalmologo.id == ControlMedico.oftalmologo_id)
        .where(
            ControlMedico.paciente_id == paciente_id,
            or_(
                ControlMedico.estado.is_(None),
                ControlMedico.estado.in_(_ESTADOS_CONTROL_VISIBLES),
            ),
        )
        .order_by(
            ControlMedico.fecha_programada.desc(),
            ControlMedico.id.desc(),
        )
    ).all()

    return historial, consultas, diagnosticos, controles


def antecedentes_autorizados(historial: HistorialClinico):
    """Antecedentes ya filtrados por el historial activo."""
    return [
        antecedente
        for antecedente in historial.antecedentes
        if antecedente.estado is True
    ]
