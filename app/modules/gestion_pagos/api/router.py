from fastapi import APIRouter, Depends, Header, Path, Request, Response
from sqlalchemy.orm import Session

from app.core.dependencies import obtener_usuario_actual
from app.database.session import get_db
from app.modules.gestion_pagos.schemas.schemas import (
    ConsultaPagoResumen,
    EstadoPagoStripeRespuesta,
    IntencionPagoStripeRespuesta,
    PagoHistorialItem,
    SeleccionServiciosPago,
    ServicioPagoRespuesta,
    WebhookStripeRespuesta,
)
from app.modules.gestion_pagos.services import service
from app.modules.gestion_pagos.providers import (
    ProveedorPagoBase,
    obtener_proveedor_stripe,
)


router = APIRouter(prefix="/pagos", tags=["Gestión de Pagos"])


@router.get("/")
def obtener_modulo():
    return {"mensaje": "Módulo de pagos funcionando"}


@router.get("/mis-consultas", response_model=list[ConsultaPagoResumen])
def listar_mis_consultas_con_pagos(
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    return service.listar_mis_consultas_con_pagos(db, usuario)


@router.get(
    "/mis-consultas/{consulta_id}/servicios",
    response_model=list[ServicioPagoRespuesta],
)
def listar_mis_servicios_de_consulta(
    consulta_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    return service.listar_mis_servicios_de_consulta(
        db,
        consulta_id,
        usuario,
    )


@router.post(
    "/stripe/intencion",
    response_model=IntencionPagoStripeRespuesta,
    status_code=201,
)
def crear_intencion_stripe(
    datos: SeleccionServiciosPago,
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    return service.crear_intencion_stripe(
        db,
        datos.servicio_realizado_ids,
        usuario,
        proveedor,
    )


@router.post(
    "/stripe/webhook",
    response_model=WebhookStripeRespuesta,
)
async def webhook_stripe(
    request: Request,
    stripe_signature: str | None = Header(
        default=None,
        alias="Stripe-Signature",
    ),
    db: Session = Depends(get_db),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    payload = await request.body()
    return service.procesar_webhook_stripe(
        db,
        payload,
        stripe_signature,
        proveedor,
    )


@router.get(
    "/stripe/pagos/{pago_id}/estado",
    response_model=EstadoPagoStripeRespuesta,
)
def consultar_estado_pago_stripe(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    return service.consultar_estado_pago_stripe(db, pago_id, usuario)


@router.get("/mis-pagos", response_model=list[PagoHistorialItem])
def listar_mis_pagos(
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    return service.listar_mis_pagos(db, usuario)


@router.get("/mis-pagos/{pago_id}/comprobante")
def descargar_comprobante_pago(
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    contenido, nombre_archivo = service.generar_comprobante_pago(
        db,
        pago_id,
        usuario,
    )
    return Response(
        content=contenido,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre_archivo}"',
        },
    )
