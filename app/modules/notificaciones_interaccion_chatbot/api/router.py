from fastapi import APIRouter
from app.modules.notificaciones_interaccion_chatbot.api.chatbot import router as chatbot_router

router = APIRouter(
    prefix="/notificaciones-chatbot",
)
router.include_router(chatbot_router)

@router.get("/", tags=["Notificaciones, Valoración y Chatbot"])
def obtener_modulo():
    return {"mensaje": "Módulo de notificaciones, valoración y chatbot funcionando"}
