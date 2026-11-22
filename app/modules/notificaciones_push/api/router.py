"""Router HTTP de notificaciones push para la aplicacion movil de pacientes.

Todas las rutas exigen un JWT **tenant** valido y el rol ``Paciente``. El
usuario y el tenant se derivan exclusivamente de las dependencias del backend.
No existen endpoints de creacion/envio de notificaciones ni de administracion.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.modules.notificaciones_push.api.dependencies import (
    ContextoPaciente,
    get_control_plane_db,
    obtener_contexto_paciente,
)
from app.modules.notificaciones_push.schemas.http import (
    BandejaRespuesta,
    DispositivoRespuesta,
    NotificacionLeidaRespuesta,
    PreferenciasActualizacionRequest,
    PreferenciasRespuesta,
    RegistroDispositivoRequest,
)
from app.modules.notificaciones_push.services import (
    bandeja as servicio_bandeja,
    dispositivos as servicio_dispositivos,
    preferencias as servicio_preferencias,
)

router = APIRouter(
    prefix="/notificaciones",
    tags=["Notificaciones push"],
)


@router.post(
    "/dispositivos",
    response_model=DispositivoRespuesta,
    status_code=status.HTTP_200_OK,
    summary="Registrar o actualizar el dispositivo push del paciente",
    description=(
        "Registra o actualiza idempotentemente un dispositivo FCM del paciente "
        "autenticado. El token se trata como secreto: nunca se devuelve. El "
        "usuario, el tenant y la huella del token se calculan en el backend."
    ),
)
def registrar_dispositivo(
    datos: RegistroDispositivoRequest,
    contexto: ContextoPaciente = Depends(obtener_contexto_paciente),
    control: Session = Depends(get_control_plane_db),
) -> DispositivoRespuesta:
    dispositivo = servicio_dispositivos.registrar_dispositivo(
        contexto.db,
        control,
        usuario_id=contexto.usuario.id,
        tenant_database_id=contexto.tenant_database_id,
        datos=datos,
    )
    return DispositivoRespuesta(
        id=dispositivo.id,
        plataforma=dispositivo.plataforma,
        activo=bool(dispositivo.activo),
        fecha_registro=dispositivo.fecha_registro,
        fecha_actualizacion=dispositivo.fecha_actualizacion,
    )


@router.delete(
    "/dispositivos/{dispositivo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revocar un dispositivo propio",
    description=(
        "Revoca logicamente un dispositivo del paciente autenticado y libera "
        "su huella global solo si tenant, usuario y huella coinciden. Es "
        "idempotente y no revela la existencia de dispositivos ajenos."
    ),
)
def revocar_dispositivo(
    dispositivo_id: int,
    contexto: ContextoPaciente = Depends(obtener_contexto_paciente),
    control: Session = Depends(get_control_plane_db),
) -> Response:
    servicio_dispositivos.revocar_dispositivo(
        contexto.db,
        control,
        usuario_id=contexto.usuario.id,
        tenant_database_id=contexto.tenant_database_id,
        dispositivo_id=dispositivo_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "",
    response_model=BandejaRespuesta,
    summary="Consultar mi bandeja de notificaciones",
    description=(
        "Devuelve solo las notificaciones del paciente autenticado, con orden "
        "estable (fecha e ID descendentes) y paginacion acotada. El campo "
        "``datos`` se filtra por una allowlist de navegacion no sensible."
    ),
)
def listar_notificaciones(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=20,
        ge=1,
        le=servicio_bandeja.LIMITE_MAXIMO_PAGINA,
    ),
    contexto: ContextoPaciente = Depends(obtener_contexto_paciente),
) -> BandejaRespuesta:
    return servicio_bandeja.listar_bandeja(
        contexto.db,
        usuario_id=contexto.usuario.id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/{notificacion_id}/leida",
    response_model=NotificacionLeidaRespuesta,
    summary="Marcar una notificacion propia como leida",
    description=(
        "Marca como leida una notificacion del paciente autenticado. Es "
        "idempotente y no permite modificar ningun otro campo."
    ),
)
def marcar_notificacion_leida(
    notificacion_id: int,
    contexto: ContextoPaciente = Depends(obtener_contexto_paciente),
) -> NotificacionLeidaRespuesta:
    return servicio_bandeja.marcar_notificacion_leida(
        contexto.db,
        usuario_id=contexto.usuario.id,
        notificacion_id=notificacion_id,
    )


@router.get(
    "/preferencias",
    response_model=PreferenciasRespuesta,
    summary="Consultar mis preferencias de notificacion",
    description=(
        "Devuelve las preferencias del paciente autenticado. Si aun no existe "
        "fila, devuelve los valores por defecto sin escribir en la base."
    ),
)
def obtener_preferencias(
    contexto: ContextoPaciente = Depends(obtener_contexto_paciente),
) -> PreferenciasRespuesta:
    return servicio_preferencias.obtener_preferencias(
        contexto.db,
        usuario_id=contexto.usuario.id,
    )


@router.put(
    "/preferencias",
    response_model=PreferenciasRespuesta,
    summary="Actualizar mis preferencias de notificacion",
    description=(
        "Actualiza solo los campos de preferencia enviados, con booleanos "
        "estrictos. Crea la fila solo al guardar. Desactivar el push no borra "
        "ni oculta la bandeja historica."
    ),
)
def actualizar_preferencias(
    cambios: PreferenciasActualizacionRequest,
    contexto: ContextoPaciente = Depends(obtener_contexto_paciente),
) -> PreferenciasRespuesta:
    return servicio_preferencias.actualizar_preferencias(
        contexto.db,
        usuario_id=contexto.usuario.id,
        cambios=cambios,
    )
