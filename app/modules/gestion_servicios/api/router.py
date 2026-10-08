from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_LECTURA,
    get_db,
    obtener_administrador_actual,
    requerir_permiso,
)
from app.modules.gestion_servicios.schemas.schemas import (
    ServicioActualizar,
    ServicioCrear,
    ServicioRespuesta,
)
from app.modules.gestion_servicios.services import service


router = APIRouter(
    prefix="/servicios-oftalmologicos",
    tags=["Gestion de Servicios Oftalmologicos"],
)
permiso_lectura = requerir_permiso(
    "Registrar servicios realizados",
    ACCION_LECTURA,
)


@router.get("/", response_model=list[ServicioRespuesta])
def listar_servicios(
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
):
    return service.listar(db)


@router.get("/{servicio_id}", response_model=ServicioRespuesta)
def consultar_servicio(
    servicio_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
):
    return service.consultar(db, servicio_id)


@router.post("/", response_model=ServicioRespuesta, status_code=201)
def crear_servicio(
    request: Request,
    datos: ServicioCrear,
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return service.crear(
        db,
        datos,
        administrador,
        request.client.host if request.client else None,
    )


@router.put("/{servicio_id}", response_model=ServicioRespuesta)
def actualizar_servicio(
    request: Request,
    datos: ServicioActualizar,
    servicio_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    administrador=Depends(obtener_administrador_actual),
):
    return service.actualizar(
        db,
        servicio_id,
        datos,
        administrador,
        request.client.host if request.client else None,
    )
