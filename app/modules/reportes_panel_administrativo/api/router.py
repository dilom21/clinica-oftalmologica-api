from fastapi import APIRouter

router = APIRouter(
    prefix="/reportes-panel",
    tags=["Reportes y Panel Administrativo"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de reportes y panel administrativo funcionando"}