from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    BitacoraPaginadaRespuesta,
    BitacoraRespuesta,
)


def consultar_bitacora(
    db: Session,
    usuario_id: int | None = None,
    accion: str | None = None,
    entidad_afectada: str | None = None,
    id_registro_afectado: int | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
    page: int | None = None,
    page_size: int | None = None,
):
    if desde and hasta and desde > hasta:
        raise HTTPException(
            status_code=422,
            detail="La fecha 'desde' no puede ser posterior a 'hasta'",
        )

    paginada = page is not None or page_size is not None
    pagina_actual = page or 1
    tamano_pagina = page_size or 20

    registros = repo.listar_bitacora(
        db=db,
        usuario_id=usuario_id,
        accion=accion,
        entidad_afectada=entidad_afectada,
        id_registro_afectado=id_registro_afectado,
        desde=desde,
        hasta=hasta,
        offset=(pagina_actual - 1) * tamano_pagina if paginada else None,
        limit=tamano_pagina if paginada else None,
    )

    items = [
        BitacoraRespuesta(
            id=registro.id,
            usuario_id=registro.usuario_id,
            fecha_hora=registro.fecha_hora,
            ip=str(registro.ip) if registro.ip is not None else None,
            accion=registro.accion,
            entidad_afectada=registro.entidad_afectada,
            id_registro_afectado=registro.id_registro_afectado,
            descripcion=registro.descripcion,
        )
        for registro in registros
    ]

    if not paginada:
        return items

    total = repo.contar_bitacora(
        db=db,
        usuario_id=usuario_id,
        accion=accion,
        entidad_afectada=entidad_afectada,
        id_registro_afectado=id_registro_afectado,
        desde=desde,
        hasta=hasta,
    )
    return BitacoraPaginadaRespuesta(
        items=items,
        total=total,
        page=pagina_actual,
        page_size=tamano_pagina,
        total_pages=(total + tamano_pagina - 1) // tamano_pagina,
    )


def registrar_consulta(
    db: Session,
    usuario_id: int,
    ip: str | None = None,
):
    try:
        repo.registrar_bitacora(
            db=db,
            usuario_id=usuario_id,
            ip=ip,
            accion="CONSULTAR_BITACORA",
            entidad_afectada="bitacora",
            descripcion="Consulta de bitácora realizada",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
