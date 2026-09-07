from datetime import date

from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.casos_uso.cu12_consultar_historial_citas import (
    consultar_historial_citas as ejecutar_consulta_historial_citas,
)
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
    return ejecutar_consulta_historial_citas(
        db,
        paciente_id=paciente_id,
        nombre=nombre,
        codigo=codigo,
        identificacion=identificacion,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        estado=estado,
    )
