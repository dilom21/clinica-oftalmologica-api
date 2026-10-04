from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_agenda_citas.repositories import repository as agenda_repo
from app.modules.gestion_historial_clinico.repositories import repository as repo
from app.modules.gestion_historial_clinico.schemas.schemas import (
    AntecedenteClinicoActualizar,
    AntecedenteClinicoCrear,
    ConsultaClinicaCrear,
    HistorialClinicoRespuesta,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def consultar_historial_clinico(db: Session, paciente_id: int) -> HistorialClinicoRespuesta:
    paciente, historial = repo.obtener_paciente_con_historial(db, paciente_id)
    if paciente is None:
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    return HistorialClinicoRespuesta(paciente=paciente, historial=historial)


def crear_antecedente(db: Session, datos: AntecedenteClinicoCrear, usuario: Usuario):
    try:
        historial = repo.obtener_historial_activo_por_id(db, datos.historial_clinico_id)
        if historial is None:
            raise HTTPException(
                status_code=404, detail="Historial clínico no encontrado o inactivo",
            )

        antecedente = repo.crear_antecedente(db, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
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
    usuario: Usuario,
):
    try:
        antecedente = repo.obtener_antecedente_disponible_por_id(db, antecedente_id)
        if antecedente is None:
            raise HTTPException(
                status_code=404, detail="Antecedente clínico no encontrado o no disponible",
            )

        antecedente = repo.actualizar_antecedente(db, antecedente, datos)
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
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


# =========================================================
# CU15 - REGISTRAR CONSULTA CLÍNICA
# =========================================================

_ROL_OFTALMOLOGO = "oftalmologo"

BITACORA_REGISTRAR_CONSULTA = "REGISTRAR_CONSULTA_CLINICA"
ENTIDAD_CONSULTA_CLINICA = "consulta_clinica"

# Estados de cita admitidos por CU15 para iniciar una consulta.
# El catálogo global es ESTADOS_CITA_VALIDOS (agenda_citas.schemas); aquí solo
# se decide qué estados permiten abrir una atención: una cita CANCELADA o
# NO_ASISTIO no se atiende, y una cita ATENDIDA ya fue cerrada.
ESTADOS_CITA_INICIABLES_CU15 = (
    "PROGRAMADA",
    "CONFIRMADA",
    "EN_ESPERA",
)

ESTADO_CITA_ATENDIDA = "ATENDIDA"


def _es_violacion_unica_por_cita(exc: IntegrityError) -> bool:
    """Indica si el `IntegrityError` corresponde al UNIQUE de `cita_id`.

    La BD impone `uq_consulta_clinica_cita` sobre `consulta_clinica.cita_id`,
    así que ante una carrera entre dos registros simultáneos el INSERT falla en
    la base de datos y no en la validación previa. El mensaje del driver es la
    única evidencia disponible, y el motor puede ser PostgreSQL o SQLite en
    pruebas:

    - PostgreSQL: `duplicate key value violates unique constraint
      "uq_consulta_clinica_cita"`.
    - SQLite: `UNIQUE constraint failed: consulta_clinica.cita_id`.

    Cualquier otra violación de integridad (otra restricción, otra tabla, otra
    columna) devuelve `False` y el error original se relanza sin traducir: es
    preferible propagar un 500 honesto antes que atribuirle a este CU un
    duplicado que no ocurrió.
    """
    detalle = str(getattr(exc, "orig", None) or exc).lower().replace('"', "")

    # El nombre del índice/constraint es la evidencia más fuerte.
    if "uq_consulta_clinica_cita" in detalle:
        return True

    # SQLite y PostgreSQL cuando el mensaje nombra tabla y columna juntas.
    return "consulta_clinica" in detalle and "cita_id" in detalle


def registrar_consulta_clinica(
    db: Session,
    datos: ConsultaClinicaCrear,
    usuario: Usuario,
):
    """Registra una consulta clínica realizada por el oftalmólogo autenticado.

    El `oftalmologo_id` nunca llega desde el frontend: se resuelve a partir
    del usuario del token (usuario.id -> oftalmologo.usuario_id). Solo el rol
    Oftalmólogo puede registrar; se validan historial, paciente activo y cita
    (si se informa) incluyendo su estado y que no tenga ya una consulta
    asociada.

    Cuando la consulta se apoya en una cita, esa cita queda en ATENDIDA dentro
    de la misma unidad transaccional: consulta + estado de cita + bitácora se
    confirman con un único `db.commit()` o se descartan con un `db.rollback()`.
    """
    if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
        raise HTTPException(
            status_code=403,
            detail="Solo un oftalmólogo puede registrar consultas clínicas",
        )

    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(
        db, usuario.id,
    )
    if oftalmologo is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "El usuario autenticado no tiene un perfil de oftalmólogo "
                "activo"
            ),
        )

    historial = repo.obtener_historial_activo_por_id(
        db, datos.historial_clinico_id,
    )
    if historial is None:
        raise HTTPException(
            status_code=404,
            detail="Historial clínico no encontrado o inactivo",
        )

    if repo.obtener_paciente_activo_por_id(db, historial.paciente_id) is None:
        raise HTTPException(
            status_code=409,
            detail="El paciente asociado al historial clínico está inactivo",
        )

    cita = None
    if datos.cita_id is not None:
        cita = agenda_repo.obtener_cita_por_id(db, datos.cita_id)
        if cita is None:
            raise HTTPException(
                status_code=404, detail="Cita no encontrada",
            )
        if cita.oftalmologo_id != oftalmologo.id:
            raise HTTPException(
                status_code=403,
                detail="La cita no corresponde al oftalmólogo autenticado",
            )
        if cita.paciente_id != historial.paciente_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "La cita no corresponde al paciente del historial "
                    "clínico"
                ),
            )
        if cita.estado not in ESTADOS_CITA_INICIABLES_CU15:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No se puede registrar una consulta sobre una cita en "
                    f"estado {cita.estado}. Estados admitidos: "
                    f"{', '.join(ESTADOS_CITA_INICIABLES_CU15)}"
                ),
            )
        if repo.obtener_consulta_por_cita_id(db, datos.cita_id) is not None:
            raise HTTPException(
                status_code=409,
                detail="La cita ya tiene una consulta clínica registrada",
            )

    try:
        consulta = repo.crear_consulta_clinica(
            db,
            historial_clinico_id=historial.id,
            cita_id=datos.cita_id,
            oftalmologo_id=oftalmologo.id,
            motivo_consulta=datos.motivo_consulta,
            anamnesis=datos.anamnesis,
            observaciones=datos.observaciones,
        )

        if cita is not None:
            agenda_repo.actualizar_estado_cita(
                db, cita, ESTADO_CITA_ATENDIDA,
            )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_CONSULTA,
            entidad_afectada=ENTIDAD_CONSULTA_CLINICA,
            id_registro_afectado=consulta.id,
            descripcion="Consulta clínica registrada",
        )
        db.commit()
        db.refresh(consulta)
        return consulta
    except IntegrityError as exc:
        db.rollback()
        if _es_violacion_unica_por_cita(exc):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Ya existe una consulta clínica registrada para esta cita"
                ),
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


