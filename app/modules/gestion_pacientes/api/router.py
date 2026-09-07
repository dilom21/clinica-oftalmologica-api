from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user_id
from app.database.session import get_db

from app.modules.gestion_pacientes.schemas.schemas import (
    PacienteCrear,
    PacienteActualizar,
    PacienteRespuesta,
    MiPerfilPacienteActualizar,
    MiPerfilPacienteRespuesta,
)

from app.modules.gestion_pacientes.services import service


router = APIRouter(
    prefix="/pacientes",
    tags=["Pacientes"],
)


# =========================================================
# CU07 - GESTIONAR PACIENTES
# =========================================================

@router.post(
    "",
    response_model=PacienteRespuesta,
    status_code=201,
)
def crear_paciente(
    datos: PacienteCrear,
    db: Session = Depends(get_db),
):
    return service.crear_paciente(
        db,
        datos,
    )


@router.get(
    "",
    response_model=list[PacienteRespuesta],
)
def listar_pacientes(
    db: Session = Depends(get_db),
):
    return service.listar_pacientes(db)


@router.get(
    "/me",
    response_model=MiPerfilPacienteRespuesta,
)
def obtener_mi_perfil(
    usuario_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    return service.obtener_mi_perfil(db, usuario_id)


@router.put(
    "/me",
    response_model=MiPerfilPacienteRespuesta,
)
def actualizar_mi_perfil(
    datos: MiPerfilPacienteActualizar,
    usuario_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    return service.actualizar_mi_perfil(db, usuario_id, datos)


@router.get(
    "/{paciente_id}",
    response_model=PacienteRespuesta,
)
def obtener_paciente(
    paciente_id: int,
    db: Session = Depends(get_db),
):
    return service.obtener_paciente(
        db,
        paciente_id,
    )


@router.put(
    "/{paciente_id}",
    response_model=PacienteRespuesta,
)
def actualizar_paciente(
    paciente_id: int,
    datos: PacienteActualizar,
    db: Session = Depends(get_db),
):
    return service.actualizar_paciente(
        db,
        paciente_id,
        datos,
    )


@router.delete(
    "/{paciente_id}",
    response_model=PacienteRespuesta,
)
def eliminar_paciente(
    paciente_id: int,
    db: Session = Depends(get_db),
):
    return service.eliminar_paciente(
        db,
        paciente_id,
    )