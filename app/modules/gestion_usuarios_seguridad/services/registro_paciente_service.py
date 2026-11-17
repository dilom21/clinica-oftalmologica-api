from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.tenancy.engine_registry import TenantEngineRegistry
from app.core.tenancy.exceptions import (
    ControlPlaneConsistencyError,
    InvalidTenantDatabaseNameError,
    SubscriptionInactiveError,
    TenantConnectionError,
    TenantDatabaseUnavailableError,
    TenantInactiveError,
    TenantNotFoundError,
)
from app.core.tenancy.repository import TenantControlPlaneRepository
from app.core.tenancy.resolver import TenantResolver
from app.modules.gestion_historial_clinico.repositories import (
    repository as repo_historial,
)
from app.modules.gestion_pacientes.repositories import repository as repo_paciente
from app.modules.gestion_pacientes.schemas.schemas import PacienteCrear
from app.modules.gestion_usuarios_seguridad.repositories import repository as repo
from app.modules.gestion_usuarios_seguridad.schemas.schemas import (
    RegistroPacienteRequest,
    TenantRegistroPacienteRequest,
)

NOMBRE_ROL_PACIENTE = "Paciente"
MENSAJE_PACIENTE_EXISTENTE = (
    "Ya existe un registro de paciente con esa identificación. "
    "Debe verificar su identidad directamente con la clínica."
)


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

    Escenario A: el paciente no existe, se crean Usuario y Paciente y se
    asegura su HistorialClinico en una misma transacción.
    Escenario B: el paciente ya existe (por CI), se verifica identidad, se
    crea el Usuario, se vincula paciente.usuario_id y se asegura que el
    paciente posea su HistorialClinico (creándolo solo si falta).

    Usuario + Paciente/Vinculación + HistorialClinico + bitácora se confirman
    con un único commit; cualquier fallo revierte todo.
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


def registrar_cuenta_paciente_tenant(
    datos: TenantRegistroPacienteRequest,
    control_plane_repository: TenantControlPlaneRepository,
    tenant_registry: TenantEngineRegistry,
    ip: str | None = None,
) -> dict:
    """Autorregistro seguro en la base resuelta por el Control Plane."""
    resolver = TenantResolver(control_plane_repository)
    try:
        context = resolver.resolver_por_codigo_empresa(datos.empresa_codigo)
        resolver.require_active_database(context)
        tenant_db = tenant_registry.get_session(context)
    except (TenantNotFoundError, TenantInactiveError) as exc:
        raise HTTPException(status_code=404, detail="Clínica no disponible") from exc
    except SubscriptionInactiveError as exc:
        raise HTTPException(
            status_code=403,
            detail="La clínica no está disponible para registros",
        ) from exc
    except (
        TenantDatabaseUnavailableError,
        TenantConnectionError,
        InvalidTenantDatabaseNameError,
        ControlPlaneConsistencyError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail="La clínica no está disponible temporalmente",
        ) from exc

    try:
        tenant_db.info["tenant_context"] = context
        _verificar_identidad_base_tenant(tenant_db, context.database_name)
        registro = RegistroPacienteRequest(
            **datos.model_dump(exclude={"empresa_codigo"})
        )
        return _registrar_paciente_nuevo_tenant(tenant_db, registro, ip)
    except OperationalError as exc:
        tenant_db.rollback()
        raise HTTPException(
            status_code=503,
            detail="La clínica no está disponible temporalmente",
        ) from exc
    finally:
        tenant_db.close()


def _verificar_identidad_base_tenant(db: Session, database_name: str) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    actual = db.scalar(text("select current_database()"))
    if actual != database_name:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="La clínica no está disponible temporalmente",
        )


def _bloquear_claves_autorregistro(db: Session, datos: RegistroPacienteRequest) -> None:
    """Serializa registros iguales entre procesos mientras dura la transacción."""
    if db.get_bind().dialect.name != "postgresql":
        return
    claves = sorted(
        {
            f"registro-paciente:ci:{datos.ci.strip().lower()}",
            f"registro-paciente:correo:{datos.correo.strip().lower()}",
        }
    )
    for clave in claves:
        db.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:clave, 0))"),
            {"clave": clave},
        )


def _registrar_paciente_nuevo_tenant(
    db: Session,
    datos: RegistroPacienteRequest,
    ip: str | None,
) -> dict:
    try:
        _bloquear_claves_autorregistro(db, datos)

        if repo.obtener_usuario_por_correo(db, datos.correo):
            raise HTTPException(
                status_code=409,
                detail="El correo electrónico ya está registrado.",
            )

        rol_paciente = _obtener_rol_paciente(db)
        if repo_paciente.obtener_paciente_por_ci_normalizado(
            db, datos.ci,
        ) is not None:
            raise HTTPException(
                status_code=409,
                detail=MENSAJE_PACIENTE_EXISTENTE,
            )

        return _crear_paciente_nuevo(db, datos, rol_paciente, ip)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="No fue posible completar el registro porque los datos ya existen.",
        ) from exc
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

    # Todo paciente registrado debe poseer su historial clínico (1 a 1).
    repo_historial.asegurar_historial_clinico_para_paciente(db, paciente)

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

    # Un paciente presencial antiguo puede no tener historial: se asegura aquí
    # (idempotente: si ya existe se reutiliza y no se crea otro).
    repo_historial.asegurar_historial_clinico_para_paciente(db, paciente)

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