def consultar_consulta_clinica(db: Session, consulta_id: int):
    consulta = repo.obtener_consulta_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404, detail="Consulta clínica no encontrada",
        )
    return consulta


def listar_consultas_clinicas(
    db: Session,
    *,
    historial_clinico_id: int | None = None,
    paciente_id: int | None = None,
    oftalmologo_id: int | None = None,
):
    return repo.listar_consultas_clinicas(
        db,
        historial_clinico_id=historial_clinico_id,
        paciente_id=paciente_id,
        oftalmologo_id=oftalmologo_id,
    )


# =========================================================
# CU16 - REGISTRAR DIAGNÓSTICO
# =========================================================

BITACORA_REGISTRAR_DIAGNOSTICO = "REGISTRAR_DIAGNOSTICO"
ENTIDAD_DIAGNOSTICO = "diagnostico"

_ROL_OFTALMOLOGO = "oftalmologo"


def registrar_diagnostico(
    db: Session,
    consulta_id: int,
    datos,
    usuario: Usuario,
):
    """Registra un diagnóstico para una consulta clínica del oftalmólogo autenticado.

    El oftalmólogo se resuelve desde el usuario autenticado (usuario.id -> oftalmologo.usuario_id).
    Solo el rol Oftalmólogo puede registrar; se valida que la consulta exista, esté activa
    y pertenezca al oftalmólogo autenticado.

    Diagnóstico + bitácora se confirman con un único commit o se descartan con rollback.
    """
    if nombre_rol_actual(usuario) != _ROL_OFTALMOLOGO:
        raise HTTPException(
            status_code=403,
            detail="Solo un oftalmólogo puede registrar diagnósticos",
        )

    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(
        db, usuario.id,
    )
    if oftalmologo is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "El usuario autenticado no tiene un perfil de oftalmólogo "
                "activo"
            ),
        )

    consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404,
            detail="Consulta clínica no encontrada o inactiva",
        )

    if consulta.oftalmologo_id != oftalmologo.id:
        raise HTTPException(
            status_code=403,
            detail="La consulta no pertenece al oftalmólogo autenticado",
        )

    try:
        diagnostico = repo.crear_diagnostico(
            db,
            consulta_clinica_id=consulta.id,
            nombre=datos.nombre,
            descripcion=datos.descripcion,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_DIAGNOSTICO,
            entidad_afectada=ENTIDAD_DIAGNOSTICO,
            id_registro_afectado=diagnostico.id,
            descripcion="Diagnóstico registrado",
        )
        db.commit()
        db.refresh(diagnostico)
        return diagnostico
    except Exception:
        db.rollback()
        raise


