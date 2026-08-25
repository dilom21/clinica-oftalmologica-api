from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database.base import Base

class Usuario(Base):
    __tablename__ = "usuarios"

    ID = Column(Integer, primary_key=True, index=True)
    Correo = Column(String(100), unique=True, index=True, nullable=False)
    Password_hash = Column(String(255), nullable=False)
    Estado = Column(Boolean, default=True)  # True = Habilitado, False = Deshabilitado
    Fecha_creacion = Column(DateTime, default=datetime.utcnow)
    
    # Clave foránea según la relación con la tabla Rol del diagrama
    Id_Rol = Column(Integer, ForeignKey("rol.id"), nullable=False)

    # Relaciones (opcional si ya tienes el modelo Rol definido)
    # rol = relationship("Rol", back_populates="usuarios")