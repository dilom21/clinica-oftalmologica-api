from datetime import date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_agenda_citas.repositories import repository as repo
from app.modules.gestion_agenda_citas.services import service as service_cu09
from app.modules.gestion_agenda_citas.schemas.schemas import (
    DURACION_CITA_MINUTOS,
    ESTADO_CITA_CANCELADA,
    ESTADO_CITA_INICIAL,
    ESTADOS_CITA_VALIDOS,
)
from app.modules.gestion_pacientes.repositories.repository import (
    obtener_paciente_por_id,
    obtener_paciente_por_usuario_id,
)
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


# =========================================================
# CU10 - GESTIONAR CITAS MÉDICAS
# Reglas por rol y validaciones de citas.
# =========================================================

BITACORA_CREAR_CITA = "CREAR_CITA"
BITACORA_REPROGRAMAR_CITA = "REPROGRAMAR_CITA"
BITACORA_CAMBIAR_ESTADO_CITA = "CAMBIAR_ESTADO_CITA"
BITACORA_CANCELAR_CITA = "CANCELAR_CITA"

ENTIDAD_CITA = "cita"

CANAL_MOVIL = "MOVIL"
CANAL_WEB = "WEB"


def _es_paciente(usuario) -> bool:
    """Indica si el usuario autenticado tiene rol PACIENTE."""
    return bool(
        usuario is not None
        and nombre_rol_actual(usuario) == "paciente"
    )


def _obtener_paciente_autenticado(
    db: Session,
    usuario,
):
    """Paciente asociado al usuario autenticado (rol móvil)."""
    paciente = obtener_paciente_por_usuario_id(db, usuario.id)

    if not paciente:
        raise HTTPException(
            status_code=404,
            detail="Perfil de paciente no encontrado",
        )

    if not getattr(paciente, "estado", True):
        raise HTTPException(
            status_code=403,
            detail="El paciente está inactivo",
        )

    return paciente

_FECHA_REFERENCIA = date(1900, 1, 1)


def _a_datetime(valor: time) -> datetime:
    return datetime.combine(_FECHA_REFERENCIA, valor)


def _calcular_hora_fin(hora_inicio: time) -> time:
    """La duración de una cita es fija (regla de negocio CU09/CU10).

    El cliente nunca envía `hora_fin`: se calcula con `DURACION_CITA_MINUTOS`.
    """
    return (
        _a_datetime(hora_inicio) + timedelta(minutes=DURACION_CITA_MINUTOS)
    ).time()


def _validar_fecha_no_pasada(fecha: date) -> None:
    if fecha < date.today():
        raise HTTPException(
            status_code=400,
            detail="La fecha de la cita no puede ser pasada",
        )


def _obtener_paciente_activo(
    db: Session,
    paciente_id: int,
):
    paciente = obtener_paciente_por_id(db, paciente_id)

    if not paciente or not getattr(paciente, "estado", True):
        raise HTTPException(
            status_code=404,
            detail="Paciente no encontrado o inactivo",
        )

    return paciente


def _obtener_oftalmologo_activo(
    db: Session,
    oftalmologo_id: int,
):
    oftalmologo = repo.obtener_oftalmologo_activo_por_id(
        db,
        oftalmologo_id,
    )

    if not oftalmologo:
        raise HTTPException(
            status_code=404,
            detail="Oftalmólogo no encontrado o inactivo",
        )

    return oftalmologo


def _obtener_cita(
    db: Session,
    cita_id: int,
):
    cita = repo.obtener_cita_por_id(db, cita_id)

    if not cita:
        raise HTTPException(
            status_code=404,
            detail="Cita no encontrada",
        )

    return cita


def _intervalo_disponible_cu09(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
    hora_inicio: time,
    hora_fin: time,
) -> bool:
    """Valida la disponibilidad de un intervalo con la lógica de CU09.

    `GET /disponibilidad` ya descuenta horario no configurado, bloqueos
    activos y citas que ocupan horario; aquí solo se verifica que el tramo
    de la cita (DURACION_CITA_MINUTOS) quepa dentro de un turno libre real.
    """
    disponibilidad = service_cu09.consultar_disponibilidad(
        db,
        oftalmologo_id,
        fecha,
    )

    if not disponibilidad.tiene_horario:
        return False

    inicio = _a_datetime(hora_inicio)
    fin = _a_datetime(hora_fin)

    return any(
        _a_datetime(intervalo.hora_inicio) <= inicio
        and fin <= _a_datetime(intervalo.hora_fin)
        for intervalo in disponibilidad.intervalos_disponibles
    )


