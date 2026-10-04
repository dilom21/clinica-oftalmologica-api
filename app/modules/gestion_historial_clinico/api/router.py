from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_ESCRITURA, ACCION_LECTURA, requerir_permiso
from app.database.session import get_db
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
    AntecedenteClinicoRespuesta,
    ConsultaClinicaCrear,
    ConsultaClinicaRespuesta,
    DiagnosticoCrear,
    DiagnosticoRespuesta,
    HistorialClinicoRespuesta,
)
from app.modules.gestion_historial_clinico.services import service
from app.modules.gestion_historial_clinico.api.servicios_realizados import router as servicios_realizados_router

router = APIRouter(
    prefix="/historial-clinico",
    tags=["Historial Clínico"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de historial clínico funcionando"}


permiso_historial_clinico = requerir_permiso(
    "Consultar historial clínico", ACCION_LECTURA,
)
permiso_gestionar_antecedentes = requerir_permiso(
    "Gestionar antecedentes clínicos", ACCION_ESCRITURA,
)


@router.post(
    "/antecedentes", response_model=AntecedenteClinicoRespuesta, status_code=201,
)
def crear_antecedente(
    datos: AntecedenteClinicoCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_gestionar_antecedentes),
):
    return service.crear_antecedente(db, datos, usuario)


@router.put("/antecedentes/{antecedente_id}", response_model=AntecedenteClinicoRespuesta)
def actualizar_antecedente(
    datos: AntecedenteClinicoActualizar,
    antecedente_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_gestionar_antecedentes),
):
    return service.actualizar_antecedente(db, antecedente_id, datos, usuario)


# =========================================================
# CU15 - REGISTRAR CONSULTA CLÍNICA
# Función: "Registrar consulta clínica"
# =========================================================

permiso_registrar_consulta = requerir_permiso(
    "Registrar consulta clínica", ACCION_ESCRITURA,
)


@router.post(
    "/consultas", response_model=ConsultaClinicaRespuesta, status_code=201,
)
def registrar_consulta_clinica(
    datos: ConsultaClinicaCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_consulta),
):
    return service.registrar_consulta_clinica(db, datos, usuario)


@router.get("/consultas", response_model=list[ConsultaClinicaRespuesta])
def listar_consultas_clinicas(
    historial_clinico_id: int | None = Query(default=None, gt=0, le=2**63 - 1),
    paciente_id: int | None = Query(default=None, gt=0, le=2**63 - 1),
    oftalmologo_id: int | None = Query(default=None, gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_consultas_clinicas(
        db,
        historial_clinico_id=historial_clinico_id,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
    )


@router.get(
    "/consultas/{consulta_id}", response_model=ConsultaClinicaRespuesta,
)
def consultar_consulta_clinica(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.consultar_consulta_clinica(db, consulta_id)


router.include_router(servicios_realizados_router)


@router.get("/{paciente_id}", response_model=HistorialClinicoRespuesta)
def consultar_historial_clinico(
    paciente_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.consultar_historial_clinico(db, paciente_id)


# =========================================================
# CU16 - REGISTRAR DIAGNÓSTICO
# Función: "Registrar diagnóstico"
# =========================================================

permiso_registrar_diagnostico = requerir_permiso(
    "Registrar diagnóstico", ACCION_ESCRITURA,
)


@router.post(
    "/consultas/{consulta_id}/diagnosticos",
    response_model=DiagnosticoRespuesta,
    status_code=201,
)
def registrar_diagnostico(
    datos: DiagnosticoCrear,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_diagnostico),
):
    return service.registrar_diagnostico(db, consulta_id, datos, usuario)


@router.get(
    "/consultas/{consulta_id}/diagnosticos",
    response_model=list[DiagnosticoRespuesta],
)
def listar_diagnosticos(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_diagnosticos(db, consulta_id)
