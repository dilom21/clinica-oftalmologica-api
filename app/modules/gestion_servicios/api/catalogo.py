# CU38: lectura del catalogo del tenant autenticado.
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session
from app.core.dependencies import get_db, get_tenant_claims, nombre_rol_actual, obtener_usuario_actual
from app.modules.gestion_servicios.schemas.schemas import ServicioRespuesta
from app.modules.gestion_servicios.services import catalogo as servicio

router = APIRouter(prefix="/catalogo", tags=["CU38 - Catalogo de servicios"])

def paciente_catalogo(_claims=Depends(get_tenant_claims), usuario=Depends(obtener_usuario_actual)):
    rol = getattr(usuario, "rol", None)
    if rol is None or rol.estado is not True or nombre_rol_actual(usuario) != "paciente":
        raise HTTPException(403, "Solo pacientes con rol activo pueden consultar el catálogo")
    return usuario

@router.get("/", response_model=list[ServicioRespuesta])
def listar_catalogo(
    busqueda: str = Query(default="", max_length=150),
    offset: int = Query(default=0, ge=0),
    limite: int = Query(default=20, ge=1, le=100),
    _paciente=Depends(paciente_catalogo),
    db: Session = Depends(get_db),
):
    return servicio.listar(db, busqueda=busqueda, offset=offset, limite=limite)

@router.get("/{servicio_id}", response_model=ServicioRespuesta)
def detalle_catalogo(
    servicio_id: int = Path(gt=0, le=2**63 - 1),
    _paciente=Depends(paciente_catalogo),
    db: Session = Depends(get_db),
):
    return servicio.detalle(db, servicio_id)
