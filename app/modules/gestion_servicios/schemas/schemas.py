from pydantic import BaseModel
from typing import Optional

# 1. El molde base con los campos que siempre usamos
class ServicioBase(BaseModel):
    nombre: str
    descripcion: Optional[str] = None
    precio_base: float
    duracion_estimada: Optional[int] = None
    estado: bool = True

# 2. El molde que usamos cuando Angular nos pide CREAR un servicio
class ServicioCreate(ServicioBase):
    pass

# 3. El molde que usamos cuando Angular nos pide EDITAR un servicio (todo es opcional)
class ServicioUpdate(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    precio_base: Optional[float] = None
    duracion_estimada: Optional[int] = None
    estado: Optional[bool] = None

# 4. El molde que le respondemos a Angular cuando nos pide la LISTA de servicios
class ServicioResponse(ServicioBase):
    id: int

    class Config:
        from_attributes = True  # Esto le permite leer datos directamente de SQLAlchemy