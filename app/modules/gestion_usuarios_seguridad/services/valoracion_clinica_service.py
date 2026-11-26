from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pacientes.repositories import (
    repository as paciente_repo,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.models.valoracion_clinica import (
    ValoracionClinica,
)
from app.modules.gestion_usuarios_seguridad.repositories import (
    repository as seguridad_repo,
)
from app.modules.gestion_usuarios_seguridad.repositories import (
    valoracion_clinica as repo,
)
from app.modules.gestion_usuarios_seguridad.schemas.valoracion_clinica import (
    AtencionValorableRespuesta,
    MOTIVO_ATENCION_COMPLETADA,
    MOTIVO_CONSULTA_FINALIZADA,
    MOTIVO_RECETA_EMITIDA,
    NotificacionValoracionRespuesta,
    OftalmologoValoracionResumen,
    ResumenValoracionesPacienteRespuesta,
    ValoracionClinicaActualizar,
    ValoracionClinicaCrear,
    ValoracionClinicaRespuesta,
    etiqueta_para_puntuacion,
)


BITACORA_REGISTRAR_VALORACION = "REGISTRAR_VALORACION_CLINICA"
BITACORA_ACTUALIZAR_VALORACION = "ACTUALIZAR_VALORACION_CLINICA"
ENTIDAD_VALORACION_CLINICA = "valoracion_clinica"

MENSAJE_VALORACION_REGISTRADA = (
    "¡Gracias! Tu valoración fue registrada correctamente."
)
MENSAJE_VALORACION_ACTUALIZADA = (
    "Tu valoración fue actualizada correctamente."
)


# =========================================================
# HELPERS DE SEGURIDAD, TENANT Y SERIALIZACIÓN
# =========================================================

def _resolver_paciente_autenticado(db: Session, usuario: Usuario):
    """Resuelve el paciente exclusivamente desde el JWT autenticado."""
    rol = getattr(usuario, "rol", None)
    if (
        rol is None
        or getattr(rol, "estado", None) is not True
        or nombre_rol_actual(usuario) != "paciente"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Solo un paciente autenticado puede registrar o actualizar "
                "valoraciones de la clínica"
            ),
        )

    paciente = paciente_repo.obtener_paciente_por_usuario_id(db, usuario.id)
    if paciente is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontró un perfil de paciente asociado a esta cuenta",
        )
    if not paciente.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El perfil de paciente está inactivo",
        )
    return paciente


def _info_empresa_actual(db: Session) -> tuple[str | None, str | None]:
    """Obtiene metadatos públicos de la clínica actual desde el contexto tenant."""
    context = db.info.get("tenant_context")
    if context is None:
        return None, None
    codigo = getattr(context, "empresa_codigo", None)
    nombre = getattr(context, "empresa_nombre", None) or codigo
    return codigo, nombre


def _nombre_oftalmologo(oftalmologo) -> str:
    return f"{oftalmologo.nombres} {oftalmologo.apellidos}".strip()


def _construir_oftalmologo_resumen(oftalmologo) -> OftalmologoValoracionResumen:
    return OftalmologoValoracionResumen(
        id=oftalmologo.id,
        nombres=oftalmologo.nombres,
        apellidos=oftalmologo.apellidos,
        especialidad=oftalmologo.especialidad,
        nombre_completo=_nombre_oftalmologo(oftalmologo),
    )


def _construir_texto_aviso(
    *,
    nombre_medico: str,
    motivo_disparo: str,
    empresa_nombre: str | None = None,
) -> tuple[str, str]:
    sufijo_clinica = f" en {empresa_nombre}" if empresa_nombre else ""
    if motivo_disparo == MOTIVO_RECETA_EMITIDA:
        titulo = "Tu receta está lista — ¡Valora tu atención!"
        mensaje = (
            f"Se emitió la receta de tu revisión con Dr(a). {nombre_medico}"
            f"{sufijo_clinica}. Cuéntanos cómo fue la atención recibida "
            f"calificando del 1 al 5."
        )
    else:
        titulo = "¿Cómo fue tu atención médica?"
        mensaje = (
            f"Finalizó tu atención con Dr(a). {nombre_medico}{sufijo_clinica}. "
            f"Tu opinión nos ayuda a mejorar: califica al médico y la clínica "
            f"desde la aplicación."
        )
    return titulo, mensaje


