from fastapi import APIRouter

router = APIRouter(
    prefix="/historial-clinico",
    tags=["Historial Clínico"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de historial clínico funcionando"}