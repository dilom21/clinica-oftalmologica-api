from datetime import date, datetime
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_agenda_citas.repositories import repository as repo
from app.modules.gestion_agenda_citas.services.service import (
    listar_oftalmologos_activos,
)
from app.modules.gestion_agenda_citas.schemas.schemas import (
    BloqueoHorarioRespuesta,
    ConfiguracionDisponibilidadRespuesta,
    HorarioOftalmologoRespuesta,
    OftalmologoResumidoRespuesta,
)
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


# =========================================================
# CU11 - CONFIGURAR DISPONIBILIDAD DEL OFTALMÓLOGO
# Reglas por rol y validaciones de horarios/bloqueos.
# =========================================================

_ROL_ADMINISTRADOR = "administrador"
_ROL_OFTALMOLOGO = "oftalmologo"
_ROLES_CON_ACCESO_TOTAL = {_ROL_ADMINISTRADOR, "admin"}

BITACORA_CREAR_HORARIO = "CREAR_HORARIO_OFTALMOLOGO"
BITACORA_ACTUALIZAR_HORARIO = "ACTUALIZAR_HORARIO_OFTALMOLOGO"
BITACORA_CAMBIAR_ESTADO_HORARIO = "CAMBIAR_ESTADO_HORARIO_OFTALMOLOGO"
BITACORA_CREAR_BLOQUEO = "CREAR_BLOQUEO_HORARIO"
BITACORA_ACTUALIZAR_BLOQUEO = "ACTUALIZAR_BLOQUEO_HORARIO"
BITACORA_CAMBIAR_ESTADO_BLOQUEO = "CAMBIAR_ESTADO_BLOQUEO_HORARIO"

ENTIDAD_HORARIO = "horario_oftalmologo"
ENTIDAD_BLOQUEO = "bloqueo_horario"

ESTADOS_CITA_CANCELADA = ("CANCELADA",)


def _ocupa_horario(cita) -> bool:
    return (cita.estado or "").upper() not in ESTADOS_CITA_CANCELADA


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


def _autorizar_configuracion(usuario, oftalmologo) -> None:
    """Reglas contextuales de CU11 por rol.

    - Administrador: configura a cualquier oftalmólogo activo.
    - Oftalmólogo: únicamente su propia disponibilidad
      (oftalmologo.usuario_id == usuario.id).
    - Resto (Recepcionista, Paciente, ...): sin acceso.
    """
    rol = nombre_rol_actual(usuario)

    if rol in _ROLES_CON_ACCESO_TOTAL:
        return

    if rol == _ROL_OFTALMOLOGO:
        if int(oftalmologo.usuario_id) != int(usuario.id):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Un oftalmólogo solo puede configurar su propia "
                    "disponibilidad"
                ),
            )
        return

    raise HTTPException(
        status_code=403,
        detail="No tiene permiso para configurar la disponibilidad",
    )


def _se_solapan(inicio_a, fin_a, inicio_b, fin_b) -> bool:
    return inicio_a < fin_b and inicio_b < fin_a


def _es_cita_futura_activa(cita) -> bool:
    """Cita que ocupa horario y aún no terminó (para validar cobertura)."""
    hoy = date.today()
    if cita.fecha > hoy:
        return True
    if cita.fecha == hoy:
        ahora = datetime.now().time()
        if cita.hora_fin > ahora:
            return True
    return False


def _obtener_horario_de(
    db: Session,
    oftalmologo_id: int,
    horario_id: int,
):
    horario = repo.obtener_horario_por_id(db, horario_id)

    if not horario or horario.oftalmologo_id != oftalmologo_id:
        raise HTTPException(
            status_code=404,
            detail="Horario no encontrado",
        )

    return horario


def _obtener_bloqueo_de(
    db: Session,
    oftalmologo_id: int,
    bloqueo_id: int,
):
    bloqueo = repo.obtener_bloqueo_por_id(db, bloqueo_id)

    if not bloqueo or bloqueo.oftalmologo_id != oftalmologo_id:
        raise HTTPException(
            status_code=404,
            detail="Bloqueo no encontrado",
        )

    return bloqueo


