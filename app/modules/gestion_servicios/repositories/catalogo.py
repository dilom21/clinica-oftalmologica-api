from sqlalchemy import func, select
from app.modules.gestion_historial_clinico.models.models import ServicioOftalmologico

def listar_activos(db, *, busqueda="", offset=0, limite=20):
    consulta = select(ServicioOftalmologico).where(ServicioOftalmologico.estado.is_(True))
    if busqueda:
        consulta = consulta.where(func.lower(ServicioOftalmologico.nombre).contains(busqueda, autoescape=True))
    return db.scalars(consulta.order_by(
        ServicioOftalmologico.nombre.asc(), ServicioOftalmologico.id.asc()
    ).offset(offset).limit(limite)).all()

def obtener_activo(db, servicio_id):
    return db.scalar(select(ServicioOftalmologico).where(
        ServicioOftalmologico.id == servicio_id, ServicioOftalmologico.estado.is_(True)))