def listar_diagnosticos(db: Session, consulta_id: int):
    """Lista los diagnósticos activos de una consulta clínica."""
    consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404,
            detail="Consulta clínica no encontrada o inactiva",
        )
    return repo.listar_diagnosticos_por_consulta(db, consulta.id)


# =========================================================
# CU17 - REGISTRAR TRATAMIENTOS, INDICACIONES Y RECETAS
# =========================================================

BITACORA_REGISTRAR_TRATAMIENTO = "REGISTRAR_TRATAMIENTO"
BITACORA_REGISTRAR_INDICACION = "REGISTRAR_INDICACION"
BITACORA_REGISTRAR_RECETA = "REGISTRAR_RECETA"

ENTIDAD_TRATAMIENTO = "tratamiento"
ENTIDAD_INDICACION = "indicacion"
ENTIDAD_RECETA = "receta"

_MENSAJE_ROL_CU17 = (
    "Solo un oftalmólogo o un administrador puede registrar tratamientos, "
    "indicaciones y recetas"
)
_MENSAJE_SIN_PERFIL_OFTALMOLOGO = (
    "El usuario autenticado no tiene un perfil de oftalmólogo activo"
)
_MENSAJE_CONSULTA_NO_ENCONTRADA = "Consulta clínica no encontrada o inactiva"
_MENSAJE_CONSULTA_AJENA = (
    "La consulta no pertenece al oftalmólogo autenticado"
)

# Roles con acceso total para escrituras clínicas. El permiso específico de
# cada CU se valida primero en el router; el Service conserva la autoridad
# final sobre el actor y la propiedad de la consulta.
_ROLES_CLINICOS_CON_ACCESO_TOTAL = {"administrador", "admin"}


def _resolver_consulta_para_registro_clinico(
    db: Session,
    consulta_id: int,
    usuario: Usuario,
    *,
    mensaje_rol: str,
):
    """Valida rol, propiedad y estado para registrar datos clínicos.

    - Oftalmólogo: se resuelve su perfil activo
      (`usuario.id -> oftalmologo.usuario_id`) y la consulta debe pertenecerle.
    - Administrador: acceso total; puede registrar sobre cualquier consulta
      activa sin necesitar perfil de oftalmólogo.
    - rol distinto de Oftalmólogo/Administrador -> 403
    - oftalmólogo sin perfil activo             -> 403
    - consulta inexistente o inactiva           -> 404
    - consulta de otro oftalmólogo              -> 403
    """
    rol = nombre_rol_actual(usuario)

    if rol in _ROLES_CLINICOS_CON_ACCESO_TOTAL:
        consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
        if consulta is None:
            raise HTTPException(
                status_code=404, detail=_MENSAJE_CONSULTA_NO_ENCONTRADA,
            )
        return consulta

    if rol != _ROL_OFTALMOLOGO:
        raise HTTPException(status_code=403, detail=mensaje_rol)

    oftalmologo = agenda_repo.obtener_oftalmologo_activo_por_usuario_id(
        db, usuario.id,
    )
    if oftalmologo is None:
        raise HTTPException(
            status_code=403, detail=_MENSAJE_SIN_PERFIL_OFTALMOLOGO,
        )

    consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404, detail=_MENSAJE_CONSULTA_NO_ENCONTRADA,
        )

    if consulta.oftalmologo_id != oftalmologo.id:
        raise HTTPException(status_code=403, detail=_MENSAJE_CONSULTA_AJENA)

    return consulta


