from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session
from app.core.dependencies import get_db, obtener_usuario_actual
from app.modules.notificaciones_interaccion_chatbot.schemas.notificacion import PaginaNotificaciones, NotificacionRespuesta
from app.modules.notificaciones_interaccion_chatbot.services.notificaciones import listar_propias, marcar_leida

router = APIRouter(prefix="/notificaciones", tags=["Notificaciones de pacientes"])

@router.get("/", response_model=PaginaNotificaciones)
def listar_notificaciones(limite: int = Query(20, ge=1, le=100),
    desplazamiento: int = Query(0, ge=0), db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual)):
    return listar_propias(db, usuario, limite, desplazamiento)

@router.patch("/{notificacion_id}/leida", response_model=NotificacionRespuesta)
def marcar_notificacion_leida(notificacion_id: int = Path(gt=0),
    db: Session = Depends(get_db), usuario=Depends(obtener_usuario_actual)):
    return marcar_leida(db, usuario, notificacion_id)
