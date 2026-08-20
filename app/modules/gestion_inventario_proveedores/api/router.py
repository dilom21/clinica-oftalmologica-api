from fastapi import APIRouter

router = APIRouter(
    prefix="/inventario-proveedores",
    tags=["Inventario y Proveedores"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de inventario y proveedores funcionando"}