from datetime import datetime
from pydantic import BaseModel, ConfigDict

class NotificacionRespuesta(BaseModel):
    id: int
    titulo: str
    mensaje: str
    tipo: str
    fecha_creacion: datetime
    fecha_envio: datetime | None
    leida: bool
    fecha_lectura: datetime | None
    model_config = ConfigDict(from_attributes=True)

class PaginaNotificaciones(BaseModel):
    items: list[NotificacionRespuesta]
    total: int
    limite: int
    desplazamiento: int
