from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.gestion_pacientes.repositories import repository as repo

from app.modules.gestion_pacientes.schemas.schemas import (
    PacienteCrear,
    PacienteActualizar,
    AntecedenteClinicoCrear,
    AntecedenteClinicoActualizar,
)

from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


# =========================================================
# CU07 - GESTIONAR PACIENTES
# =========================================================

def crear_paciente(
    db: Session,
    datos: PacienteCrear,
):
    if datos.ci:
        existente = repo.obtener_paciente_por_ci(
            db,
            datos.ci,
        )

        if existente:
            raise HTTPException(
                status_code=409,
                detail="Ya existe un paciente con ese CI",
            )

    try:
        paciente = repo.crear_paciente(
            db,
            datos,
        )

        registrar_bitacora(
            db=db,
            usuario_id=None,
            accion="CREAR_PACIENTE",
            entidad_afectada="paciente",
            id_registro_afectado=paciente.id,
            descripcion="Paciente registrado",
        )

        db.commit()
        db.refresh(paciente)

        return paciente

    except Exception:
        db.rollback()
        raise


def listar_pacientes(db: Session):
    return repo.listar_pacientes(db)


def obtener_paciente(
    db: Session,
    paciente_id: int,
):
    paciente = repo.obtener_paciente_por_id(
        db,
        paciente_id,
    )

    if not paciente:
        raise HTTPException(
            status_code=404,
            detail="Paciente no encontrado",
        )

    return paciente


def actualizar_paciente(
    db: Session,
    paciente_id: int,
    datos: PacienteActualizar,
):
    paciente = obtener_paciente(
        db,
        paciente_id,
    )

    try:
        paciente = repo.actualizar_paciente(
            db,
            paciente,
            datos,
        )

        registrar_bitacora(
            db=db,
            usuario_id=None,
            accion="ACTUALIZAR_PACIENTE",
            entidad_afectada="paciente",
            id_registro_afectado=paciente.id,
            descripcion="Datos del paciente actualizados",
        )

        db.commit()
        db.refresh(paciente)

        return paciente

    except Exception:
        db.rollback()
        raise


def eliminar_paciente(
    db: Session,
    paciente_id: int,
):
    paciente = obtener_paciente(
        db,
        paciente_id,
    )

    try:
        paciente = repo.eliminar_logicamente_paciente(
            db,
            paciente,
        )

        registrar_bitacora(
            db=db,
            usuario_id=None,
            accion="DESACTIVAR_PACIENTE",
            entidad_afectada="paciente",
            id_registro_afectado=paciente.id,
            descripcion="Paciente desactivado",
        )

        db.commit()

        return paciente

    except Exception:
        db.rollback()
        raise
# =========================================================
# CU14 - GESTIONAR ANTECEDENTES CLÍNICOS
# =========================================================

def listar_antecedentes_por_historial(
    db: Session,
    historial_clinico_id: int,
):
    return repo.listar_antecedentes_por_historial(
        db,
        historial_clinico_id,
    )


def crear_antecedente(
    db: Session,
    datos: AntecedenteClinicoCrear,
):
    try:
        antecedente = repo.crear_antecedente(
            db,
            datos,
        )

        registrar_bitacora(
            db=db,
            usuario_id=None,
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
):
    antecedente = repo.obtener_antecedente_por_id(
        db,
        antecedente_id,
    )

    if not antecedente:
        raise HTTPException(
            status_code=404,
            detail="Antecedente clínico no encontrado",
        )

    try:
        antecedente = repo.actualizar_antecedente(
            db,
            antecedente,
            datos,
        )

        registrar_bitacora(
            db=db,
            usuario_id=None,
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