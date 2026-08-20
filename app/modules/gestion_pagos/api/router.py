from fastapi import APIRouter

router = APIRouter(
    prefix="/pagos",
    tags=["Gestión de Pagos"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de pagos funcionando"}