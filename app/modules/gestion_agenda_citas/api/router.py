from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_LECTURA, requerir_permiso
from app.database.session import get_db

from app.modules.gestion_agenda_citas.schemas.schemas import (
    AgendaRespuesta,
    DisponibilidadRespuesta,
    OftalmologoResumidoRespuesta,
)

from app.modules.gestion_agenda_citas.services import service


router = APIRouter(
    prefix="/agenda-citas",
    tags=["Agenda y Citas"]
)


permiso_consultar_agenda = requerir_permiso(
    "Consultar agenda y disponibilidad médica",
    ACCION_LECTURA,
)


@router.get("/")
def obtener_agenda():
    return {"mensaje": "Módulo de agenda y citas funcionando"}


# =========================================================
# CU09 - CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA
# =========================================================

@router.get(
    "/oftalmologos",
    response_model=list[OftalmologoResumidoRespuesta],
)
def listar_oftalmologos(
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_agenda),
):
    return service.listar_oftalmologos_activos(db, usuario=_usuario)


@router.get(
    "/disponibilidad",
    response_model=DisponibilidadRespuesta,
)
def obtener_disponibilidad(
    oftalmologo_id: int,
    fecha: date,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_agenda),
):
    return service.consultar_disponibilidad(
        db,
        oftalmologo_id,
        fecha,
    )


@router.get(
    "/oftalmologos/{oftalmologo_id}/agenda",
    response_model=AgendaRespuesta,
)
def obtener_agenda_por_oftalmologo(
    oftalmologo_id: int,
    fecha: date,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_agenda),
):
    return service.consultar_agenda(
        db,
        oftalmologo_id,
        fecha,
        usuario=_usuario,
    )
