from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
    HistorialClinicoRespuesta,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def consultar_historial_clinico(db: Session, paciente_id: int) -> HistorialClinicoRespuesta:
    paciente, historial = repo.obtener_paciente_con_historial(db, paciente_id)
    if paciente is None:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    return HistorialClinicoRespuesta(paciente=paciente, historial=historial)


def crear_antecedente(db: Session, datos: AntecedenteClinicoCrear, usuario: Usuario):
    try:
        historial = repo.obtener_historial_activo_por_id(db, datos.historial_clinico_id)
        if historial is None:
            raise HTTPException(
                status_code=404, detail="Historial clínico no encontrado o inactivo",
            )

        antecedente = repo.crear_antecedente(db, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="CREAR_ANTECEDENTE",
            entidad_afectada="antecedente_clinico",
            id_registro_afectado=antecedente.id,
            descripcion="Antecedente clínico registrado",
        )
        db.commit()
        db.refresh(antecedente)
        return antecedente
    except Exception:
        db.rollback()
        raise


def actualizar_antecedente(
    db: Session,
    antecedente_id: int,
    datos: AntecedenteClinicoActualizar,
    usuario: Usuario,
):
    try:
        antecedente = repo.obtener_antecedente_disponible_por_id(db, antecedente_id)
        if antecedente is None:
            raise HTTPException(
                status_code=404, detail="Antecedente clínico no encontrado o no disponible",
            )

        antecedente = repo.actualizar_antecedente(db, antecedente, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="ACTUALIZAR_ANTECEDENTE",
            entidad_afectada="antecedente_clinico",
            id_registro_afectado=antecedente.id,
            descripcion="Antecedente clínico actualizado",
        )
        db.commit()
        db.refresh(antecedente)
        return antecedente
    except Exception:
        db.rollback()
        raise