def _construir_valoracion_respuesta(
    db: Session,
    valoracion: ValoracionClinica,
    *,
    mensaje: str | None = None,
) -> ValoracionClinicaRespuesta:
    empresa_codigo, empresa_nombre = _info_empresa_actual(db)
    consulta = getattr(valoracion, "consulta_clinica", None)
    oftalmologo = getattr(valoracion, "oftalmologo", None)
    return ValoracionClinicaRespuesta(
        id=valoracion.id,
        paciente_id=valoracion.paciente_id,
        consulta_clinica_id=valoracion.consulta_clinica_id,
        oftalmologo_id=valoracion.oftalmologo_id,
        puntuacion=valoracion.puntuacion,
        etiqueta_puntuacion=etiqueta_para_puntuacion(valoracion.puntuacion),
        comentario=valoracion.comentario,
        fecha_creacion=valoracion.fecha_creacion,
        fecha_actualizacion=valoracion.fecha_actualizacion,
        fecha_consulta=consulta.fecha_consulta if consulta is not None else None,
        motivo_consulta=(
            consulta.motivo_consulta if consulta is not None else None
        ),
        oftalmologo=(
            _construir_oftalmologo_resumen(oftalmologo)
            if oftalmologo is not None
            else None
        ),
        empresa_codigo=empresa_codigo,
        empresa_nombre=empresa_nombre,
        mensaje=mensaje,
    )


def _tiene_receta_activa(consulta) -> bool:
    recetas = getattr(consulta, "recetas", None) or []
    return any(getattr(r, "estado", True) is True for r in recetas)


def _construir_notificacion_respuesta(
    db: Session,
    *,
    consulta,
    notificacion_row: dict | None = None,
    atendida: bool = False,
) -> NotificacionValoracionRespuesta:
    empresa_codigo, empresa_nombre = _info_empresa_actual(db)
    tiene_receta = _tiene_receta_activa(consulta)
    oftalmologo = consulta.oftalmologo

    if notificacion_row is not None:
        return NotificacionValoracionRespuesta(
            id=notificacion_row["id"],
            consulta_clinica_id=consulta.id,
            fecha_consulta=consulta.fecha_consulta,
            motivo_consulta=consulta.motivo_consulta,
            tiene_receta=tiene_receta,
            oftalmologo=_construir_oftalmologo_resumen(oftalmologo),
            motivo_disparo=notificacion_row["motivo_disparo"],
            titulo=notificacion_row["titulo"],
            mensaje=notificacion_row["mensaje"],
            leida=notificacion_row["leida"],
            atendida=atendida,
            fecha_creacion=notificacion_row["fecha_creacion"],
            empresa_codigo=empresa_codigo,
            empresa_nombre=empresa_nombre,
        )

    motivo = (
        MOTIVO_RECETA_EMITIDA
        if tiene_receta
        else MOTIVO_ATENCION_COMPLETADA
    )
    titulo, mensaje = _construir_texto_aviso(
        nombre_medico=_nombre_oftalmologo(oftalmologo),
        motivo_disparo=motivo,
        empresa_nombre=empresa_nombre,
    )
    return NotificacionValoracionRespuesta(
        id=None,
        consulta_clinica_id=consulta.id,
        fecha_consulta=consulta.fecha_consulta,
        motivo_consulta=consulta.motivo_consulta,
        tiene_receta=tiene_receta,
        oftalmologo=_construir_oftalmologo_resumen(oftalmologo),
        motivo_disparo=motivo,
        titulo=titulo,
        mensaje=mensaje,
        leida=False,
        atendida=atendida,
        fecha_creacion=consulta.fecha_consulta,
        empresa_codigo=empresa_codigo,
        empresa_nombre=empresa_nombre,
    )


