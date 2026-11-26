
from fastapi import APIRouter

<<<<<<< HEAD
from app.modules.gestion_inventario_proveedores.api.compras import (
    router as compras_router,
)
from app.modules.gestion_inventario_proveedores.api.proveedores import (
    router as proveedores_router,
)

router = APIRouter()
router.include_router(proveedores_router)
router.include_router(compras_router)
=======
from .catalogo import router as catalogo_router
from .inventario import router as inventario_router
from .movimientos import router as movimientos_router
from .proveedores import router as proveedores_router

router = APIRouter()

router.include_router(catalogo_router)
router.include_router(inventario_router)
router.include_router(movimientos_router)
router.include_router(proveedores_router)
>>>>>>> main


@router.get("/inventario-proveedores/", tags=["Inventario y Proveedores"])
def obtener_modulo():
    return {"mensaje": "Módulo de inventario y proveedores funcionando"}