def _validar_estado_cita(estado: str) -> str:
    normalizado = (estado or "").strip().upper()

    if normalizado not in ESTADOS_CITA_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Estado de cita inválido: {estado}. "
                f"Valores válidos: {', '.join(ESTADOS_CITA_VALIDOS)}"
            ),
        )

    return normalizado


def registrar_cita(
    db: Session,
    datos,
    usuario,
):
    """Registra una cita para un paciente con un oftalmólogo.

    Valida paciente activo, oftalmólogo activo, fecha no pasada y que el
    horario de la cita (DURACION_CITA_MINUTOS) esté disponible según CU09.
    El estado inicial de la cita es PROGRAMADA.

    Si el usuario autenticado es PACIENTE, la cita se asocia forzosamente a su
    propio registro de paciente y el canal queda en MOVIL.
    """
    es_paciente = _es_paciente(usuario)

    if es_paciente:
        paciente_autenticado = _obtener_paciente_autenticado(db, usuario)
        paciente_id = paciente_autenticado.id

        if datos.paciente_id is not None and datos.paciente_id != paciente_id:
            raise HTTPException(
                status_code=403,
                detail="No puedes registrar citas para otro paciente",
            )
    else:
        _obtener_paciente_activo(db, datos.paciente_id)
        paciente_id = datos.paciente_id

    oftalmologo = _obtener_oftalmologo_activo(db, datos.oftalmologo_id)
    _validar_fecha_no_pasada(datos.fecha)

    hora_fin = _calcular_hora_fin(datos.hora_inicio)

    if not _intervalo_disponible_cu09(
        db,
        oftalmologo.id,
        datos.fecha,
        datos.hora_inicio,
        hora_fin,
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "El horario seleccionado no está disponible para el "
                "oftalmólogo en la fecha indicada"
            ),
        )

    try:
        cita = repo.crear_cita(
            db,
            paciente_id=paciente_id,
            oftalmologo_id=oftalmologo.id,
            fecha=datos.fecha,
            hora_inicio=datos.hora_inicio,
            hora_fin=hora_fin,
            motivo=datos.motivo,
            observaciones=datos.observaciones,
            estado=ESTADO_CITA_INICIAL,
            canal=CANAL_MOVIL if es_paciente else CANAL_WEB,
            creado_por_usuario_id=getattr(usuario, "id", None),
        )

        registrar_bitacora(
            db=db,
            usuario_id=getattr(usuario, "id", None),
            accion=BITACORA_CREAR_CITA,
            entidad_afectada=ENTIDAD_CITA,
            id_registro_afectado=cita.id,
            descripcion="Cita registrada",
        )

        db.commit()
        db.refresh(cita)

        return cita

    except Exception:
        db.rollback()
        raise


def listar_citas(
    db: Session,
    *,
    fecha: date | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
    estado: str | None = None,
    usuario=None,
):
    """Consulta citas aplicando filtros opcionales de CU10.

    Si el usuario autenticado es PACIENTE, la consulta se limita forzosamente
    a las citas de su propio registro de paciente.
    """
    if _es_paciente(usuario):
        paciente_autenticado = _obtener_paciente_autenticado(db, usuario)

        if (
            paciente_id is not None
            and paciente_id != paciente_autenticado.id
        ):
            raise HTTPException(
                status_code=403,
                detail="No puedes consultar citas de otro paciente",
            )

        paciente_id = paciente_autenticado.id

    estado_normalizado = (
        _validar_estado_cita(estado)
        if estado is not None
        else None
    )

    return repo.listar_citas(
        db,
        fecha=fecha,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
        estado=estado_normalizado,
    )


def obtener_cita(
    db: Session,
    cita_id: int,
    usuario=None,
):
    """Detalle de una cita por id.

    Si el usuario autenticado es PACIENTE, solo puede ver sus propias citas.
    """
    cita = _obtener_cita(db, cita_id)

    if _es_paciente(usuario):
        paciente_autenticado = _obtener_paciente_autenticado(db, usuario)
        if cita.paciente_id != paciente_autenticado.id:
            raise HTTPException(
                status_code=404,
                detail="Cita no encontrada",
            )

    return cita


