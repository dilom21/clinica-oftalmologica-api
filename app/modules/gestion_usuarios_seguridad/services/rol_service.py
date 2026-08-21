from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import RolCrear


def crear_rol(
    db: Session,
    datos: RolCrear,
):
    try:
        rol = repo.crear_rol(
            db=db,
            nombre=datos.nombre,
            descripcion=datos.descripcion,
            estado=datos.estado,
        )

        db.commit()
        db.refresh(rol)

        return rol

    except Exception:
        db.rollback()
        raise


def listar_roles(db: Session):
    return repo.listar_roles(db)


# La asignación o el cambio de rol se adaptará posteriormente a usuario.rol_id.
