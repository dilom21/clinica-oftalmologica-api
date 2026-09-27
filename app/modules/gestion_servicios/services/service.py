from sqlalchemy.orm import Session
from app.modules.gestion_servicios.repositories.repository import ServicioRepository
from app.modules.gestion_servicios.schemas.schemas import ServicioCreate, ServicioUpdate

servicio_repo = ServicioRepository()

def obtener_servicios(db: Session):
    return servicio_repo.get_todos(db)

def obtener_servicio(db: Session, servicio_id: int):
    return servicio_repo.get_por_id(db, servicio_id)

def crear_servicio(db: Session, servicio: ServicioCreate):
    return servicio_repo.crear(db, servicio)

def actualizar_servicio(db: Session, servicio_id: int, servicio_actualizado: ServicioUpdate):
    # Primero verificamos que el servicio exista
    servicio_bd = servicio_repo.get_por_id(db, servicio_id)
    if not servicio_bd:
        return None # Retornamos None para que el router lance el error 404
    
    return servicio_repo.actualizar(db, servicio_bd, servicio_actualizado)