def _es_duplicado_valoracion(exc: IntegrityError) -> bool:
    detalle = str(getattr(exc, "orig", None) or exc).lower().replace('"', "")
    if "uq_valoracion_clinica_consulta" in detalle:
        return True
    return "valoracion_clinica" in detalle and "consulta_clinica_id" in detalle


# =========================================================
# DISPARADOR POST-ATENCIÓN (CU15 CONSULTA / CU17 RECETA)
# Reutiliza `public.notificacion` existente (Opción A).
# =========================================================

def disparar_aviso_valoracion_post_atencion(
    db: Session,
    *,
    consulta_clinica_id: int,
    motivo_disparo: str = MOTIVO_CONSULTA_FINALIZADA,
) -> dict | None:
    """Genera el mensaje en `public.notificacion` al finalizar consulta o recetar.

    Participa de la transacción del llamador (NO ejecuta commit). Si la base
    no tiene la tabla `notificacion` (p. ej. BD legacy o pruebas antiguas),
    retorna `None` sin interrumpir el registro clínico.
    """
    if not repo.tabla_existe(db, "notificacion"):
        return None

    contexto = repo.obtener_contexto_consulta_para_aviso(
        db,
        consulta_clinica_id,
    )
    if contexto is None:
        return None

    consulta, paciente, oftalmologo = contexto
    if paciente.usuario_id is None:
        return None

    # Si la atención ya fue valorada previamente, no se reabre el aviso.
    if (
        repo.tabla_existe(db, "valoracion_clinica")
        and repo.obtener_valoracion_por_consulta(db, consulta.id) is not None
    ):
        return None

    _, empresa_nombre = _info_empresa_actual(db)
    titulo, cuerpo = _construir_texto_aviso(
        nombre_medico=_nombre_oftalmologo(oftalmologo),
        motivo_disparo=motivo_disparo,
        empresa_nombre=empresa_nombre,
    )

    return repo.crear_o_actualizar_notificacion_valoracion(
        db,
        usuario_id=paciente.usuario_id,
        cita_id=consulta.cita_id,
        consulta_clinica_id=consulta.id,
        oftalmologo_id=oftalmologo.id,
        motivo_disparo=motivo_disparo,
        titulo=titulo,
        cuerpo=cuerpo,
    )


# =========================================================
# OPERACIONES DEL PACIENTE MÓVIL (CU40)
# =========================================================

def obtener_resumen_valoraciones(
    db: Session,
    usuario: Usuario,
) -> ResumenValoracionesPacienteRespuesta:
    """Devuelve pendientes por valorar, historial de atenciones y valoraciones."""
    paciente = _resolver_paciente_autenticado(db, usuario)
    empresa_codigo, empresa_nombre = _info_empresa_actual(db)

    consultas = repo.listar_consultas_atendidas_por_paciente(db, paciente.id)
    valoraciones = repo.listar_valoraciones_por_paciente(db, paciente.id)
    valoraciones_por_consulta = {
        val.consulta_clinica_id: val for val in valoraciones
    }
    notificaciones_por_consulta = (
        repo.listar_notificaciones_valoracion_por_usuario(db, usuario.id)
    )

    pendientes: list[NotificacionValoracionRespuesta] = []
    atenciones: list[AtencionValorableRespuesta] = []

    for consulta in consultas:
        if consulta.oftalmologo is None:
            continue
        val = valoraciones_por_consulta.get(consulta.id)
        notif_row = notificaciones_por_consulta.get(consulta.id)
        esta_valorada = val is not None

        notif_resp = (
            _construir_notificacion_respuesta(
                db,
                consulta=consulta,
                notificacion_row=notif_row,
                atendida=esta_valorada,
            )
            if (notif_row is not None or not esta_valorada)
            else None
        )
        val_resp = (
            _construir_valoracion_respuesta(db, val)
            if val is not None
            else None
        )

        if not esta_valorada and notif_resp is not None:
            pendientes.append(notif_resp)

        atenciones.append(
            AtencionValorableRespuesta(
                consulta_clinica_id=consulta.id,
                fecha_consulta=consulta.fecha_consulta,
                motivo_consulta=consulta.motivo_consulta,
                tiene_receta=_tiene_receta_activa(consulta),
                oftalmologo=_construir_oftalmologo_resumen(
                    consulta.oftalmologo
                ),
                notificacion=notif_resp,
                valoracion=val_resp,
                puede_actualizar=esta_valorada,
            )
        )

    return ResumenValoracionesPacienteRespuesta(
        empresa_codigo=empresa_codigo,
        empresa_nombre=empresa_nombre,
        pendientes=pendientes,
        atenciones=atenciones,
        valoraciones=[
            _construir_valoracion_respuesta(db, item)
            for item in valoraciones
        ],
    )


