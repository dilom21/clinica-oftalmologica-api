from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional

class UsuarioBase(BaseModel):
    Correo: EmailStr
    Id_Rol: int
    Estado: Optional[bool] = True

class UsuarioCreate(UsuarioBase):
    Password: str

class UsuarioResponse(UsuarioBase):
    ID: int
    Fecha_creacion: datetime

    class Config:
        from_attributes = True  # Para compatibilidad con Pydantic v2 / orm_mode