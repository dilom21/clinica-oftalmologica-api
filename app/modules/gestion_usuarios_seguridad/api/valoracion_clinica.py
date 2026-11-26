from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, obtener_usuario_actual
from app.modules.gestion_usuarios_seguridad.schemas.valoracion_clinica import (
    NotificacionValoracionRespuesta,
    ResumenValoracionesPacienteRespuesta,
    ValoracionClinicaActualizar,
    ValoracionClinicaCrear,
    ValoracionClinicaRespuesta,
)
from app.modules.gestion_usuarios_seguridad.services import (
    valoracion_clinica_service,
)


router = APIRouter(
    prefix="/valoraciones",
    tags=["CU40 - Valoración de la Clínica (Móvil)"],
)


@router.get(
    "/resumen",
    response_model=ResumenValoracionesPacienteRespuesta,
)
def obtener_resumen_valoraciones(
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Resumen completo para el paciente autenticado en su clínica (tenant)."""
    return valoracion_clinica_service.obtener_resumen_valoraciones(db, usuario)


@router.get(
    "/pendientes",
    response_model=list[NotificacionValoracionRespuesta],
)
def listar_notificaciones_valoracion_pendientes(
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Mensajes/avisos post-atención para valorar al médico y la clínica."""
    return valoracion_clinica_service.listar_notificaciones_pendientes(
        db, usuario,
    )


@router.patch(
    "/notificaciones/{notificacion_id}/leer",
    response_model=NotificacionValoracionRespuesta,
)
def marcar_notificacion_valoracion_leida(
    notificacion_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Marca un aviso móvil de valoración como leído por el paciente."""
    return valoracion_clinica_service.marcar_notificacion_leida(
        db, notificacion_id, usuario,
    )


@router.get(
    "/mis-valoraciones",
    response_model=list[ValoracionClinicaRespuesta],
)
def listar_mis_valoraciones(
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Lista las valoraciones ya registradas por el paciente autenticado."""
    return valoracion_clinica_service.listar_mis_valoraciones(db, usuario)


@router.get(
    "/consulta/{consulta_clinica_id}",
    response_model=ValoracionClinicaRespuesta,
)
def consultar_mi_valoracion_por_consulta(
    consulta_clinica_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Consulta la valoración existente de una atención médica específica."""
    return valoracion_clinica_service.consultar_valoracion_por_consulta(
        db, consulta_clinica_id, usuario,
    )


@router.post(
    "",
    response_model=ValoracionClinicaRespuesta,
    status_code=201,
)
def registrar_valoracion_clinica(
    request: Request,
    datos: ValoracionClinicaCrear,
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Registra una nueva valoración de la clínica para una atención."""
    return valoracion_clinica_service.registrar_valoracion(
        db,
        datos,
        usuario,
        ip=request.client.host if request.client else None,
    )


@router.put(
    "/consulta/{consulta_clinica_id}",
    response_model=ValoracionClinicaRespuesta,
)
def guardar_o_actualizar_valoracion_por_consulta(
    request: Request,
    datos: ValoracionClinicaActualizar,
    consulta_clinica_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Registra o actualiza sin duplicar la valoración de una atención."""
    return valoracion_clinica_service.guardar_o_actualizar_valoracion_por_consulta(
        db,
        consulta_clinica_id,
        datos,
        usuario,
        ip=request.client.host if request.client else None,
    )


@router.put(
    "/{valoracion_id}",
    response_model=ValoracionClinicaRespuesta,
)
def actualizar_valoracion_clinica(
    request: Request,
    datos: ValoracionClinicaActualizar,
    valoracion_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    """Actualiza una valoración existente del paciente sin generar duplicados."""
    return valoracion_clinica_service.actualizar_valoracion(
        db,
        valoracion_id,
        datos,
        usuario,
        ip=request.client.host if request.client else None,
    )
