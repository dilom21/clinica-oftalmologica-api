from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.session import get_db

from app.modules.gestion_pacientes.schemas.schemas import (
    PacienteCrear,
    PacienteActualizar,
    PacienteRespuesta,
    AntecedenteClinicoCrear,
    AntecedenteClinicoActualizar,
    AntecedenteClinicoRespuesta,
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

# =========================================================
# CU14 - GESTIONAR ANTECEDENTES CLÍNICOS
# =========================================================

@router.get(
    "/historial/{historial_clinico_id}/antecedentes",
    response_model=list[AntecedenteClinicoRespuesta],
)
def listar_antecedentes(
    historial_clinico_id: int,
    db: Session = Depends(get_db),
):
    return service.listar_antecedentes_por_historial(
        db,
        historial_clinico_id,
    )


@router.post(
    "/antecedentes",
    response_model=AntecedenteClinicoRespuesta,
    status_code=201,
)
def crear_antecedente(
    datos: AntecedenteClinicoCrear,
    db: Session = Depends(get_db),
):
    return service.crear_antecedente(
        db,
        datos,
    )


@router.put(
    "/antecedentes/{antecedente_id}",
    response_model=AntecedenteClinicoRespuesta,
)
def actualizar_antecedente(
    antecedente_id: int,
    datos: AntecedenteClinicoActualizar,
    db: Session = Depends(get_db),
):
    return service.actualizar_antecedente(
        db,
        antecedente_id,
        datos,
    )