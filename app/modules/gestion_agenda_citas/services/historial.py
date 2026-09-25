from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.repositories import historial as repo
from app.modules.gestion_agenda_citas.schemas.historial import HistorialCitasRespuesta
from app.modules.gestion_agenda_citas.services.citas import _validar_estado_cita


def consultar_historial_citas(
    db: Session,
    *,
    paciente_id: int | None = None,
    nombre: str | None = None,
    codigo: int | None = None,
    identificacion: str | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    estado: str | None = None,
) -> HistorialCitasRespuesta:
    nombre = (nombre or "").strip() or None
    identificacion = (identificacion or "").strip() or None

    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        raise HTTPException(
            status_code=400,
            detail="fecha_desde no puede ser posterior a fecha_hasta",
        )

    if paciente_id is None and codigo is None and not nombre and not identificacion:
        raise HTTPException(
            status_code=400,
            detail="Debe indicar paciente_id, nombre, codigo o identificacion",
        )

    estado_normalizado = _validar_estado_cita(estado) if estado is not None else None
    pacientes = repo.buscar_pacientes(
        db,
        paciente_id=paciente_id,
        nombre=nombre,
        codigo=codigo,
        identificacion=identificacion,
    )
    if not pacientes:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    if len(pacientes) > 1:
        raise HTTPException(
            status_code=409,
            detail="La búsqueda coincide con varios pacientes; precise el ID o la identificación",
        )

    paciente = pacientes[0]
    citas = repo.listar_citas_paciente(
        db,
        paciente.id,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        estado=estado_normalizado,
    )
    return HistorialCitasRespuesta(
        paciente=paciente,
        citas=citas,
        mensaje=(
            "El paciente no tiene citas registradas para los filtros indicados"
            if not citas else None
        ),
    )