def _resolver_consulta_propia(
    db: Session,
    consulta_id: int,
    usuario: Usuario,
):
    """Compatibilidad CU17 sobre el validador clínico compartido."""
    return _resolver_consulta_para_registro_clinico(
        db,
        consulta_id,
        usuario,
        mensaje_rol=_MENSAJE_ROL_CU17,
    )


def _obtener_consulta_activa(db: Session, consulta_id: int):
    """Consulta activa para lecturas (validación mínima de existencia)."""
    consulta = repo.obtener_consulta_activa_por_id(db, consulta_id)
    if consulta is None:
        raise HTTPException(
            status_code=404, detail=_MENSAJE_CONSULTA_NO_ENCONTRADA,
        )
    return consulta


def registrar_tratamiento(
    db: Session,
    consulta_id: int,
    datos,
    usuario: Usuario,
):
    """Registra un tratamiento en una consulta propia y activa.

    Tratamiento + bitácora se confirman con un único commit o se descartan
    con rollback, igual que CU16. No se exige diagnóstico previo.
    """
    consulta = _resolver_consulta_propia(db, consulta_id, usuario)

    try:
        tratamiento = repo.crear_tratamiento(
            db,
            consulta_clinica_id=consulta.id,
            descripcion=datos.descripcion,
            observaciones=datos.observaciones,
            fecha_inicio=datos.fecha_inicio,
            fecha_fin=datos.fecha_fin,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_TRATAMIENTO,
            entidad_afectada=ENTIDAD_TRATAMIENTO,
            id_registro_afectado=tratamiento.id,
            descripcion="Tratamiento registrado",
        )
        db.commit()
        db.refresh(tratamiento)
        return tratamiento
    except Exception:
        db.rollback()
        raise


def listar_tratamientos(db: Session, consulta_id: int):
    """Lista los tratamientos activos de una consulta clínica."""
    consulta = _obtener_consulta_activa(db, consulta_id)
    return repo.listar_tratamientos_por_consulta(db, consulta.id)


def registrar_indicacion(
    db: Session,
    consulta_id: int,
    datos,
    usuario: Usuario,
):
    """Registra una indicación clínica en una consulta propia y activa.

    Indicación + bitácora se confirman con un único commit o se descartan
    con rollback.
    """
    consulta = _resolver_consulta_propia(db, consulta_id, usuario)

    try:
        indicacion = repo.crear_indicacion(
            db,
            consulta_clinica_id=consulta.id,
            descripcion=datos.descripcion,
        )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_INDICACION,
            entidad_afectada=ENTIDAD_INDICACION,
            id_registro_afectado=indicacion.id,
            descripcion="Indicación registrada",
        )
        db.commit()
        db.refresh(indicacion)
        return indicacion
    except Exception:
        db.rollback()
        raise


def listar_indicaciones(db: Session, consulta_id: int):
    """Lista las indicaciones de una consulta clínica."""
    consulta = _obtener_consulta_activa(db, consulta_id)
    return repo.listar_indicaciones_por_consulta(db, consulta.id)


def registrar_receta(
    db: Session,
    consulta_id: int,
    datos,
    usuario: Usuario,
):
    """Registra una receta con sus medicamentos en una consulta propia.

    Operación atómica: la receta, sus N detalles y una única entrada de
    bitácora se confirman con un solo commit. Si falla cualquier detalle, el
    rollback deja la base sin receta, sin detalles parciales y sin bitácora.
    No se genera una entrada de bitácora por cada DetalleReceta.
    """
    consulta = _resolver_consulta_propia(db, consulta_id, usuario)

    try:
        receta = repo.crear_receta(
            db,
            consulta_clinica_id=consulta.id,
            observaciones=datos.observaciones,
        )

        for detalle in datos.detalles:
            repo.crear_detalle_receta(
                db,
                receta_id=receta.id,
                medicamento=detalle.medicamento,
                presentacion=detalle.presentacion,
                dosis=detalle.dosis,
                frecuencia=detalle.frecuencia,
                duracion=detalle.duracion,
                indicaciones=detalle.indicaciones,
            )

        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_RECETA,
            entidad_afectada=ENTIDAD_RECETA,
            id_registro_afectado=receta.id,
            descripcion="Receta registrada",
        )
        db.commit()
        # Se relee con selectinload para devolver los detalles ya ordenados.
        return repo.obtener_receta_por_id(db, receta.id)
    except Exception:
        db.rollback()
        raise


def listar_recetas(db: Session, consulta_id: int):
    """Lista las recetas activas de una consulta, con sus detalles."""
    consulta = _obtener_consulta_activa(db, consulta_id)
    return repo.listar_recetas_por_consulta(db, consulta.id)