def _validar_duplicado_solapamiento_horario(
    db: Session,
    oftalmologo_id: int,
    dia_semana: int,
    hora_inicio,
    hora_fin,
    excluir_id: int | None = None,
) -> None:
    """Rechaza horarios activos duplicados o solapados del mismo día."""
    existentes = repo.obtener_horarios_por_oftalmologo_y_dia(
        db,
        oftalmologo_id,
        dia_semana,
    )

    for horario in existentes:
        if excluir_id is not None and horario.id == excluir_id:
            continue

        if (
            horario.hora_inicio == hora_inicio
            and horario.hora_fin == hora_fin
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ya existe un horario activo con el mismo intervalo "
                    "para ese día"
                ),
            )

        if _se_solapan(
            hora_inicio,
            hora_fin,
            horario.hora_inicio,
            horario.hora_fin,
        ):
            raise HTTPException(
                status_code=409,
                detail="El horario se solapa con otro horario activo del día",
            )


def _validar_cobertura_citas_futuras(
    db: Session,
    oftalmologo_id: int,
    horarios_resultantes,
) -> None:
    """Impide dejar citas futuras activas fuera de todo horario vigente.

    Las citas CANCELADA no ocupan horario (misma regla que CU09).
    No se cancelan ni reprograman citas: eso corresponde a CU10.
    """
    citas = repo.obtener_citas_futuras_por_oftalmologo(
        db,
        oftalmologo_id,
        desde=date.today(),
    )
    citas_futuras = [
        cita for cita in citas
        if _ocupa_horario(cita) and _es_cita_futura_activa(cita)
    ]

    if not citas_futuras:
        return

    activos_por_dia: dict[int, list] = {}
    for horario in horarios_resultantes:
        if not getattr(horario, "estado", True):
            continue
        activos_por_dia.setdefault(horario.dia_semana, []).append(horario)

    for cita in citas_futuras:
        dia_semana = cita.fecha.isoweekday()
        cubierta = any(
            horario.hora_inicio <= cita.hora_inicio
            and cita.hora_fin <= horario.hora_fin
            for horario in activos_por_dia.get(dia_semana, [])
        )

        if not cubierta:
            raise HTTPException(
                status_code=409,
                detail=(
                    "La operación dejaría citas futuras activas fuera de "
                    "todo horario; cancele o reprograme las citas primero "
                    "(CU10)"
                ),
            )


def _validar_fecha_no_pasada(fecha: date) -> None:
    if fecha < date.today():
        raise HTTPException(
            status_code=400,
            detail="La fecha del bloqueo no puede ser pasada",
        )


def _validar_bloqueo_contra_agenda(
    db: Session,
    oftalmologo_id: int,
    fecha: date,
    hora_inicio,
    hora_fin,
    excluir_id: int | None = None,
) -> None:
    """Valida un bloqueo contra horario activo, otros bloqueos y citas."""
    dia_semana = fecha.isoweekday()
    horarios = repo.obtener_horarios_por_oftalmologo_y_dia(
        db,
        oftalmologo_id,
        dia_semana,
    )

    if not any(
        _se_solapan(hora_inicio, hora_fin, h.hora_inicio, h.hora_fin)
        for h in horarios
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "El bloqueo debe intersecar al menos un horario activo "
                "de la fecha"
            ),
        )

    bloqueos = repo.obtener_bloqueos_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )

    for bloqueo in bloqueos:
        if excluir_id is not None and bloqueo.id == excluir_id:
            continue

        if (
            bloqueo.hora_inicio == hora_inicio
            and bloqueo.hora_fin == hora_fin
        ):
            raise HTTPException(
                status_code=409,
                detail="Ya existe un bloqueo activo con el mismo horario",
            )

        if _se_solapan(
            hora_inicio,
            hora_fin,
            bloqueo.hora_inicio,
            bloqueo.hora_fin,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "El bloqueo se solapa con otro bloqueo activo "
                    "del mismo día"
                ),
            )

    citas = repo.obtener_citas_por_oftalmologo_y_fecha(
        db,
        oftalmologo_id,
        fecha,
    )

    for cita in citas:
        if not _ocupa_horario(cita):
            continue

        if _se_solapan(
            hora_inicio,
            hora_fin,
            cita.hora_inicio,
            cita.hora_fin,
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "El bloqueo se solapa con una cita existente; "
                    "debe cancelarse o reprogramarse la cita primero (CU10)"
                ),
            )


