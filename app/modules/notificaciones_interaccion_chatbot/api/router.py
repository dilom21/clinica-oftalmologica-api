from fastapi import APIRouter

router = APIRouter(
    prefix="/notificaciones-chatbot",
    tags=["Notificaciones, Valoración y Chatbot"]
)

@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de notificaciones, valoración y chatbot funcionando"}