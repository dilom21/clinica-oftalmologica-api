from sqlalchemy.orm import Session
from app.modules.gestion_servicios.models.models import ServicioOftalmologico
from app.modules.gestion_servicios.schemas.schemas import ServicioCreate, ServicioUpdate

class ServicioRepository:
    
    # 1. Obtener toda la lista de servicios
    def get_todos(self, db: Session):
        return db.query(ServicioOftalmologico).all()

    # 2. Buscar un solo servicio por su ID
    def get_por_id(self, db: Session, servicio_id: int):
        return db.query(ServicioOftalmologico).filter(ServicioOftalmologico.id == servicio_id).first()

    # 3. Guardar un nuevo servicio en la base de datos
    def crear(self, db: Session, servicio: ServicioCreate):
        nuevo_servicio = ServicioOftalmologico(
            nombre=servicio.nombre,
            descripcion=servicio.descripcion,
            precio_base=servicio.precio_base,
            duracion_estimada=servicio.duracion_estimada,
            estado=servicio.estado
        )
        db.add(nuevo_servicio)
        db.commit()
        db.refresh(nuevo_servicio) # Refresca para obtener el ID que le asignó la base de datos
        return nuevo_servicio

    # 4. Actualizar un servicio que ya existe
    def actualizar(self, db: Session, servicio_bd: ServicioOftalmologico, servicio_actualizado: ServicioUpdate):
        # exclude_unset=True asegura que solo actualicemos los campos que el frontend realmente envió
        datos_actualizados = servicio_actualizado.dict(exclude_unset=True)
        for key, value in datos_actualizados.items():
            setattr(servicio_bd, key, value)
        
        db.commit()
        db.refresh(servicio_bd)
        return servicio_bd