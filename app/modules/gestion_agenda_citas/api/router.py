from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.dependencies import requerir_permiso
from app.database.session import get_db
from app.modules.gestion_agenda_citas.schemas.schemas import HistorialCitasRespuesta
from app.modules.gestion_agenda_citas.services import service

router = APIRouter(
    prefix="/agenda-citas",
    tags=["Agenda y Citas"]
)

@router.get("/")
def obtener_agenda():
    return {"mensaje": "Módulo de agenda y citas funcionando"}


@router.get(
    "/historial",
    response_model=HistorialCitasRespuesta,
    dependencies=[Depends(requerir_permiso("CONSULTAR_HISTORIAL_CITAS"))],
)
def consultar_historial_citas(
    paciente_id: int | None = Query(default=None),
    nombre: str | None = Query(default=None, min_length=1),
    codigo: str | None = Query(default=None, min_length=1),
    identificacion: str | None = Query(default=None, min_length=1),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None, min_length=1, max_length=20),
    db: Session = Depends(get_db),
):
    if paciente_id is None and not any((nombre, codigo, identificacion)):
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe indicar paciente_id, nombre, codigo o identificacion",
        )

    return service.consultar_historial_citas(
        db,
        paciente_id=paciente_id,
        nombre=nombre,
        codigo=codigo,
        identificacion=identificacion,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        estado=estado,
    )