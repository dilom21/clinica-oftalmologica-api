from typing import Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA,
    ACCION_LECTURA,
    get_db,
    obtener_usuario_actual,
    requerir_permiso,
)
from app.modules.gestion_pagos.providers import ProveedorPagoBase, obtener_proveedor_stripe
from app.modules.gestion_pagos.schemas.planes_cuotas import (
    CancelarPlanPago,
    IntencionCuotaStripeRespuesta,
    IntentosCuotaRespuesta,
    PagoCuotaCrear,
    PagoCuotaRespuesta,
    PlanPagoCrear,
    PlanPagoRespuesta,
    PlanesPagoPagina,
    RechazarPagoCuota,
    ServiciosDisponiblesPlanRespuesta,
)
from app.modules.gestion_pagos.services import planes_cuotas as service


router = APIRouter(tags=["CU32/CU33 - Planes y cuotas"])
planes_lectura = requerir_permiso("Gestionar planes de pago", ACCION_LECTURA)
planes_escritura = requerir_permiso("Gestionar planes de pago", ACCION_ESCRITURA)
cuotas_lectura = requerir_permiso("Gestionar cuotas", ACCION_LECTURA)
cuotas_escritura = requerir_permiso("Gestionar cuotas", ACCION_ESCRITURA)


@router.get(
    "/planes/servicios-disponibles",
    response_model=ServiciosDisponiblesPlanRespuesta,
)
def servicios_disponibles(
    paciente_id: int = Query(gt=0, le=2**63 - 1),
    consulta_clinica_id: int = Query(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(planes_lectura),
):
    return service.listar_servicios_disponibles(
        db, usuario, paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
    )


@router.post("/planes", response_model=PlanPagoRespuesta, status_code=201)
def crear_plan(
    request: Request,
    datos: PlanPagoCrear,
    clave_idempotencia: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    ),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(planes_escritura),
):
    return service.crear_plan(
        db, datos, clave_idempotencia, usuario,
        request.client.host if request.client else None,
    )


@router.get("/planes", response_model=PlanesPagoPagina)
def listar_planes(
    paciente_id: int | None = Query(None, gt=0, le=2**63 - 1),
    estado: Literal["ACTIVO", "COMPLETADO", "CANCELADO"] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(planes_lectura),
):
    return service.listar_planes(
        db, usuario, paciente_id=paciente_id, estado=estado,
        page=page, page_size=page_size,
    )


@router.get("/planes/{plan_id}", response_model=PlanPagoRespuesta)
def consultar_plan(
    plan_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(planes_lectura),
):
    return service.consultar_plan(db, plan_id, usuario)


@router.post("/planes/{plan_id}/cancelar", response_model=PlanPagoRespuesta)
def cancelar_plan(
    request: Request,
    datos: CancelarPlanPago,
    plan_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(planes_escritura),
):
    return service.cancelar_plan(
        db, plan_id, datos, usuario,
        request.client.host if request.client else None,
    )


@router.get("/mis-planes", response_model=PlanesPagoPagina)
def listar_mis_planes(
    estado: Literal["ACTIVO", "COMPLETADO", "CANCELADO"] | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    return service.listar_mis_planes(
        db, usuario, estado=estado, page=page, page_size=page_size,
    )


@router.get("/mis-planes/{plan_id}", response_model=PlanPagoRespuesta)
def consultar_mi_plan(
    plan_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    return service.consultar_mi_plan(db, plan_id, usuario)


@router.post(
    "/cuotas/{cuota_id}/stripe/intencion",
    response_model=IntencionCuotaStripeRespuesta,
    status_code=201,
)
def crear_intencion_stripe(
    cuota_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    proveedor: ProveedorPagoBase = Depends(obtener_proveedor_stripe),
):
    return service.crear_intencion_stripe(db, cuota_id, usuario, proveedor)


@router.post(
    "/cuotas/{cuota_id}/pagos",
    response_model=PagoCuotaRespuesta,
    status_code=201,
)
def registrar_pago_cuota(
    request: Request,
    datos: PagoCuotaCrear,
    cuota_id: int = Path(gt=0, le=2**63 - 1),
    clave_idempotencia: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=100,
        pattern=r"^[A-Za-z0-9._:/-]+$",
    ),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(cuotas_escritura),
):
    return service.registrar_pago_manual(
        db, cuota_id, datos, clave_idempotencia, usuario,
        request.client.host if request.client else None,
    )


@router.post(
    "/cuotas/{cuota_id}/pagos/{pago_id}/confirmar",
    response_model=PagoCuotaRespuesta,
)
def confirmar_pago_cuota(
    request: Request,
    cuota_id: int = Path(gt=0, le=2**63 - 1),
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(cuotas_escritura),
):
    return service.confirmar_pago_manual(
        db, cuota_id, pago_id, usuario,
        request.client.host if request.client else None,
    )


@router.post(
    "/cuotas/{cuota_id}/pagos/{pago_id}/rechazar",
    response_model=PagoCuotaRespuesta,
)
def rechazar_pago_cuota(
    request: Request,
    datos: RechazarPagoCuota,
    cuota_id: int = Path(gt=0, le=2**63 - 1),
    pago_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
    _permiso=Depends(cuotas_escritura),
):
    return service.rechazar_pago_manual(
        db, cuota_id, pago_id, datos, usuario,
        request.client.host if request.client else None,
    )


@router.get(
    "/cuotas/{cuota_id}/intentos",
    response_model=IntentosCuotaRespuesta,
)
def listar_intentos(
    cuota_id: int = Path(gt=0, le=2**63 - 1),
    db: Session = Depends(get_db),
    usuario=Depends(obtener_usuario_actual),
):
    # La autoridad final permanece en el servicio: pacientes ven solo lo
    # propio; Administrador/Recepcionista requieren el permiso de lectura.
    if getattr(getattr(usuario, "rol", None), "nombre", "").strip().lower() != "paciente":
        cuotas_lectura(usuario=usuario, db=db)
    return service.listar_intentos_cuota(db, cuota_id, usuario)

