from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo


def consultar_bitacora(db: Session):
    return repo.listar_bitacora(db)
