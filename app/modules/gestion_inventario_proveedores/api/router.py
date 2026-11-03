from fastapi import APIRouter

from app.modules.gestion_inventario_proveedores.api.compras import (
    router as compras_router,
)
from app.modules.gestion_inventario_proveedores.api.proveedores import (
    router as proveedores_router,
)

router = APIRouter()
router.include_router(proveedores_router)
router.include_router(compras_router)


@router.get("/inventario-proveedores/", tags=["Inventario y Proveedores"])
def obtener_modulo():
    return {"mensaje": "Módulo de inventario y proveedores funcionando"}
