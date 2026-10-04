from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_servicios.models.models import ServicioOftalmologico


def obtener_servicio(db: Session, servicio_id: int):
    return db.get(ServicioOftalmologico, servicio_id)


def _cargar_relaciones():
    return (
        selectinload(ServicioRealizado.servicio),
        selectinload(ServicioRealizado.paciente),
        selectinload(ServicioRealizado.oftalmologo),
    )


def obtener_registro(db: Session, registro_id: int, *, bloquear: bool = False):
    stmt = (
        select(ServicioRealizado)
        .options(*_cargar_relaciones())
        .where(ServicioRealizado.id == registro_id)
        .execution_options(populate_existing=True)
    )
    if bloquear:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def guardar_registro(db: Session, registro: ServicioRealizado):
    db.add(registro)
    db.flush()
    # Carga relaciones dentro de la transacción, antes de serializar/confirmar.
    return obtener_registro(db, registro.id)


def listar_registros(
    db: Session, *, paciente_id=None, servicio_id=None, consulta_clinica_id=None,
    oftalmologo_id=None, estado=True, desde=None, hasta=None, page=1, page_size=20,
):
    filtros = []
    for columna, valor in (
        (ServicioRealizado.paciente_id, paciente_id),
        (ServicioRealizado.servicio_id, servicio_id),
        (ServicioRealizado.consulta_clinica_id, consulta_clinica_id),
        (ServicioRealizado.oftalmologo_id, oftalmologo_id),
        (ServicioRealizado.estado, estado),
    ):
        if valor is not None:
            filtros.append(columna == valor)
    if desde is not None:
        filtros.append(ServicioRealizado.fecha_realizacion >= desde)
    if hasta is not None:
        filtros.append(ServicioRealizado.fecha_realizacion <= hasta)

    total = db.scalar(select(func.count()).select_from(ServicioRealizado).where(*filtros))
    stmt = (
        select(ServicioRealizado)
        .options(*_cargar_relaciones())
        .where(*filtros)
        .order_by(
            ServicioRealizado.fecha_realizacion.desc().nulls_last(),
            ServicioRealizado.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return db.scalars(stmt).all(), total