def listar_notificaciones_pendientes(
    db: Session,
    usuario: Usuario,
) -> list[NotificacionValoracionRespuesta]:
    """Lista los avisos de atenciones finalizadas pendientes de valoración."""
    resumen = obtener_resumen_valoraciones(db, usuario)
    return resumen.pendientes


def marcar_notificacion_leida(
    db: Session,
    notificacion_id: int,
    usuario: Usuario,
) -> NotificacionValoracionRespuesta:
    paciente = _resolver_paciente_autenticado(db, usuario)
    notif_row = repo.obtener_notificacion_valoracion_por_id(
        db, notificacion_id, usuario.id,
    )
    if notif_row is None or notif_row["consulta_clinica_id"] is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificación de valoración no encontrada",
        )

    consulta = repo.obtener_consulta_activa_del_paciente(
        db, notif_row["consulta_clinica_id"], paciente.id,
    )
    if consulta is None or consulta.oftalmologo is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificación de valoración no encontrada",
        )

    try:
        repo.marcar_notificacion_leida(db, notificacion_id)
        db.commit()
        actualizada = repo.obtener_notificacion_valoracion_por_id(
            db, notificacion_id, usuario.id,
        )
        atendida = (
            repo.obtener_valoracion_por_consulta(db, consulta.id) is not None
        )
        return _construir_notificacion_respuesta(
            db,
            consulta=consulta,
            notificacion_row=actualizada or notif_row,
            atendida=atendida,
        )
    except Exception:
        db.rollback()
        raise


def listar_mis_valoraciones(
    db: Session,
    usuario: Usuario,
) -> list[ValoracionClinicaRespuesta]:
    paciente = _resolver_paciente_autenticado(db, usuario)
    valoraciones = repo.listar_valoraciones_por_paciente(db, paciente.id)
    return [
        _construir_valoracion_respuesta(db, item) for item in valoraciones
    ]


def consultar_valoracion_por_consulta(
    db: Session,
    consulta_clinica_id: int,
    usuario: Usuario,
) -> ValoracionClinicaRespuesta:
    paciente = _resolver_paciente_autenticado(db, usuario)
    consulta = repo.obtener_consulta_activa_del_paciente(
        db, consulta_clinica_id, paciente.id,
    )
    if consulta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Atención médica no encontrada para el paciente",
        )

    valoracion = repo.obtener_valoracion_por_consulta(db, consulta.id)
    if valoracion is None or valoracion.paciente_id != paciente.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aún no registraste una valoración para esta atención",
        )

    return _construir_valoracion_respuesta(db, valoracion)


