
from fastapi import APIRouter

from .catalogo import router as catalogo_router
from .movimientos import router as movimientos_router
from .proveedores import router as proveedores_router

router = APIRouter()

router.include_router(catalogo_router)
router.include_router(movimientos_router)
router.include_router(proveedores_router)


@router.get("/inventario-proveedores/", tags=["Inventario y Proveedores"])
def obtener_modulo():
    return {"mensaje": "Módulo de inventario y proveedores funcionando"}
