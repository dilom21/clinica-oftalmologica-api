from pydantic import BaseModel, EmailStr
from typing import Optional

class ProveedorBase(BaseModel):
    razon_social: str
    nit: Optional[str] = None
    telefono: Optional[str] = None
    correo: Optional[EmailStr] = None
    direccion: Optional[str] = None
    contacto: Optional[str] = None
    estado: Optional[bool] = True

class ProveedorCreate(ProveedorBase):
    pass

class ProveedorUpdate(BaseModel):
    razon_social: Optional[str] = None
    nit: Optional[str] = None
    telefono: Optional[str] = None
    correo: Optional[EmailStr] = None
    direccion: Optional[str] = None
    contacto: Optional[str] = None
    estado: Optional[bool] = None

class ProveedorResponse(ProveedorBase):
    id: int

    class Config:
        from_attributes = True