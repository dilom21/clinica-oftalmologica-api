from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    requerir_permiso,
)
from app.database.session import get_db

from app.modules.gestion_agenda_citas.schemas.schemas import (
    AgendaRespuesta,
    BloqueoHorarioActualizar,
    BloqueoHorarioCrear,
    BloqueoHorarioRespuesta,
    ConfiguracionDisponibilidadRespuesta,
    DisponibilidadRespuesta,
    EstadoDisponibilidadActualizar,
    HorarioOftalmologoActualizar,
    HorarioOftalmologoCrear,
    HorarioOftalmologoRespuesta,
    OftalmologoResumidoRespuesta,
)

from app.modules.gestion_agenda_citas.services import (
    configuracion,
    service,
)


router = APIRouter(
    prefix="/agenda-citas",
    tags=["Agenda y Citas"]
)


permiso_consultar_agenda = requerir_permiso(
    "Consultar agenda y disponibilidad médica",
    ACCION_LECTURA,
)


@router.get("/")
def obtener_agenda():
    return {"mensaje": "Módulo de agenda y citas funcionando"}


# =========================================================
# CU09 - CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA
# =========================================================

@router.get(
    "/oftalmologos",
    response_model=list[OftalmologoResumidoRespuesta],
)
def listar_oftalmologos(
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_agenda),
):
    return service.listar_oftalmologos_activos(db, usuario=_usuario)


@router.get(
    "/disponibilidad",
    response_model=DisponibilidadRespuesta,
)
def obtener_disponibilidad(
    oftalmologo_id: int,
    fecha: date,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_agenda),
):
    return service.consultar_disponibilidad(
        db,
        oftalmologo_id,
        fecha,
    )


@router.get(
    "/oftalmologos/{oftalmologo_id}/agenda",
    response_model=AgendaRespuesta,
)
def obtener_agenda_por_oftalmologo(
    oftalmologo_id: int,
    fecha: date,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_agenda),
):
    return service.consultar_agenda(
        db,
        oftalmologo_id,
        fecha,
        usuario=_usuario,
    )


# =========================================================
# CU11 - CONFIGURAR DISPONIBILIDAD DEL OFTALMÓLOGO
# Función: "Configurar disponibilidad del oftalmólogo"
# =========================================================

NOMBRE_FUNCION_CONFIGURAR_DISPONIBILIDAD = (
    "Configurar disponibilidad del oftalmólogo"
)

permiso_consultar_configuracion = requerir_permiso(
    NOMBRE_FUNCION_CONFIGURAR_DISPONIBILIDAD,
    ACCION_LECTURA,
)

permiso_escribir_configuracion = requerir_permiso(
    NOMBRE_FUNCION_CONFIGURAR_DISPONIBILIDAD,
    ACCION_ESCRITURA,
)


@router.get(
    "/configuracion/oftalmologos",
    response_model=list[OftalmologoResumidoRespuesta],
)
def listar_oftalmologos_configuracion(
    db: Session = Depends(get_db),
    usuario=Depends(permiso_consultar_configuracion),
):
    return configuracion.listar_oftalmologos_configuracion(db, usuario)


@router.get(
    "/configuracion/oftalmologos/{oftalmologo_id}",
    response_model=ConfiguracionDisponibilidadRespuesta,
)
def obtener_configuracion_oftalmologo(
    oftalmologo_id: int,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_consultar_configuracion),
):
    return configuracion.consultar_configuracion(
        db,
        oftalmologo_id,
        usuario,
    )


@router.post(
    "/configuracion/oftalmologos/{oftalmologo_id}/horarios",
    response_model=HorarioOftalmologoRespuesta,
    status_code=201,
)
def crear_horario_oftalmologo(
    oftalmologo_id: int,
    datos: HorarioOftalmologoCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_configuracion),
):
    return configuracion.crear_horario(
        db,
        oftalmologo_id,
        datos,
        usuario,
    )


@router.put(
    "/configuracion/oftalmologos/{oftalmologo_id}/horarios/{horario_id}",
    response_model=HorarioOftalmologoRespuesta,
)
def actualizar_horario_oftalmologo(
    oftalmologo_id: int,
    horario_id: int,
    datos: HorarioOftalmologoActualizar,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_configuracion),
):
    return configuracion.actualizar_horario(
        db,
        oftalmologo_id,
        horario_id,
        datos,
        usuario,
    )


@router.patch(
    "/configuracion/oftalmologos/{oftalmologo_id}/horarios/{horario_id}/estado",
    response_model=HorarioOftalmologoRespuesta,
)
def cambiar_estado_horario_oftalmologo(
    oftalmologo_id: int,
    horario_id: int,
    datos: EstadoDisponibilidadActualizar,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_configuracion),
):
    return configuracion.cambiar_estado_horario(
        db,
        oftalmologo_id,
        horario_id,
        datos.estado,
        usuario,
    )


@router.post(
    "/configuracion/oftalmologos/{oftalmologo_id}/bloqueos",
    response_model=BloqueoHorarioRespuesta,
    status_code=201,
)
def crear_bloqueo_horario(
    oftalmologo_id: int,
    datos: BloqueoHorarioCrear,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_configuracion),
):
    return configuracion.crear_bloqueo(
        db,
        oftalmologo_id,
        datos,
        usuario,
    )


@router.put(
    "/configuracion/oftalmologos/{oftalmologo_id}/bloqueos/{bloqueo_id}",
    response_model=BloqueoHorarioRespuesta,
)
def actualizar_bloqueo_horario(
    oftalmologo_id: int,
    bloqueo_id: int,
    datos: BloqueoHorarioActualizar,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_configuracion),
):
    return configuracion.actualizar_bloqueo(
        db,
        oftalmologo_id,
        bloqueo_id,
        datos,
        usuario,
    )


@router.patch(
    "/configuracion/oftalmologos/{oftalmologo_id}/bloqueos/{bloqueo_id}/estado",
    response_model=BloqueoHorarioRespuesta,
)
def cambiar_estado_bloqueo_horario(
    oftalmologo_id: int,
    bloqueo_id: int,
    datos: EstadoDisponibilidadActualizar,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_configuracion),
):
    return configuracion.cambiar_estado_bloqueo(
        db,
        oftalmologo_id,
        bloqueo_id,
        datos.estado,
        usuario,
    )
