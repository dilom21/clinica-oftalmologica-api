from typing import Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    get_db,
    obtener_usuario_actual,
    requerir_permiso,
)
from app.modules.gestion_pagos.schemas.administrativos import (
    EstadoPago,
    MetodoManual,
    NotificacionPagoRespuesta,
    PagoAdministrativoCrear,
    PagoAdministrativoRespuesta,
    PagosAdministrativosPagina,
    ResumenServiciosPendientes,
)
from app.modules.gestion_pagos.services import administrativos as service


router = APIRouter(prefix="/administrativos", tags=["CU31 - Pagos administrativos"])
LiteralTarjeta = Literal["TARJETA"]
permiso_lectura = requerir_permiso("Gestionar pagos", ACCION_LECTURA)
permiso_escritura = requerir_permiso("Gestionar pagos", ACCION_ESCRITURA)
permiso_confirmacion = requerir_permiso(
    "Confirmar pagos bancarios", ACCION_ESCRITURA,
)


@router.get("/servicios-pendientes", response_model=ResumenServiciosPendientes)
def servicios_pendientes(
    paciente_id: int = Query(gt=0, le=2**63 - 1),
    consulta_clinica_id: int | None = Query(None, gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    return service.listar_servicios_pendientes(
        db, usuario, paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
    )


@router.post("", response_model=PagoAdministrativoRespuesta, status_code=201)
def registrar_pago(
    request: Request,
    datos: PagoAdministrativoCrear,
    clave_idempotencia: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    ),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
):
    return service.registrar_pago(
        db, datos, clave_idempotencia, usuario,
        request.client.host if request.client else None,
    )


@router.post("/{pago_id}/confirmar", response_model=PagoAdministrativoRespuesta)
def confirmar_pago(
    request: Request,
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_confirmacion),
):
    return service.confirmar_pago(
        db, pago_id, usuario, request.client.host if request.client else None,
    )


@router.get("", response_model=PagosAdministrativosPagina)
def listar_pagos(
    paciente_id: int | None = Query(None, gt=0, le=2**63 - 1),
    metodo_pago: MetodoManual | LiteralTarjeta | None = Query(None),
    estado: EstadoPago | None = Query(None),
    desde: AwareDatetime | None = Query(None),
    hasta: AwareDatetime | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    return service.listar(
        db, usuario, paciente_id=paciente_id, metodo_pago=metodo_pago,
        estado=estado, desde=desde, hasta=hasta, page=page, page_size=page_size,
    )


@router.get("/{pago_id}", response_model=PagoAdministrativoRespuesta)
def consultar_pago(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    return service.consultar(db, pago_id, usuario)


@router.get("/{pago_id}/comprobante")
def descargar_comprobante(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    contenido, nombre = service.generar_comprobante(db, pago_id, usuario)
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


@router.get(
    "/{pago_id}/notificacion",
    response_model=NotificacionPagoRespuesta,
)
def consultar_notificacion_administrativa(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    return service.estado_notificacion_administrativa(db, pago_id, usuario)


@router.post("/{pago_id}/notificacion/reintentar", response_model=NotificacionPagoRespuesta)
def reintentar_notificacion(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
):
    return service.reintentar_notificacion(db, pago_id, usuario)
