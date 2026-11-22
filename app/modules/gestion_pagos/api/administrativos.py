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
from app.modules.gestion_pagos.schemas.ajustes import (
    AnularPago,
    AtribuirReembolsoHuerfano,
    ConciliarAjuste,
    CorregirPago,
    HistorialAjustesRespuesta,
    PagoAjusteRespuesta,
    ReembolsarPago,
    WebhookHuerfanoRespuesta,
)
from app.modules.gestion_pagos.providers import (
    ProveedorPagoBase,
    obtener_proveedor_stripe,
)
from app.modules.gestion_pagos.services import ajustes as ajustes_service
from app.modules.gestion_pagos.services import administrativos as service
from app.modules.gestion_pagos.services import huerfanos as huerfanos_service


router = APIRouter(prefix="/administrativos", tags=["CU31 - Pagos administrativos"])
LiteralTarjeta = Literal["TARJETA"]
MODULO_PAGOS = "Pagos"
permiso_lectura = requerir_permiso(
    "Gestionar pagos", ACCION_LECTURA, modulo=MODULO_PAGOS,
)
permiso_escritura = requerir_permiso(
    "Gestionar pagos", ACCION_ESCRITURA, modulo=MODULO_PAGOS,
)
permiso_confirmacion = requerir_permiso(
    "Confirmar pagos bancarios", ACCION_ESCRITURA, modulo=MODULO_PAGOS,
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


@router.get("/ajustes/{ajuste_id}", response_model=PagoAjusteRespuesta)
def consultar_ajuste(
    ajuste_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    return ajustes_service.consultar_ajuste(db, ajuste_id, usuario)


@router.post(
    "/ajustes/{ajuste_id}/conciliar", response_model=PagoAjusteRespuesta,
)
def conciliar_ajuste(
    request: Request,
    datos: ConciliarAjuste,
    ajuste_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    return ajustes_service.conciliar_ajuste(
        db, ajuste_id, datos, usuario,
        request.client.host if request.client else None, proveedor,
    )


@router.get(
    "/reembolsos-huerfanos", response_model=list[WebhookHuerfanoRespuesta],
)
def listar_reembolsos_huerfanos(
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
):
    """E-3: refunds verificados que esperan conciliacion manual."""
    return huerfanos_service.listar_huerfanos(db, usuario)


@router.post(
    "/reembolsos-huerfanos/{huerfano_id}/atribuir",
    response_model=PagoAjusteRespuesta,
)
def atribuir_reembolso_huerfano(
    request: Request,
    datos: AtribuirReembolsoHuerfano,
    huerfano_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    """E-3: concilia un refund huerfano con refund_id explicito y evidencia."""
    return huerfanos_service.atribuir_reembolso_huerfano(
        db, huerfano_id, datos, usuario,
        request.client.host if request.client else None, proveedor,
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


@router.get("/{pago_id}/ajustes", response_model=HistorialAjustesRespuesta)
def historial_ajustes_pago(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_lectura),
):
    return ajustes_service.historial_ajustes(db, pago_id, usuario)


@router.post("/{pago_id}/anular", response_model=PagoAjusteRespuesta)
def anular_pago(
    request: Request,
    datos: AnularPago,
    pago_id: int = Path(gt=0, le=2**63 - 1),
    clave_idempotencia: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    ),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    return ajustes_service.anular_pago(
        db, pago_id, datos, clave_idempotencia, usuario,
        request.client.host if request.client else None, proveedor,
    )


@router.post("/{pago_id}/corregir", response_model=PagoAjusteRespuesta)
def corregir_pago(
    request: Request,
    datos: CorregirPago,
    pago_id: int = Path(gt=0, le=2**63 - 1),
    clave_idempotencia: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    ),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
):
    return ajustes_service.corregir_pago(
        db, pago_id, datos, clave_idempotencia, usuario,
        request.client.host if request.client else None,
    )


@router.post("/{pago_id}/reembolsos", response_model=PagoAjusteRespuesta)
def reembolsar_pago(
    request: Request,
    datos: ReembolsarPago,
    pago_id: int = Path(gt=0, le=2**63 - 1),
    clave_idempotencia: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    ),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(permiso_escritura),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    return ajustes_service.reembolsar_pago(
        db, pago_id, datos, clave_idempotencia, usuario,
        request.client.host if request.client else None, proveedor,
    )
