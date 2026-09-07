from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import Cita
from app.modules.gestion_pacientes.models.models import Paciente


def buscar_paciente(
    db: Session,
    paciente_id: int | None = None,
    nombre: str | None = None,
    codigo: str | None = None,
    identificacion: str | None = None,
) -> Paciente | None:
    if paciente_id is not None:
        return db.get(Paciente, paciente_id)

    terminos = [valor.strip() for valor in (nombre, codigo, identificacion) if valor]
    if not terminos:
        return None

    condiciones = []
    for termino in terminos:
        patron = f"%{termino}%"
        condiciones.append(
            or_(
                Paciente.nombres.ilike(patron),
                Paciente.apellidos.ilike(patron),
                Paciente.ci.ilike(patron),
                cast(Paciente.id, Integer) == termino if termino.isdigit() else False,
            )
        )

    return db.scalar(
        select(Paciente)
        .where(or_(*condiciones))
        .order_by(Paciente.apellidos, Paciente.nombres)
        .limit(1)
    )


def listar_citas_paciente(
    db: Session,
    paciente_id: int,
    fecha_desde=None,
    fecha_hasta=None,
    estado: str | None = None,
) -> list[Cita]:
    consulta = select(Cita).where(Cita.paciente_id == paciente_id)

    if fecha_desde is not None:
        consulta = consulta.where(Cita.fecha >= fecha_desde)
    if fecha_hasta is not None:
        consulta = consulta.where(Cita.fecha <= fecha_hasta)
    if estado is not None:
        consulta = consulta.where(func.upper(Cita.estado) == estado.upper())

    return list(
        db.scalars(
            consulta.order_by(Cita.fecha.asc(), Cita.hora_inicio.asc(), Cita.id.asc())
        ).all()
    )