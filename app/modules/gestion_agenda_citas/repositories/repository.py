from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.gestion_agenda_citas.models.models import (
    BloqueoHorario,
    Cita,
    HorarioOftalmologo,
    Oftalmologo,
)


# =========================================================
# CU09 - CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA
# =========================================================

ESTADOS_CITA_CANCELADA = ("CANCELADA",)


def obtener_oftalmologo_por_id(
    db: Session,
    oftalmologo_id: int,
):
    return db.get(Oftalmologo, oftalmologo_id)


def listar_oftalmologos_activos(db: Session):
    stmt = (
        select(Oftalmologo)
        .where(Oftalmologo.estado.is_(True))
        .order_by(
            Oftalmologo.apellidos,
            Oftalmologo.nombres,
        )
    )

    return db.scalars(stmt).all()


def obtener_oftalmologo_activo_por_id(
    db: Session,
    oftalmologo_id: int,
):
    stmt = (
        select(Oftalmologo)
        .where(
            Oftalmologo.id == oftalmologo_id,
            Oftalmologo.estado.is_(True),
        )
    )

    return db.scalar(stmt)


def obtener_horarios_por_oftalmologo_y_dia(
    db: Session,
    oftalmologo_id: int,
    dia_semana: int,
):
    stmt = (
        select(HorarioOftalmologo)
        .where(
            HorarioOftalmologo.oftalmologo_id == oftalmologo_id,
            HorarioOftalmologo.dia_semana == dia_semana,
            HorarioOftalmologo.estado.is_(True),
        )
        .order_by(
            HorarioOftalmologo.hora_inicio,
            HorarioOftalmologo.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_bloqueos_por_oftalmologo_y_fecha(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
):
    stmt = (
        select(BloqueoHorario)
        .where(
            BloqueoHorario.oftalmologo_id == oftalmologo_id,
            BloqueoHorario.fecha == fecha,
            BloqueoHorario.estado.is_(True),
        )
        .order_by(
            BloqueoHorario.hora_inicio,
            BloqueoHorario.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_citas_por_oftalmologo_y_fecha(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
):
    stmt = (
        select(Cita)
        .where(
            Cita.oftalmologo_id == oftalmologo_id,
            Cita.fecha == fecha,
            Cita.estado.notin_(ESTADOS_CITA_CANCELADA),
        )
        .order_by(
            Cita.hora_inicio,
            Cita.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_citas_futuras_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
    desde: date,
):
    """Citas desde `desde` (inclusive) cuyo estado ocupa horario (CU11)."""
    stmt = (
        select(Cita)
        .where(
            Cita.oftalmologo_id == oftalmologo_id,
            Cita.fecha >= desde,
            Cita.estado.notin_(ESTADOS_CITA_CANCELADA),
        )
        .order_by(
            Cita.fecha,
            Cita.hora_inicio,
            Cita.id,
        )
    )

    return db.scalars(stmt).all()


# =========================================================
# CU11 - CONFIGURAR DISPONIBILIDAD DEL OFTALMÓLOGO
# =========================================================

def obtener_horario_por_id(
    db: Session,
    horario_id: int,
):
    return db.get(HorarioOftalmologo, horario_id)


def obtener_bloqueo_por_id(
    db: Session,
    bloqueo_id: int,
):
    return db.get(BloqueoHorario, bloqueo_id)


def obtener_horarios_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
):
    """Todos los horarios (activos e inactivos) de un oftalmólogo."""
    stmt = (
        select(HorarioOftalmologo)
        .where(HorarioOftalmologo.oftalmologo_id == oftalmologo_id)
        .order_by(
            HorarioOftalmologo.dia_semana,
            HorarioOftalmologo.hora_inicio,
            HorarioOftalmologo.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_horarios_activos_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
):
    """Horarios activos de cualquier día (para simular cambios semanales)."""
    stmt = (
        select(HorarioOftalmologo)
        .where(
            HorarioOftalmologo.oftalmologo_id == oftalmologo_id,
            HorarioOftalmologo.estado.is_(True),
        )
        .order_by(
            HorarioOftalmologo.dia_semana,
            HorarioOftalmologo.hora_inicio,
            HorarioOftalmologo.id,
        )
    )

    return db.scalars(stmt).all()


def obtener_bloqueos_por_oftalmologo(
    db: Session,
    oftalmologo_id: int,
):
    """Todos los bloqueos (activos e inactivos) de un oftalmólogo."""
    stmt = (
        select(BloqueoHorario)
        .where(BloqueoHorario.oftalmologo_id == oftalmologo_id)
        .order_by(
            BloqueoHorario.fecha,
            BloqueoHorario.hora_inicio,
            BloqueoHorario.id,
        )
    )

    return db.scalars(stmt).all()


def crear_horario_oftalmologo(
    db: Session,
    oftalmologo_id: int,
    dia_semana: int,
    hora_inicio,
    hora_fin,
    estado: bool = True,
):
    horario = HorarioOftalmologo(
        oftalmologo_id=oftalmologo_id,
        dia_semana=dia_semana,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        estado=estado,
    )

    db.add(horario)
    db.flush()
    db.refresh(horario)

    return horario


def actualizar_horario_oftalmologo(
    db: Session,
    horario: HorarioOftalmologo,
    dia_semana: int,
    hora_inicio,
    hora_fin,
):
    horario.dia_semana = dia_semana
    horario.hora_inicio = hora_inicio
    horario.hora_fin = hora_fin

    db.flush()
    db.refresh(horario)

    return horario


def cambiar_estado_horario_oftalmologo(
    db: Session,
    horario: HorarioOftalmologo,
    estado: bool,
):
    horario.estado = estado

    db.flush()
    db.refresh(horario)

    return horario


def crear_bloqueo_horario(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
    hora_inicio,
    hora_fin,
    motivo: str | None = None,
    estado: bool = True,
):
    bloqueo = BloqueoHorario(
        oftalmologo_id=oftalmologo_id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        motivo=motivo,
        estado=estado,
        fecha_registro=datetime.now(timezone.utc),
    )

    db.add(bloqueo)
    db.flush()
    db.refresh(bloqueo)

    return bloqueo


def actualizar_bloqueo_horario(
    db: Session,
    bloqueo: BloqueoHorario,
    fecha: date,
    hora_inicio,
    hora_fin,
    motivo: str | None = None,
):
    bloqueo.fecha = fecha
    bloqueo.hora_inicio = hora_inicio
    bloqueo.hora_fin = hora_fin
    bloqueo.motivo = motivo

    db.flush()
    db.refresh(bloqueo)

    return bloqueo


def cambiar_estado_bloqueo_horario(
    db: Session,
    bloqueo: BloqueoHorario,
    estado: bool,
):
    bloqueo.estado = estado

    db.flush()
    db.refresh(bloqueo)

    return bloqueo

# =========================================================
# CU10 - GESTIONAR CITAS MÉDICAS
# =========================================================


def _se_solapan(inicio_a, fin_a, inicio_b, fin_b) -> bool:
    return inicio_a < fin_b and inicio_b < fin_a


def crear_cita(
    db: Session,
    *,
    paciente_id: int,
    oftalmologo_id: int,
    fecha: date,
    hora_inicio,
    hora_fin,
    motivo: str | None = None,
    observaciones: str | None = None,
    estado: str,
    creado_por_usuario_id: int | None = None,
) -> Cita:
    ahora = datetime.now(timezone.utc)
    cita = Cita(
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
        fecha=fecha,
        hora_inicio=hora_inicio,
        hora_fin=hora_fin,
        motivo=motivo,
        observaciones=observaciones,
        estado=estado,
        creado_por_usuario_id=creado_por_usuario_id,
        fecha_registro=ahora,
        fecha_actualizacion=ahora,
    )

    db.add(cita)
    db.flush()
    db.refresh(cita)

    return cita


def listar_citas(
    db: Session,
    *,
    fecha: date | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
    estado: str | None = None,
):
    condiciones = []
    if fecha is not None:
        condiciones.append(Cita.fecha == fecha)
    if paciente_id is not None:
        condiciones.append(Cita.paciente_id == paciente_id)
    if oftalmologo_id is not None:
        condiciones.append(Cita.oftalmologo_id == oftalmologo_id)
    if estado is not None:
        condiciones.append(Cita.estado == estado)

    stmt = select(Cita)
    if condiciones:
        stmt = stmt.where(*condiciones)
    stmt = stmt.order_by(
        Cita.fecha,
        Cita.hora_inicio,
        Cita.id,
    )

    return db.scalars(stmt).all()


def obtener_cita_por_id(
    db: Session,
    cita_id: int,
):
    return db.get(Cita, cita_id)


def actualizar_cita(
    db: Session,
    cita: Cita,
    *,
    fecha: date,
    hora_inicio,
    hora_fin,
    motivo: str | None = None,
    observaciones: str | None = None,
) -> Cita:
    cita.fecha = fecha
    cita.hora_inicio = hora_inicio
    cita.hora_fin = hora_fin
    cita.motivo = motivo
    cita.observaciones = observaciones
    cita.fecha_actualizacion = datetime.now(timezone.utc)

    db.flush()
    db.refresh(cita)

    return cita


def actualizar_estado_cita(
    db: Session,
    cita: Cita,
    estado: str,
) -> Cita:
    cita.estado = estado
    cita.fecha_actualizacion = datetime.now(timezone.utc)

    db.flush()
    db.refresh(cita)

    return cita


def verificar_disponibilidad_horario(
    db: Session,
    *,
    oftalmologo_id: int,
    fecha: date,
    hora_inicio,
    hora_fin,
    excluir_cita_id: int | None = None,
) -> bool:
    """Indica si un intervalo cae dentro del horario configurado y no choca
    con bloqueos activos ni con otras citas que ocupan horario.

    Reutiliza la misma regla de ocupación que CU09 (solo CANCELADA libera).
    """
    dia_semana = fecha.isoweekday()
    horarios = obtener_horarios_por_oftalmologo_y_dia(
        db,
        oftalmologo_id,
        dia_semana,
    )

    if not any(
        h.hora_inicio <= hora_inicio and hora_fin <= h.hora_fin
        for h in horarios
    ):
        return False

    bloqueos = obtener_bloqueos_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )
    for bloqueo in bloqueos:
        if _se_solapan(
            hora_inicio,
            hora_fin,
            bloqueo.hora_inicio,
            bloqueo.hora_fin,
        ):
            return False

    citas = obtener_citas_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )
    for cita in citas:
        if excluir_cita_id is not None and cita.id == excluir_cita_id:
            continue
        if _se_solapan(
            hora_inicio,
            hora_fin,
            cita.hora_inicio,
            cita.hora_fin,
        ):
            return False

    return True

