from fastapi import APIRouter

router = APIRouter(
    prefix="/agenda-citas",
    tags=["Agenda y Citas"]
)

@router.get("/")
def obtener_agenda():
    return {"mensaje": "Módulo de agenda y citas funcionando"}