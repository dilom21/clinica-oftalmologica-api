from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_ESCRITURA, ACCION_LECTURA, requerir_permiso
from app.database.session import get_db
from app.modules.gestion_historial_clinico.api.controles import router as controles_router
from app.modules.gestion_historial_clinico.api.servicios_realizados import (
    router as servicios_realizados_router,
)
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
    AntecedenteClinicoRespuesta,
    ConsultaClinicaCrear,
    ConsultaClinicaRespuesta,
    DiagnosticoCrear,
    DiagnosticoRespuesta,
    ExamenConResultadosRespuesta,
    ExamenOftalmologicoCrear,
    ExamenOftalmologicoRespuesta,
    HistorialClinicoRespuesta,
    IndicacionCrear,
    IndicacionRespuesta,
    RecetaCrear,
    RecetaRespuesta,
    ResultadoExamenCrear,
    ResultadoExamenRespuesta,
    TratamientoCrear,
    TratamientoRespuesta,
)
from app.modules.gestion_historial_clinico.services import service

router = APIRouter(
    prefix="/historial-clinico",
    tags=["Historial Clínico"]
)

router.include_router(controles_router)
router.include_router(servicios_realizados_router)

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


# =========================================================
# CU17 - REGISTRAR TRATAMIENTOS, INDICACIONES Y RECETAS
# Función: "Registrar tratamientos, indicaciones y recetas"
#
# POST: permiso de CU17 + ESCRITURA; además el Service exige rol Oftalmólogo
#       (dueño de la consulta) o Administrador (acceso total, sin exigir
#       perfil de oftalmólogo ni propiedad de la consulta).
# GET : "Consultar historial clínico" + LECTURA, para que CU13 pueda reutilizar
#       los listados sin exigir el permiso de registro.
# =========================================================

permiso_registrar_tratamientos = requerir_permiso(
    "Registrar tratamientos, indicaciones y recetas", ACCION_ESCRITURA,
)


@router.post(
    "/consultas/{consulta_id}/tratamientos",
    response_model=TratamientoRespuesta,
    status_code=201,
)
def registrar_tratamiento(
    datos: TratamientoCrear,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_tratamientos),
):
    return service.registrar_tratamiento(db, consulta_id, datos, usuario)


@router.get(
    "/consultas/{consulta_id}/tratamientos",
    response_model=list[TratamientoRespuesta],
)
def listar_tratamientos(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_tratamientos(db, consulta_id)


@router.post(
    "/consultas/{consulta_id}/indicaciones",
    response_model=IndicacionRespuesta,
    status_code=201,
)
def registrar_indicacion(
    datos: IndicacionCrear,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_tratamientos),
):
    return service.registrar_indicacion(db, consulta_id, datos, usuario)


@router.get(
    "/consultas/{consulta_id}/indicaciones",
    response_model=list[IndicacionRespuesta],
)
def listar_indicaciones(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_indicaciones(db, consulta_id)


@router.post(
    "/consultas/{consulta_id}/recetas",
    response_model=RecetaRespuesta,
    status_code=201,
)
def registrar_receta(
    datos: RecetaCrear,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_tratamientos),
):
    return service.registrar_receta(db, consulta_id, datos, usuario)


@router.get(
    "/consultas/{consulta_id}/recetas",
    response_model=list[RecetaRespuesta],
)
def listar_recetas(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_recetas(db, consulta_id)


@router.get("/recetas/{receta_id}", response_model=RecetaRespuesta)
def consultar_receta(
    receta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.consultar_receta(db, receta_id)


# =========================================================
# CU18 - REGISTRAR RESULTADOS DE EXAMENES OFTALMOLOGICOS
# =========================================================

permiso_registrar_resultados_examenes = requerir_permiso(
    "Registrar resultados de exámenes oftalmológicos",
    ACCION_ESCRITURA,
)


@router.post(
    "/consultas/{consulta_id}/examenes",
    response_model=ExamenOftalmologicoRespuesta,
    status_code=201,
)
def registrar_examen(
    datos: ExamenOftalmologicoCrear,
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_resultados_examenes),
):
    return service.registrar_examen(db, consulta_id, datos, usuario)


@router.get(
    "/consultas/{consulta_id}/examenes",
    response_model=list[ExamenConResultadosRespuesta],
)
def listar_examenes(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_examenes(db, consulta_id)


@router.post(
    "/examenes/{examen_id}/resultados",
    response_model=ResultadoExamenRespuesta,
    status_code=201,
)
def registrar_resultado_examen(
    datos: ResultadoExamenCrear,
    examen_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_registrar_resultados_examenes),
):
    return service.registrar_resultado_examen(db, examen_id, datos, usuario)


@router.get(
    "/examenes/{examen_id}/resultados",
    response_model=list[ResultadoExamenRespuesta],
)
def listar_resultados_examen(
    examen_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.listar_resultados_examen(db, examen_id)


@router.get(
    "/examenes/{examen_id}",
    response_model=ExamenConResultadosRespuesta,
)
def consultar_examen(
    examen_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_historial_clinico),
):
    return service.consultar_examen(db, examen_id)
