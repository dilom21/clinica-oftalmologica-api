from sqlalchemy import Column, Integer, String, Boolean
from app.database.base import Base 

class Proveedor(Base):
    __tablename__ = "proveedores" # Puedes cambiarlo a "PROVEEDOR" si tu BD exige mayúsculas

    id = Column(Integer, primary_key=True, index=True)
    razon_social = Column(String(150), nullable=False)
    nit = Column(String(30), unique=True, index=True, nullable=True)
    telefono = Column(String(20), nullable=True)
    correo = Column(String(100), nullable=True)
    direccion = Column(String(255), nullable=True)
    contacto = Column(String(100), nullable=True)
    estado = Column(Boolean, default=True) # Asumimos Boolean (True=Activo, False=Inactivo)