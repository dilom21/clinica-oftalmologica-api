from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

# Importa tu conexión a la base de datos (ajusta la ruta si en tu proyecto es diferente)
from app.database.session import get_db
from app.modules.gestion_servicios.schemas.schemas import ServicioResponse, ServicioCreate, ServicioUpdate
from app.modules.gestion_servicios.repositories.repository import ServicioRepository

# Esto crea la sección en Swagger
router = APIRouter(
    prefix="/servicios-oftalmologicos",
    tags=["Gestión de Servicios Oftalmológicos"]
)

repo = ServicioRepository()

# 1. Listar todos los servicios
@router.get("/", response_model=List[ServicioResponse])
def listar_servicios(db: Session = Depends(get_db)):
    return repo.get_todos(db)

# 2. Obtener un servicio específico
@router.get("/{servicio_id}", response_model=ServicioResponse)
def obtener_servicio(servicio_id: int, db: Session = Depends(get_db)):
    servicio = repo.get_por_id(db, servicio_id)
    if not servicio:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    return servicio

# 3. Crear un nuevo servicio
@router.post("/", response_model=ServicioResponse, status_code=201)
def crear_servicio(servicio: ServicioCreate, db: Session = Depends(get_db)):
    return repo.crear(db, servicio)

# 4. Actualizar un servicio
@router.put("/{servicio_id}", response_model=ServicioResponse)
def actualizar_servicio(servicio_id: int, servicio_actualizado: ServicioUpdate, db: Session = Depends(get_db)):
    servicio_bd = repo.get_por_id(db, servicio_id)
    if not servicio_bd:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    return repo.actualizar(db, servicio_bd, servicio_actualizado)