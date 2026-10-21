from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_ESCRITURA, ACCION_LECTURA, get_db, requerir_permiso
from app.modules.gestion_agenda_citas.schemas.schemas import OftalmologoResumidoRespuesta
from app.modules.gestion_historial_clinico.schemas.controles import (
    ControlMedicoActualizar,
    ControlMedicoCrear,
    ControlMedicoRespuesta,
    EstadoControl,
)
from app.modules.gestion_historial_clinico.services import controles as service


router = APIRouter()

permiso_consultar_controles = requerir_permiso("Consultar historial clínico", ACCION_LECTURA)
permiso_programar_controles = requerir_permiso("Programar controles médicos", ACCION_ESCRITURA)


@router.post(
    "/consultas/{consulta_id}/controles",
    response_model=ControlMedicoRespuesta,
    status_code=201,
)
def programar_control(
    datos: ControlMedicoCrear,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_programar_controles),
):
    return service.programar_control(db, consulta_id, datos, usuario)


@router.get(
    "/consultas/{consulta_id}/controles", response_model=list[ControlMedicoRespuesta],
)
def listar_controles_por_consulta(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_controles),
):
    return service.listar_controles_por_consulta(db, consulta_id)


@router.get("/controles", response_model=list[ControlMedicoRespuesta])
def listar_controles(
    paciente_id: int | None = Query(default=None, gt=0, le=2**63 - 1),
    consulta_clinica_id: int | None = Query(default=None, gt=0, le=2**63 - 1),
    estado: EstadoControl | None = Query(default=None),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_controles),
):
    return service.listar_controles(
        db,
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
        estado=estado,
    )


@router.get(
    "/controles/oftalmologo-actual", response_model=OftalmologoResumidoRespuesta | None,
)
def obtener_oftalmologo_actual(
    db: Session = Depends(get_db),
    usuario=Depends(permiso_consultar_controles),
):
    return service.obtener_oftalmologo_actual(db, usuario)


@router.get("/controles/{control_id}", response_model=ControlMedicoRespuesta)
def consultar_control(
    control_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_controles),
):
    return service.consultar_control(db, control_id)


@router.put("/controles/{control_id}", response_model=ControlMedicoRespuesta)
def actualizar_control(
    datos: ControlMedicoActualizar,
    control_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_programar_controles),
):
    return service.actualizar_control(db, control_id, datos, usuario)