def listar_oftalmologos_configuracion(db: Session, usuario):
    """Oftalmólogos configurables por el usuario (propio para Oftalmólogo)."""
    return listar_oftalmologos_activos(db, usuario=usuario)


def consultar_configuracion(
    db: Session,
    oftalmologo_id: int,
    usuario,
) -> ConfiguracionDisponibilidadRespuesta:
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    horarios = repo.obtener_horarios_por_oftalmologo(db, oftalmologo_id)
    bloqueos = repo.obtener_bloqueos_por_oftalmologo(db, oftalmologo_id)

    return ConfiguracionDisponibilidadRespuesta(
        oftalmologo=OftalmologoResumidoRespuesta.model_validate(oftalmologo),
        horarios=[
            HorarioOftalmologoRespuesta.model_validate(h)
            for h in horarios
        ],
        bloqueos=[
            BloqueoHorarioRespuesta.model_validate(b)
            for b in bloqueos
        ],
    )


def crear_horario(
    db: Session,
    oftalmologo_id: int,
    datos,
    usuario,
):
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    _validar_duplicado_solapamiento_horario(
        db,
        oftalmologo_id,
        datos.dia_semana,
        datos.hora_inicio,
        datos.hora_fin,
    )

    try:
        horario = repo.crear_horario_oftalmologo(
            db,
            oftalmologo_id=oftalmologo_id,
            dia_semana=datos.dia_semana,
            hora_inicio=datos.hora_inicio,
            hora_fin=datos.hora_fin,
            estado=True,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_CREAR_HORARIO,
            entidad_afectada=ENTIDAD_HORARIO,
            id_registro_afectado=horario.id,
            descripcion="Horario semanal creado para el oftalmólogo",
        )

        db.commit()
        db.refresh(horario)

        return horario

    except Exception:
        db.rollback()
        raise


def actualizar_horario(
    db: Session,
    oftalmologo_id: int,
    horario_id: int,
    datos,
    usuario,
):
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    horario = _obtener_horario_de(db, oftalmologo_id, horario_id)

    if (
        horario.dia_semana == datos.dia_semana
        and horario.hora_inicio == datos.hora_inicio
        and horario.hora_fin == datos.hora_fin
    ):
        return horario

    if horario.estado:
        _validar_duplicado_solapamiento_horario(
            db,
            oftalmologo_id,
            datos.dia_semana,
            datos.hora_inicio,
            datos.hora_fin,
            excluir_id=horario.id,
        )

        reemplazo = SimpleNamespace(
            id=horario.id,
            dia_semana=datos.dia_semana,
            hora_inicio=datos.hora_inicio,
            hora_fin=datos.hora_fin,
            estado=True,
        )
        activos = repo.obtener_horarios_activos_por_oftalmologo(
            db,
            oftalmologo_id,
        )
        resultantes = [
            h for h in activos if h.id != horario.id
        ] + [reemplazo]

        _validar_cobertura_citas_futuras(
            db,
            oftalmologo_id,
            resultantes,
        )

    try:
        horario = repo.actualizar_horario_oftalmologo(
            db,
            horario,
            dia_semana=datos.dia_semana,
            hora_inicio=datos.hora_inicio,
            hora_fin=datos.hora_fin,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_ACTUALIZAR_HORARIO,
            entidad_afectada=ENTIDAD_HORARIO,
            id_registro_afectado=horario.id,
            descripcion="Horario semanal del oftalmólogo actualizado",
        )

        db.commit()
        db.refresh(horario)

        return horario

    except Exception:
        db.rollback()
        raise


def cambiar_estado_horario(
    db: Session,
    oftalmologo_id: int,
    horario_id: int,
    estado: bool,
    usuario,
):
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    horario = _obtener_horario_de(db, oftalmologo_id, horario_id)

    if horario.estado == estado:
        return horario

    if estado:
        _validar_duplicado_solapamiento_horario(
            db,
            oftalmologo_id,
            horario.dia_semana,
            horario.hora_inicio,
            horario.hora_fin,
            excluir_id=horario.id,
        )
    else:
        activos = repo.obtener_horarios_activos_por_oftalmologo(
            db,
            oftalmologo_id,
        )
        resultantes = [
            h for h in activos if h.id != horario.id
        ]
        _validar_cobertura_citas_futuras(
            db,
            oftalmologo_id,
            resultantes,
        )

    try:
        horario = repo.cambiar_estado_horario_oftalmologo(
            db,
            horario,
            estado,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_CAMBIAR_ESTADO_HORARIO,
            entidad_afectada=ENTIDAD_HORARIO,
            id_registro_afectado=horario.id,
            descripcion=(
                "Horario semanal del oftalmólogo activado"
                if estado
                else "Horario semanal del oftalmólogo desactivado"
            ),
        )

        db.commit()
        db.refresh(horario)

        return horario

    except Exception:
        db.rollback()
        raise


