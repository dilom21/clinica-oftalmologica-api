from sqlalchemy import Column, Integer, String, Float, Boolean
from app.database.base import Base
class ServicioOftalmologico(Base):
    __tablename__ = "servicios_oftalmologicos"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)
    descripcion = Column(String(500), nullable=True)
    precio_base = Column(Float, nullable=False)
    duracion_estimada = Column(Integer, nullable=True) # Expresado en minutos
    estado = Column(Boolean, default=True)