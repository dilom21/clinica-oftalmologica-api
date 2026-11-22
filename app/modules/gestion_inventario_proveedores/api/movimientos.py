"""CU27: endpoints tenant-aware para registrar y consultar movimientos."""
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    get_db,
    get_tenant_claims,
    requerir_permiso,
)
from app.modules.gestion_inventario_proveedores.schemas.movimientos import (
    ExistenciaRespuesta, MovimientoManualCrear, MovimientoRespuesta,
)
from app.modules.gestion_inventario_proveedores.services import movimientos

FUNCION_CU27 = "Gestionar movimientos de inventario"
MODULO_CU27 = "Inventario y Proveedores"
permiso_cu27_lectura = requerir_permiso(
    FUNCION_CU27, ACCION_LECTURA, modulo=MODULO_CU27,
)
permiso_cu27_escritura = requerir_permiso(
    FUNCION_CU27, ACCION_ESCRITURA, modulo=MODULO_CU27,
)


router = APIRouter(
    prefix="/inventario-proveedores",
    tags=["CU27 - Movimientos de inventario"],
    dependencies=[Depends(get_tenant_claims)],
)


@router.post(
    "/movimientos", response_model=MovimientoRespuesta,
    status_code=status.HTTP_201_CREATED,
)
def registrar_movimiento(
    datos: MovimientoManualCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_cu27_escritura),
):
    return movimientos.registrar_manual(db, datos, usuario.id)


@router.get("/movimientos", response_model=list[MovimientoRespuesta])
def consultar_movimientos(
    lote_id: int | None = Query(default=None, gt=0),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_cu27_lectura),
):
    return movimientos.listar_movimientos(db, lote_id=lote_id, skip=skip, limit=limit)


@router.get("/existencias/{lote_id}", response_model=ExistenciaRespuesta)
def consultar_existencia(
    lote_id: int = Path(gt=0),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_cu27_lectura),
):
    return movimientos.consultar_existencia(db, lote_id)
