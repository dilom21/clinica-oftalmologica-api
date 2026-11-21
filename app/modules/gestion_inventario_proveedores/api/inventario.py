from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
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
from app.modules.gestion_usuarios_seguridad.models.models import Accion, Funcion, Modulo, Rol, RolFuncion


router = APIRouter(
    prefix="/inventario-proveedores",
    tags=["CU30 - Inventario y alertas"],
    dependencies=[Depends(get_tenant_claims)],
)
_permiso_lectura = requerir_permiso("Consultar inventario y alertas", ACCION_LECTURA)


def permiso_lectura(
    usuario=Depends(_permiso_lectura),
    db: Session = Depends(get_db),
):
    """Revalida el rol y el módulo de CU30 en la misma sesión del tenant."""
    rol_activo = db.scalar(
        select(Rol.id).where(Rol.id == usuario.rol_id, Rol.estado.is_(True))
    )
    modulo_activo = db.scalar(
        select(Modulo.id)
        .join(Funcion, Funcion.modulo_id == Modulo.id)
        .join(RolFuncion, RolFuncion.funcion_id == Funcion.id)
        .join(Accion, Accion.id == RolFuncion.accion_id)
        .where(
            RolFuncion.rol_id == usuario.rol_id,
            Funcion.nombre == "Consultar inventario y alertas",
            Funcion.estado.is_(True),
            Modulo.nombre == "Inventario y Proveedores",
            Modulo.estado.is_(True),
            Accion.estado.is_(True),
            func.upper(func.trim(Accion.nombre)).in_({ACCION_LECTURA, "AMBAS"}),
        )
        .limit(1)
    )
    if rol_activo is None or modulo_activo is None:
        raise HTTPException(status_code=403, detail="No tiene permiso para Consultar inventario y alertas")
    return usuario


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
