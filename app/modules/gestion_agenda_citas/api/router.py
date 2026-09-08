from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    CitaCreate,
    CitaEstadoUpdate,
    CitaResponse,
    CitaUpdate,
    ConfiguracionDisponibilidadRespuesta,
    DisponibilidadRespuesta,
    EstadoDisponibilidadActualizar,
    HorarioOftalmologoActualizar,
    HorarioOftalmologoCrear,
    HorarioOftalmologoRespuesta,
    OftalmologoResumidoRespuesta,
    HistorialCitasRespuesta,
)

from app.modules.gestion_agenda_citas.services import (
    citas,
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
# CU12 - CONSULTAR HISTORIAL DE CITAS
# Función: "Consultar historial de citas"
# Ruta estática /historial: se declara antes de cualquier ruta dinámica.
# =========================================================

NOMBRE_FUNCION_CONSULTAR_HISTORIAL_CITAS = "Consultar historial de citas"

permiso_consultar_historial_citas = requerir_permiso(
    NOMBRE_FUNCION_CONSULTAR_HISTORIAL_CITAS,
    ACCION_LECTURA,
)


@router.get(
    "/historial",
    response_model=HistorialCitasRespuesta,
)
def consultar_historial_citas(
    paciente_id: int | None = Query(default=None),
    nombre: str | None = Query(default=None, min_length=1),
    codigo: str | None = Query(default=None, min_length=1),
    identificacion: str | None = Query(default=None, min_length=1),
    fecha_desde: date | None = Query(default=None),
    fecha_hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None, min_length=1, max_length=20),
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_historial_citas),
):
    if paciente_id is None and not any((nombre, codigo, identificacion)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe indicar paciente_id, nombre, codigo o identificacion",
        )

    return service.consultar_historial_citas(
        db,
        paciente_id=paciente_id,
        nombre=nombre,
        codigo=codigo,
        identificacion=identificacion,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        estado=estado,
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


# =========================================================
# CU10 - GESTIONAR CITAS MÉDICAS
# Función: "Gestionar citas médicas"
# =========================================================

NOMBRE_FUNCION_GESTIONAR_CITAS = "Gestionar citas médicas"

permiso_consultar_citas = requerir_permiso(
    NOMBRE_FUNCION_GESTIONAR_CITAS,
    ACCION_LECTURA,
)

permiso_escribir_citas = requerir_permiso(
    NOMBRE_FUNCION_GESTIONAR_CITAS,
    ACCION_ESCRITURA,
)


@router.post(
    "/citas",
    response_model=CitaResponse,
    status_code=201,
)
def registrar_cita(
    datos: CitaCreate,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_citas),
):
    return citas.registrar_cita(
        db,
        datos,
        usuario,
    )


@router.get(
    "/citas",
    response_model=list[CitaResponse],
)
def consultar_citas(
    fecha: date | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
    estado: str | None = None,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_citas),
):
    return citas.listar_citas(
        db,
        fecha=fecha,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
        estado=estado,
    )


@router.get(
    "/citas/{cita_id}",
    response_model=CitaResponse,
)
def obtener_cita(
    cita_id: int,
    db: Session = Depends(get_db),
    _usuario=Depends(permiso_consultar_citas),
):
    return citas.obtener_cita(
        db,
        cita_id,
    )


@router.put(
    "/citas/{cita_id}",
    response_model=CitaResponse,
)
def reprogramar_cita(
    cita_id: int,
    datos: CitaUpdate,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_citas),
):
    return citas.reprogramar_cita(
        db,
        cita_id,
        datos,
        usuario,
    )


@router.patch(
    "/citas/{cita_id}/estado",
    response_model=CitaResponse,
)
def cambiar_estado_cita(
    cita_id: int,
    datos: CitaEstadoUpdate,
    db: Session = Depends(get_db),
    usuario=Depends(permiso_escribir_citas),
):
    return citas.cambiar_estado_cita(
        db,
        cita_id,
        datos.estado,
        usuario,
    )