def crear_bloqueo(
    db: Session,
    oftalmologo_id: int,
    datos,
    usuario,
):
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    _validar_fecha_no_pasada(datos.fecha)
    _validar_bloqueo_contra_agenda(
        db,
        oftalmologo_id,
        datos.fecha,
        datos.hora_inicio,
        datos.hora_fin,
    )

    try:
        bloqueo = repo.crear_bloqueo_horario(
            db,
            oftalmologo_id=oftalmologo_id,
            fecha=datos.fecha,
            hora_inicio=datos.hora_inicio,
            hora_fin=datos.hora_fin,
            motivo=datos.motivo,
            estado=True,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_CREAR_BLOQUEO,
            entidad_afectada=ENTIDAD_BLOQUEO,
            id_registro_afectado=bloqueo.id,
            descripcion="Bloqueo horario creado",
        )

        db.commit()
        db.refresh(bloqueo)

        return bloqueo

    except Exception:
        db.rollback()
        raise


def actualizar_bloqueo(
    db: Session,
    oftalmologo_id: int,
    bloqueo_id: int,
    datos,
    usuario,
):
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    bloqueo = _obtener_bloqueo_de(db, oftalmologo_id, bloqueo_id)

    if (
        bloqueo.fecha == datos.fecha
        and bloqueo.hora_inicio == datos.hora_inicio
        and bloqueo.hora_fin == datos.hora_fin
        and bloqueo.motivo == datos.motivo
    ):
        return bloqueo

    if datos.fecha != bloqueo.fecha:
        _validar_fecha_no_pasada(datos.fecha)

    if bloqueo.estado:
        _validar_bloqueo_contra_agenda(
            db,
            oftalmologo_id,
            datos.fecha,
            datos.hora_inicio,
            datos.hora_fin,
            excluir_id=bloqueo.id,
        )

    try:
        bloqueo = repo.actualizar_bloqueo_horario(
            db,
            bloqueo,
            fecha=datos.fecha,
            hora_inicio=datos.hora_inicio,
            hora_fin=datos.hora_fin,
            motivo=datos.motivo,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_ACTUALIZAR_BLOQUEO,
            entidad_afectada=ENTIDAD_BLOQUEO,
            id_registro_afectado=bloqueo.id,
            descripcion="Bloqueo horario actualizado",
        )

        db.commit()
        db.refresh(bloqueo)

        return bloqueo

    except Exception:
        db.rollback()
        raise


def cambiar_estado_bloqueo(
    db: Session,
    oftalmologo_id: int,
    bloqueo_id: int,
    estado: bool,
    usuario,
):
    oftalmologo = _obtener_oftalmologo_activo(db, oftalmologo_id)
    _autorizar_configuracion(usuario, oftalmologo)

    bloqueo = _obtener_bloqueo_de(db, oftalmologo_id, bloqueo_id)

    if bloqueo.estado == estado:
        return bloqueo

    if estado:
        _validar_fecha_no_pasada(bloqueo.fecha)
        _validar_bloqueo_contra_agenda(
            db,
            oftalmologo_id,
            bloqueo.fecha,
            bloqueo.hora_inicio,
            bloqueo.hora_fin,
            excluir_id=bloqueo.id,
        )

    try:
        bloqueo = repo.cambiar_estado_bloqueo_horario(
            db,
            bloqueo,
            estado,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_CAMBIAR_ESTADO_BLOQUEO,
            entidad_afectada=ENTIDAD_BLOQUEO,
            id_registro_afectado=bloqueo.id,
            descripcion=(
                "Bloqueo horario activado"
                if estado
                else "Bloqueo horario desactivado"
            ),
        )

        db.commit()
        db.refresh(bloqueo)

        return bloqueo

    except Exception:
        db.rollback()
        raise