def consultar_receta(db: Session, receta_id: int):
    """Devuelve una receta con sus detalles (reutilizable desde CU13)."""
    receta = repo.obtener_receta_por_id(db, receta_id)
    if receta is None:
        raise HTTPException(status_code=404, detail="Receta no encontrada")
    return receta


# =========================================================
# CU18 - REGISTRAR RESULTADOS DE EXAMENES OFTALMOLOGICOS
# =========================================================

BITACORA_REGISTRAR_EXAMEN_OFTALMOLOGICO = (
    "REGISTRAR_EXAMEN_OFTALMOLOGICO"
)
BITACORA_REGISTRAR_RESULTADO_EXAMEN = "REGISTRAR_RESULTADO_EXAMEN"

ENTIDAD_EXAMEN_OFTALMOLOGICO = "examen_oftalmologico"
ENTIDAD_RESULTADO_EXAMEN = "resultado_examen"

_MENSAJE_ROL_CU18 = (
    "Solo un oftalmologo o un administrador puede registrar examenes "
    "oftalmologicos y sus resultados"
)
_MENSAJE_EXAMEN_NO_ENCONTRADO = (
    "Examen oftalmologico no encontrado o inactivo"
)


def _resolver_consulta_cu18(
    db: Session,
    consulta_id: int,
    usuario: Usuario,
):
    """Aplica en Service las reglas de actor y propiedad de CU18."""
    return _resolver_consulta_para_registro_clinico(
        db,
        consulta_id,
        usuario,
        mensaje_rol=_MENSAJE_ROL_CU18,
    )


def _obtener_examen_disponible(db: Session, examen_id: int):
    """Examen activo cuya consulta tambien permanece activa."""
    examen = repo.obtener_examen_activo_por_id(db, examen_id)
    if (
        examen is None
        or examen.consulta_clinica is None
        or not examen.consulta_clinica.estado
    ):
        raise HTTPException(
            status_code=404,
            detail=_MENSAJE_EXAMEN_NO_ENCONTRADO,
        )
    return examen


def registrar_examen(
    db: Session,
    consulta_id: int,
    datos,
    usuario: Usuario,
):
    """Registra un examen y su bitacora en una sola transaccion."""
    consulta = _resolver_consulta_cu18(db, consulta_id, usuario)

    try:
        examen = repo.crear_examen(
            db,
            consulta_clinica_id=consulta.id,
            nombre_examen=datos.nombre_examen,
            observaciones=datos.observaciones,
        )
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_EXAMEN_OFTALMOLOGICO,
            entidad_afectada=ENTIDAD_EXAMEN_OFTALMOLOGICO,
            id_registro_afectado=examen.id,
            descripcion="Examen oftalmologico registrado",
        )
        db.commit()
        db.refresh(examen)
        return examen
    except Exception:
        db.rollback()
        raise


def listar_examenes(db: Session, consulta_id: int):
    """Lista examenes activos con sus resultados activos, sin N+1."""
    consulta = _obtener_consulta_activa(db, consulta_id)
    return repo.listar_examenes_por_consulta(db, consulta.id)


def registrar_resultado_examen(
    db: Session,
    examen_id: int,
    datos,
    usuario: Usuario,
):
    """Agrega un resultado; varios resultados por examen son validos."""
    examen = _obtener_examen_disponible(db, examen_id)
    _resolver_consulta_cu18(db, examen.consulta_clinica_id, usuario)

    try:
        resultado = repo.crear_resultado_examen(
            db,
            examen_id=examen.id,
            resultado=datos.resultado,
            archivo_url=datos.archivo_url,
        )
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            accion=BITACORA_REGISTRAR_RESULTADO_EXAMEN,
            entidad_afectada=ENTIDAD_RESULTADO_EXAMEN,
            id_registro_afectado=resultado.id,
            descripcion="Resultado de examen registrado",
        )
        db.commit()
        db.refresh(resultado)
        return resultado
    except Exception:
        db.rollback()
        raise


def listar_resultados_examen(db: Session, examen_id: int):
    """Lista solo resultados activos, del mas reciente al mas antiguo."""
    examen = _obtener_examen_disponible(db, examen_id)
    return repo.listar_resultados_por_examen(db, examen.id)


def consultar_examen(db: Session, examen_id: int):
    """Devuelve un examen activo con sus resultados activos anidados."""
    return _obtener_examen_disponible(db, examen_id)
