from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pacientes.repositories import repository as repo

from app.modules.gestion_pacientes.schemas.schemas import (
    PacienteCrear,
    PacienteActualizar,
    MiPerfilPacienteResponse,
    MiPerfilPacienteActualizar,
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
# CU08 - MI PERFIL (APP MÓVIL DE PACIENTES)
# =========================================================

def obtener_mi_perfil(
    db: Session,
    usuario,
) -> MiPerfilPacienteResponse:
    """Devuelve el perfil del paciente autenticado.

    El paciente se resuelve exclusivamente a partir del usuario del JWT
    (usuario.id == paciente.usuario_id); nunca recibe paciente_id del cliente.
    """
    if nombre_rol_actual(usuario) != "paciente":
        raise HTTPException(
            status_code=403,
            detail="Acceso disponible únicamente para pacientes.",
        )

    paciente = repo.obtener_paciente_por_usuario_id(
        db,
        usuario.id,
    )

    if not paciente:
        raise HTTPException(
            status_code=404,
            detail="No se encontró el perfil del paciente asociado a esta cuenta.",
        )

    if not paciente.estado:
        raise HTTPException(
            status_code=403,
            detail="El paciente está inactivo.",
        )

    return MiPerfilPacienteResponse(
        id=paciente.id,
        correo=usuario.correo,
        nombres=paciente.nombres,
        apellidos=paciente.apellidos,
        ci=paciente.ci,
        fecha_nacimiento=paciente.fecha_nacimiento,
        sexo=paciente.sexo,
        telefono=paciente.telefono,
        contacto_emergencia=paciente.contacto_emergencia,
        direccion=paciente.direccion,
        estado=paciente.estado,
    )


def actualizar_mi_perfil(
    db: Session,
    usuario,
    datos: MiPerfilPacienteActualizar,
) -> MiPerfilPacienteResponse:
    """Actualiza los datos autorizados del paciente autenticado (CU08)."""
    if nombre_rol_actual(usuario) != "paciente":
        raise HTTPException(
            status_code=403,
            detail="Acceso disponible únicamente para pacientes.",
        )

    paciente = repo.obtener_paciente_por_usuario_id(
        db,
        usuario.id,
    )

    if not paciente:
        raise HTTPException(
            status_code=404,
            detail="No se encontró el perfil del paciente asociado a esta cuenta.",
        )

    if not paciente.estado:
        raise HTTPException(
            status_code=403,
            detail="El paciente está inactivo.",
        )

    try:
        # Solo se actualizan los campos enviados en el request.
        paciente = repo.actualizar_paciente(
            db,
            paciente,
            datos,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion="ACTUALIZAR_MI_PERFIL",
            entidad_afectada="paciente",
            id_registro_afectado=paciente.id,
            descripcion="El paciente actualizó su perfil",
        )

        db.commit()

        return obtener_mi_perfil(db, usuario)

    except Exception:
        db.rollback()
        raise