def reprogramar_cita(
    db: Session,
    cita_id: int,
    datos,
    usuario,
):
    """Actualiza campos modificables de una cita.

    Si cambian `fecha` u `hora_inicio` se recalcula `hora_fin`
    (hora_inicio + `DURACION_CITA_MINUTOS`) y se vuelve a validar la
    disponibilidad con la lógica de CU09 antes de actualizar.
    """
    cita = _obtener_cita(db, cita_id)

    if _es_paciente(usuario):
        paciente_autenticado = _obtener_paciente_autenticado(db, usuario)
        if cita.paciente_id != paciente_autenticado.id:
            raise HTTPException(
                status_code=404,
                detail="Cita no encontrada",
            )

    nueva_fecha = datos.fecha if datos.fecha is not None else cita.fecha
    nueva_hora_inicio = (
        datos.hora_inicio
        if datos.hora_inicio is not None
        else cita.hora_inicio
    )
    nuevo_motivo = datos.motivo if datos.motivo is not None else cita.motivo
    nuevas_observaciones = (
        datos.observaciones
        if datos.observaciones is not None
        else cita.observaciones
    )

    if (
        cita.fecha == nueva_fecha
        and cita.hora_inicio == nueva_hora_inicio
        and cita.motivo == nuevo_motivo
        and cita.observaciones == nuevas_observaciones
    ):
        return cita

    if (
        cita.fecha != nueva_fecha
        or cita.hora_inicio != nueva_hora_inicio
    ):
        _validar_fecha_no_pasada(nueva_fecha)
        nueva_hora_fin = _calcular_hora_fin(nueva_hora_inicio)

        if not _intervalo_disponible_cu09(
            db,
            cita.oftalmologo_id,
            nueva_fecha,
            nueva_hora_inicio,
            nueva_hora_fin,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "El horario seleccionado no está disponible para el "
                    "oftalmólogo en la fecha indicada"
                ),
            )
    else:
        nueva_hora_fin = cita.hora_fin

    try:
        cita = repo.actualizar_cita(
            db,
            cita,
            fecha=nueva_fecha,
            hora_inicio=nueva_hora_inicio,
            hora_fin=nueva_hora_fin,
            motivo=nuevo_motivo,
            observaciones=nuevas_observaciones,
        )

        registrar_bitacora(
            db=db,
            usuario_id=getattr(usuario, "id", None),
            accion=BITACORA_REPROGRAMAR_CITA,
            entidad_afectada=ENTIDAD_CITA,
            id_registro_afectado=cita.id,
            descripcion="Cita reprogramada",
        )

        db.commit()
        db.refresh(cita)

        return cita

    except Exception:
        db.rollback()
        raise


def cambiar_estado_cita(
    db: Session,
    cita_id: int,
    estado: str,
    usuario,
):
    """Cambia el estado de una cita (no se elimina físicamente).

    Un paciente solo puede CANCELAR sus propias citas; no puede administrar
    estados (CONFIRMADA, EN_ESPERA, ATENDIDA, etc.).
    """
    cita = _obtener_cita(db, cita_id)
    estado_normalizado = _validar_estado_cita(estado)

    if _es_paciente(usuario):
        paciente_autenticado = _obtener_paciente_autenticado(db, usuario)
        if cita.paciente_id != paciente_autenticado.id:
            raise HTTPException(
                status_code=404,
                detail="Cita no encontrada",
            )
        if estado_normalizado != ESTADO_CITA_CANCELADA:
            raise HTTPException(
                status_code=403,
                detail="Los pacientes solo pueden cancelar sus propias citas",
            )

    if cita.estado == estado_normalizado:
        return cita

    try:
        cita = repo.actualizar_estado_cita(
            db,
            cita,
            estado_normalizado,
        )

        registrar_bitacora(
            db=db,
            usuario_id=getattr(usuario, "id", None),
            accion=BITACORA_CAMBIAR_ESTADO_CITA,
            entidad_afectada=ENTIDAD_CITA,
            id_registro_afectado=cita.id,
            descripcion=(
                f"Estado de cita cambiado a {estado_normalizado}"
            ),
        )

        db.commit()
        db.refresh(cita)

        return cita

    except Exception:
        db.rollback()
        raise


def cancelar_cita(
    db: Session,
    cita_id: int,
    usuario,
):
    """Cancela una cita cambiando su estado a CANCELADA.

    Un paciente solo puede cancelar citas propias.
    """
    cita = _obtener_cita(db, cita_id)

    if _es_paciente(usuario):
        paciente_autenticado = _obtener_paciente_autenticado(db, usuario)
        if cita.paciente_id != paciente_autenticado.id:
            raise HTTPException(
                status_code=404,
                detail="Cita no encontrada",
            )

    if cita.estado == ESTADO_CITA_CANCELADA:
        return cita

    try:
        cita = repo.actualizar_estado_cita(
            db,
            cita,
            ESTADO_CITA_CANCELADA,
        )

        registrar_bitacora(
            db=db,
            usuario_id=getattr(usuario, "id", None),
            accion=BITACORA_CANCELAR_CITA,
            entidad_afectada=ENTIDAD_CITA,
            id_registro_afectado=cita.id,
            descripcion="Cita cancelada",
        )

        db.commit()
        db.refresh(cita)

        return cita

    except Exception:
        db.rollback()
        raise
