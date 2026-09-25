from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_agenda_citas.models.models import Oftalmologo
from app.modules.gestion_agenda_citas.repositories import repository as repo

from app.modules.gestion_agenda_citas.schemas.schemas import (
    AgendaRespuesta,
    CitaAgendaRespuesta,
    DisponibilidadRespuesta,
    IntervaloHorarioRespuesta,
    OftalmologoResumidoRespuesta,
)

from app.modules.gestion_agenda_citas.services.disponibilidad import (
    calcular_intervalos_disponibles,
    obtener_dia_semana,
)


# =========================================================
# CU09 - CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA
# =========================================================

_ROL_ADMINISTRADOR = "administrador"
_ROL_OFTALMOLOGO = "oftalmologo"
_ROL_RECEPCIONISTA = "recepcionista"

_ROLES_CON_ACCESO_A_CUALQUIER_AGENDA = {
    _ROL_ADMINISTRADOR,
    _ROL_RECEPCIONISTA,
}


def _resumir_oftalmologo(oftalmologo: Oftalmologo) -> OftalmologoResumidoRespuesta:
    return OftalmologoResumidoRespuesta.model_validate(oftalmologo)


def _obtener_oftalmologo_activo(
    db: Session,
    oftalmologo_id: int,
) -> Oftalmologo:
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


def _es_rol_oftalmologo(usuario) -> bool:
    return nombre_rol_actual(usuario) == _ROL_OFTALMOLOGO


def _autorizar_consulta_agenda(usuario, oftalmologo: Oftalmologo) -> None:
    """Reglas contextuales de consulta de agenda por rol (CU09).

    - Administrador y Recepcionista: agenda de cualquier oftalmólogo.
    - Oftalmólogo: únicamente su propia agenda (oftalmologo.usuario_id
      == usuario.id); si intenta consultar otra, 403.
    - Resto de roles (incluido Paciente): sin acceso a la agenda -> 403.
    """
    rol = nombre_rol_actual(usuario)

    if rol in _ROLES_CON_ACCESO_A_CUALQUIER_AGENDA:
        return

    if rol == _ROL_OFTALMOLOGO:
        if oftalmologo.usuario_id != usuario.id:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Un oftalmólogo solo puede consultar su propia agenda"
                ),
            )
        return

    raise HTTPException(
        status_code=403,
        detail=(
            "No tiene permiso para consultar la agenda de un oftalmólogo"
        ),
    )


def listar_oftalmologos_activos(
    db: Session,
    usuario=None,
):
    """Lista oftalmólogos activos.

    Para el rol Oftalmólogo devuelve únicamente su propio registro, de modo
    que la UI no permita seleccionar agendas ajenas.
    """
    oftalmologos = repo.listar_oftalmologos_activos(db)

    if usuario is not None and _es_rol_oftalmologo(usuario):
        oftalmologos = [
            oftalmologo
            for oftalmologo in oftalmologos
            if oftalmologo.usuario_id == usuario.id
        ]

    return oftalmologos


def consultar_disponibilidad(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
) -> DisponibilidadRespuesta:
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    dia_semana = obtener_dia_semana(fecha)

    horarios = repo.obtener_horarios_por_oftalmologo_y_dia(
        db,
        oftalmologo_id,
        dia_semana,
    )

    horarios_base = [
        IntervaloHorarioRespuesta(
            hora_inicio=horario.hora_inicio,
            hora_fin=horario.hora_fin,
        )
        for horario in horarios
        if horario.hora_inicio < horario.hora_fin
    ]

    if not horarios_base:
        return DisponibilidadRespuesta(
            oftalmologo=_resumir_oftalmologo(oftalmologo),
            fecha=fecha,
            tiene_horario=False,
            horarios_base=[],
            intervalos_disponibles=[],
        )

    bloqueos = repo.obtener_bloqueos_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )
    citas = repo.obtener_citas_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )

    ocupaciones = [
        (bloqueo.hora_inicio, bloqueo.hora_fin)
        for bloqueo in bloqueos
    ] + [
        (cita.hora_inicio, cita.hora_fin)
        for cita in citas
    ]

    intervalos_disponibles = [
        IntervaloHorarioRespuesta(
            hora_inicio=inicio,
            hora_fin=fin,
        )
        for inicio, fin in calcular_intervalos_disponibles(
            horarios_base=[(h.hora_inicio, h.hora_fin) for h in horarios],
            ocupaciones=ocupaciones,
        )
    ]

    return DisponibilidadRespuesta(
        oftalmologo=_resumir_oftalmologo(oftalmologo),
        fecha=fecha,
        tiene_horario=True,
        horarios_base=horarios_base,
        intervalos_disponibles=intervalos_disponibles,
    )


def consultar_agenda(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
    usuario=None,
) -> AgendaRespuesta:
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)

    if usuario is not None:
        _autorizar_consulta_agenda(usuario, oftalmologo)

    dia_semana = obtener_dia_semana(fecha)

    horarios = repo.obtener_horarios_por_oftalmologo_y_dia(
        db,
        oftalmologo_id,
        dia_semana,
    )

    horarios_base = [
        IntervaloHorarioRespuesta(
            hora_inicio=horario.hora_inicio,
            hora_fin=horario.hora_fin,
        )
        for horario in horarios
        if horario.hora_inicio < horario.hora_fin
    ]

    if not horarios_base:
        return AgendaRespuesta(
            oftalmologo=_resumir_oftalmologo(oftalmologo),
            fecha=fecha,
            tiene_horario=False,
            horarios_base=[],
            citas=[],
        )

    citas = repo.obtener_citas_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )

    citas_agenda = [
        CitaAgendaRespuesta.model_validate(cita)
        for cita in citas
    ]

    return AgendaRespuesta(
        oftalmologo=_resumir_oftalmologo(oftalmologo),
        fecha=fecha,
        tiene_horario=True,
        horarios_base=horarios_base,
        citas=citas_agenda,
    )


