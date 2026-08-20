from fastapi import APIRouter

router = APIRouter(
    prefix="/usuarios-seguridad",
    tags=["Usuarios y Seguridad"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de usuarios y seguridad funcionando"}