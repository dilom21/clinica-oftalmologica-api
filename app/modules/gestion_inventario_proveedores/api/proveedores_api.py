from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    get_db,
    requerir_permiso,
)
from app.modules.gestion_inventario_proveedores.schemas.proveedor import (
    ProveedorActualizar,
    ProveedorCrear,
    ProveedorRespuesta,
)
from app.modules.gestion_inventario_proveedores.services import proveedor as service


FUNCION_RBAC = "Gestionar proveedores"

router = APIRouter(
    prefix="/proveedores",
    tags=["CU28 - Gestionar proveedores"],
)
permiso_lectura = requerir_permiso(FUNCION_RBAC, ACCION_LECTURA)
permiso_escritura = requerir_permiso(FUNCION_RBAC, ACCION_ESCRITURA)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/", response_model=list[ProveedorRespuesta])
def listar_proveedores(
    estado: bool | None = Query(True, description="Omitido: activos; false: inactivos"),
    busqueda: str | None = Query(None, min_length=1, max_length=150),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
):
    return service.listar(
        db, estado=estado, busqueda=busqueda, skip=skip, limit=limit,
    )


@router.get("/{proveedor_id}", response_model=ProveedorRespuesta)
def consultar_proveedor(
    proveedor_id: int = Path(gt=0, le=2**31 - 1),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
):
    return service.consultar(db, proveedor_id)


@router.post("/", response_model=ProveedorRespuesta, status_code=201)
def crear_proveedor(
    request: Request,
    datos: ProveedorCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    return service.crear(db, datos, usuario, _ip(request))


@router.put("/{proveedor_id}", response_model=ProveedorRespuesta)
def actualizar_proveedor(
    request: Request,
    datos: ProveedorActualizar,
    proveedor_id: int = Path(gt=0, le=2**31 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    return service.actualizar(db, proveedor_id, datos, usuario, _ip(request))


@router.delete("/{proveedor_id}", status_code=204)
def desactivar_proveedor(
    request: Request,
    proveedor_id: int = Path(gt=0, le=2**31 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escritura),
):
    service.desactivar(db, proveedor_id, usuario, _ip(request))
    return Response(status_code=204)
