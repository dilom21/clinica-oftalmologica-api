from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app.core.dependencies import ACCION_ESCRITURA, ACCION_LECTURA, requerir_permiso
from app.core.dependencies import get_db
from app.modules.gestion_historial_clinico.schemas.servicios_realizados import (
    ServicioRealizadoActualizar,
    ServicioRealizadoCrear,
    ServicioRealizadoRespuesta,
    ServiciosRealizadosPagina,
    ServiciosRealizadosLoteCrear,
)
from app.modules.gestion_historial_clinico.services import servicios_realizados as service


router = APIRouter(prefix="/servicios-realizados", tags=["CU22 - Servicios realizados"])
permiso_lectura = requerir_permiso("Registrar servicios realizados", ACCION_LECTURA)
permiso_escritura = requerir_permiso("Registrar servicios realizados", ACCION_ESCRITURA)


@router.post("", response_model=ServicioRealizadoRespuesta, status_code=201)
def registrar_servicio(
    datos: ServicioRealizadoCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    return service.registrar(db, datos, usuario)


@router.post("/lote", response_model=list[ServicioRealizadoRespuesta], status_code=201)
def registrar_servicios_lote(
    datos: ServiciosRealizadosLoteCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    return service.registrar_lote(db, datos, usuario)


@router.get("", response_model=ServiciosRealizadosPagina)
def listar_servicios(
    paciente_id: int | None = Query(None, gt=0, le=2**63 - 1),
    servicio_id: int | None = Query(None, gt=0, le=2**63 - 1),
    consulta_clinica_id: int | None = Query(None, gt=0, le=2**63 - 1),
    oftalmologo_id: int | None = Query(None, gt=0, le=2**63 - 1),
    estado: bool | None = Query(True, description="Omitido: activos; false: anulados"),
    desde: AwareDatetime | None = Query(None),
    hasta: AwareDatetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
):
    return service.listar(
        db, paciente_id=paciente_id, servicio_id=servicio_id,
        consulta_clinica_id=consulta_clinica_id, oftalmologo_id=oftalmologo_id,
        estado=estado, desde=desde, hasta=hasta, page=page, page_size=page_size,
    )


@router.get("/{registro_id}", response_model=ServicioRealizadoRespuesta)
def consultar_servicio(
    registro_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
):
    return service.consultar(db, registro_id)


@router.put("/{registro_id}", response_model=ServicioRealizadoRespuesta)
def actualizar_servicio(
    datos: ServicioRealizadoActualizar,
    registro_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    return service.actualizar(db, registro_id, datos, usuario)


@router.delete("/{registro_id}", response_model=ServicioRealizadoRespuesta)
def anular_servicio(
    registro_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    return service.anular(db, registro_id, usuario)
