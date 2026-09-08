from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_pacientes.models.models import Paciente


def buscar_pacientes(
    db: Session,
    *,
    paciente_id: int | None = None,
    nombre: str | None = None,
    codigo: int | None = None,
    identificacion: str | None = None,
) -> list[Paciente]:
    """Combina los filtros; dos resultados bastan para detectar ambigüedad.

    Paciente no tiene una columna codigo: se consulta su ID bigint real.
    Los nombres admiten coincidencias parciales, el CI es exacto.
    """
    consulta = select(Paciente)
    if paciente_id is not None:
        consulta = consulta.where(Paciente.id == paciente_id)
    if codigo is not None:
        consulta = consulta.where(Paciente.id == codigo)
    if nombre:
        nombre_completo = Paciente.nombres + " " + Paciente.apellidos
        for termino in nombre.split():
            consulta = consulta.where(
                nombre_completo.icontains(termino, autoescape=True)
            )
    if identificacion:
        consulta = consulta.where(Paciente.ci == identificacion)

    return list(db.scalars(consulta.order_by(Paciente.id).limit(2)).all())


def listar_citas_paciente(
    db: Session,
    paciente_id: int,
    *,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    estado: str | None = None,
) -> list[Cita]:
    consulta = select(Cita).where(Cita.paciente_id == paciente_id)
    if fecha_desde is not None:
        consulta = consulta.where(Cita.fecha >= fecha_desde)
    if fecha_hasta is not None:
        consulta = consulta.where(Cita.fecha <= fecha_hasta)
    if estado is not None:
        consulta = consulta.where(func.upper(Cita.estado) == estado)

    return list(db.scalars(consulta.order_by(
        Cita.fecha, Cita.hora_inicio, Cita.id,
    )).all())