def registrar_valoracion(
    db: Session,
    datos: ValoracionClinicaCrear,
    usuario: Usuario,
    ip: str | None = None,
) -> ValoracionClinicaRespuesta:
    """Registra una valoración nueva para una atención finalizada del paciente."""
    paciente = _resolver_paciente_autenticado(db, usuario)

    consulta = repo.obtener_consulta_activa_del_paciente(
        db,
        datos.consulta_clinica_id,
        paciente.id,
    )
    if consulta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No se encontró una atención médica finalizada asociada a tu "
                "cuenta para valorar"
            ),
        )

    existente = repo.obtener_valoracion_por_consulta(db, consulta.id)
    if existente is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Ya registraste una valoración para esta atención. "
                "Puedes actualizarla sin generar un registro duplicado."
            ),
        )

    try:
        valoracion = repo.crear_valoracion(
            db,
            paciente_id=paciente.id,
            consulta_clinica_id=consulta.id,
            oftalmologo_id=consulta.oftalmologo_id,
            puntuacion=datos.puntuacion,
            comentario=datos.comentario,
        )

        # Marca como leída la notificación existente en `public.notificacion`.
        repo.marcar_notificacion_valoracion_leida_por_consulta(
            db, consulta.id, usuario.id,
        )

        seguridad_repo.registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion=BITACORA_REGISTRAR_VALORACION,
            entidad_afectada=ENTIDAD_VALORACION_CLINICA,
            id_registro_afectado=valoracion.id,
            descripcion=(
                f"Valoración de clínica registrada para consulta {consulta.id} "
                f"con puntuación {datos.puntuacion}/5"
            ),
        )
        db.commit()
        cargada = repo.obtener_valoracion_por_id(db, valoracion.id)
        return _construir_valoracion_respuesta(
            db,
            cargada or valoracion,
            mensaje=MENSAJE_VALORACION_REGISTRADA,
        )
    except IntegrityError as exc:
        db.rollback()
        if _es_duplicado_valoracion(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Ya registraste una valoración para esta atención. "
                    "Puedes actualizarla sin generar un registro duplicado."
                ),
            ) from exc
        raise
    except Exception:
        db.rollback()
        raise


def actualizar_valoracion(
    db: Session,
    valoracion_id: int,
    datos: ValoracionClinicaActualizar,
    usuario: Usuario,
    ip: str | None = None,
) -> ValoracionClinicaRespuesta:
    """Actualiza la puntuación y/o comentario de una valoración propia."""
    paciente = _resolver_paciente_autenticado(db, usuario)

    valoracion = repo.obtener_valoracion_por_id(db, valoracion_id)
    if valoracion is None or valoracion.paciente_id != paciente.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Valoración no encontrada",
        )

    try:
        repo.actualizar_valoracion(
            db,
            valoracion,
            puntuacion=datos.puntuacion,
            comentario=datos.comentario,
        )
        seguridad_repo.registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion=BITACORA_ACTUALIZAR_VALORACION,
            entidad_afectada=ENTIDAD_VALORACION_CLINICA,
            id_registro_afectado=valoracion.id,
            descripcion=(
                f"Valoración de clínica actualizada para consulta "
                f"{valoracion.consulta_clinica_id} con puntuación "
                f"{datos.puntuacion}/5"
            ),
        )
        db.commit()
        cargada = repo.obtener_valoracion_por_id(db, valoracion.id)
        return _construir_valoracion_respuesta(
            db,
            cargada or valoracion,
            mensaje=MENSAJE_VALORACION_ACTUALIZADA,
        )
    except Exception:
        db.rollback()
        raise


def guardar_o_actualizar_valoracion_por_consulta(
    db: Session,
    consulta_clinica_id: int,
    datos: ValoracionClinicaActualizar,
    usuario: Usuario,
    ip: str | None = None,
) -> ValoracionClinicaRespuesta:
    """Operación idempotente: crea la valoración si no existe o la actualiza."""
    paciente = _resolver_paciente_autenticado(db, usuario)
    consulta = repo.obtener_consulta_activa_del_paciente(
        db,
        consulta_clinica_id,
        paciente.id,
    )
    if consulta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "No se encontró una atención médica finalizada asociada a tu "
                "cuenta para valorar"
            ),
        )

    existente = repo.obtener_valoracion_por_consulta(db, consulta.id)
    if existente is not None:
        return actualizar_valoracion(db, existente.id, datos, usuario, ip=ip)

    crear_dto = ValoracionClinicaCrear(
        consulta_clinica_id=consulta.id,
        puntuacion=datos.puntuacion,
        comentario=datos.comentario,
    )
    return registrar_valoracion(db, crear_dto, usuario, ip=ip)
