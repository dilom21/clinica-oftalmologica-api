from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.gestion_pacientes.repositories import repository as repo_paciente
from app.modules.gestion_pacientes.schemas.schemas import PacienteCrear
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    RegistroPacienteRequest,
)

NOMBRE_ROL_PACIENTE = "Paciente"


def _obtener_rol_paciente(db: Session):
    rol = repo.obtener_rol_por_nombre(db, NOMBRE_ROL_PACIENTE)

    if not rol or not rol.estado:
        raise HTTPException(
            status_code=400,
            detail="No fue posible crear la cuenta.",
        )

    return rol


def registrar_cuenta_paciente(
    db: Session,
    datos: RegistroPacienteRequest,
    ip: str | None = None,
) -> dict:
    """Crea la cuenta móvil de un paciente.

    Escenario A: el paciente ya existe (por CI), se verifica identidad y solo
    se crea el Usuario, vinculando paciente.usuario_id.
    Escenario B: el paciente no existe, se crean Usuario y Paciente en una
    misma transacción.
    """
    if repo.obtener_usuario_por_correo(db, datos.correo):
        raise HTTPException(
            status_code=409,
            detail="El correo electrónico ya está registrado.",
        )

    rol_paciente = _obtener_rol_paciente(db)

    paciente = repo_paciente.obtener_paciente_por_ci(db, datos.ci)

    try:
        if paciente is None:
            return _crear_paciente_nuevo(db, datos, rol_paciente, ip)

        return _vincular_paciente_existente(
            db,
            datos,
            rol_paciente,
            paciente,
            ip,
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def _crear_paciente_nuevo(
    db: Session,
    datos: RegistroPacienteRequest,
    rol_paciente,
    ip: str | None,
) -> dict:
    usuario = repo.crear_usuario(
        db=db,
        correo=datos.correo,
        password_hash=hash_password(datos.password),
        rol_id=rol_paciente.id,
        estado=True,
    )

    datos_paciente = PacienteCrear(
        nombres=datos.nombres,
        apellidos=datos.apellidos,
        ci=datos.ci,
        fecha_nacimiento=datos.fecha_nacimiento,
        sexo=datos.sexo,
        telefono=datos.telefono,
        contacto_emergencia=datos.contacto_emergencia,
        direccion=datos.direccion,
        estado=True,
        usuario_id=usuario.id,
    )
    paciente = repo_paciente.crear_paciente(db, datos_paciente)

    repo.registrar_bitacora(
        db=db,
        usuario_id=usuario.id,
        accion="REGISTRO_PACIENTE",
        ip=ip,
        entidad_afectada="paciente",
        id_registro_afectado=paciente.id,
        descripcion="Paciente registrado desde la aplicación móvil",
    )

    db.commit()

    return {"message": "Cuenta creada correctamente"}


def _vincular_paciente_existente(
    db: Session,
    datos: RegistroPacienteRequest,
    rol_paciente,
    paciente,
    ip: str | None,
) -> dict:
    if not paciente.estado:
        raise HTTPException(
            status_code=400,
            detail="No fue posible crear la cuenta.",
        )

    if paciente.usuario_id is not None:
        raise HTTPException(
            status_code=409,
            detail="El paciente ya posee una cuenta asociada.",
        )

    if (
        paciente.fecha_nacimiento != datos.fecha_nacimiento
        or (paciente.telefono or "").strip() != (datos.telefono or "").strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="No se pudo verificar la información del paciente.",
        )

    usuario = repo.crear_usuario(
        db=db,
        correo=datos.correo,
        password_hash=hash_password(datos.password),
        rol_id=rol_paciente.id,
        estado=True,
    )

    repo_paciente.asignar_usuario_a_paciente(db, paciente, usuario.id)

    repo.registrar_bitacora(
        db=db,
        usuario_id=usuario.id,
        accion="VINCULAR_CUENTA_PACIENTE",
        ip=ip,
        entidad_afectada="paciente",
        id_registro_afectado=paciente.id,
        descripcion="Cuenta móvil vinculada a paciente existente",
    )

    db.commit()

    return {"message": "Cuenta creada correctamente"}
