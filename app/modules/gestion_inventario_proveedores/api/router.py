from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_LECTURA, get_db, get_tenant_claims, requerir_permiso,
)
from app.modules.gestion_inventario_proveedores.repositories.inventario import (
    RepositorioInventario, obtener_repositorio_inventario,
)
from app.modules.gestion_inventario_proveedores.schemas.inventario import (
    AlertasPagina, FiltrosInventario, InventarioPagina, LotesProductoRespuesta,
)
from app.modules.gestion_inventario_proveedores.services import inventario as service


router = APIRouter(
    prefix="/inventario-proveedores",
    tags=["CU30 - Inventario y alertas"],
    dependencies=[Depends(get_tenant_claims)],
)
permiso_lectura = requerir_permiso("Consultar inventario y alertas", ACCION_LECTURA)


@router.get("/inventario", response_model=InventarioPagina)
def consultar_inventario(
    filtros: Annotated[FiltrosInventario, Query()],
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
    repositorio: RepositorioInventario = Depends(obtener_repositorio_inventario),
):
    return service.listar_inventario(db, filtros, repositorio)


@router.get("/productos/{producto_id}/lotes", response_model=LotesProductoRespuesta)
def consultar_lotes_producto(
    producto_id: int = Path(gt=0, le=2**63 - 1),
    dias_vencimiento: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
    repositorio: RepositorioInventario = Depends(obtener_repositorio_inventario),
):
    return service.consultar_lotes(db, producto_id, dias_vencimiento, repositorio)


@router.get("/alertas", response_model=AlertasPagina)
def consultar_alertas(
    filtros: Annotated[FiltrosInventario, Query()],
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_lectura),
    repositorio: RepositorioInventario = Depends(obtener_repositorio_inventario),
):
    return service.listar_alertas(db, filtros, repositorio)
