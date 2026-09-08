from datetime import date

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.repositories import repository
from app.modules.gestion_agenda_citas.schemas.schemas import HistorialCitasRespuesta


def consultar_historial_citas(
	db: Session,
	paciente_id: int | None = None,
	nombre: str | None = None,
	codigo: str | None = None,
	identificacion: str | None = None,
	fecha_desde: date | None = None,
	fecha_hasta: date | None = None,
	estado: str | None = None,
) -> HistorialCitasRespuesta:
	if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="La fecha inicial no puede ser posterior a la fecha final",
		)

	paciente = repository.buscar_paciente(
		db,
		paciente_id=paciente_id,
		nombre=nombre,
		codigo=codigo,
		identificacion=identificacion,
	)
	if not paciente:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Paciente no encontrado",
		)

	citas = repository.listar_citas_paciente(
		db,
		paciente.id,
		fecha_desde=fecha_desde,
		fecha_hasta=fecha_hasta,
		estado=estado,
	)

	return HistorialCitasRespuesta(
		paciente=paciente,
		citas=citas,
		mensaje="El paciente no tiene citas registradas" if not citas else None,
	)
