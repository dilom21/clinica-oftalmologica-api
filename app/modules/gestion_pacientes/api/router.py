from fastapi import APIRouter

router = APIRouter(
    prefix="/pacientes",
    tags=["Gestión de Pacientes"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de pacientes funcionando"}