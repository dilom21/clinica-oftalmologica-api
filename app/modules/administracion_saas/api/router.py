from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.tenancy.dependencies import get_control_db
from app.modules.administracion_saas.dependencies import get_saas_admin
from app.modules.administracion_saas.models import SaasUsuario
from app.modules.administracion_saas.repository import SaasRepository
from app.modules.administracion_saas.schemas import (
    BitacoraResponse,
    EmpresaResponse,
    EstadoRequest,
    PlanResponse,
    ProvisionamientoResponse,
    SaasLoginRequest,
    SaasLoginResponse,
    SuscripcionResponse,
    TenantResponse,
)
from app.modules.administracion_saas.service import (
    autenticar,
    cambiar_estado_empresa,
    cambiar_estado_suscripcion,
    sanitize_error,
)


router = APIRouter(prefix="/saas", tags=["Administracion SaaS"])


def _empresa_response(row) -> dict:
    empresa, plan, estado_suscripcion, tenant = row
    return {
        "id": empresa.id,
        "codigo": empresa.codigo,
        "slug": empresa.slug,
        "nombre": empresa.nombre_comercial or empresa.razon_social,
        "estado": empresa.estado,
        "plan": plan,
        "estado_suscripcion": estado_suscripcion,
        "database_name": getattr(tenant, "database_name", None),
        "estado_tenant": getattr(tenant, "estado", None),
        "version_schema": getattr(tenant, "version_schema", None),
        "fecha_provisionamiento": getattr(tenant, "fecha_provisionamiento", None),
    }


@router.post("/auth/login", response_model=SaasLoginResponse)
def login(
    request: Request,
    data: SaasLoginRequest,
    db: Session = Depends(get_control_db),
):
    ip = request.client.host if request.client else None
    return autenticar(db, data.correo, data.password, ip)


@router.get("/empresas", response_model=list[EmpresaResponse])
def empresas(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return [_empresa_response(row) for row in SaasRepository(db).empresas()]


@router.get("/empresas/{empresa_id}", response_model=EmpresaResponse)
def empresa(
    empresa_id: int,
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    row = next(
        (item for item in SaasRepository(db).empresas() if item[0].id == empresa_id),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return _empresa_response(row)


@router.get("/planes", response_model=list[PlanResponse])
def planes(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return SaasRepository(db).planes()


@router.get("/suscripciones", response_model=list[SuscripcionResponse])
def suscripciones(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return SaasRepository(db).suscripciones()


@router.get("/tenants", response_model=list[TenantResponse])
def tenants(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return [
        {
            "empresa": tenant.empresa_id,
            "database_name": tenant.database_name,
            "estado": tenant.estado,
            "version_schema": tenant.version_schema,
            "fecha_provisionamiento": tenant.fecha_provisionamiento,
            "ultima_verificacion": tenant.ultima_verificacion,
        }
        for tenant in SaasRepository(db).tenants()
    ]


@router.get("/provisionamientos", response_model=list[ProvisionamientoResponse])
def provisionamientos(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return [
        {
            "id": item.id,
            "empresa_id": item.empresa_id,
            "tenant_database_id": item.tenant_database_id,
            "estado": item.estado,
            "paso_actual": item.paso_actual,
            "intentos": item.intentos,
            "fecha_inicio": item.fecha_inicio,
            "fecha_fin": item.fecha_fin,
            "mensaje_error": sanitize_error(item.mensaje_error),
        }
        for item in SaasRepository(db).provisionamientos()
    ]


@router.get("/bitacora", response_model=list[BitacoraResponse])
def bitacora(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return [
        BitacoraResponse.model_validate(row).model_copy(
            update={"descripcion": sanitize_error(row.descripcion)},
        )
        for row in SaasRepository(db).bitacora()
    ]


@router.patch("/empresas/{empresa_id}/estado", response_model=EmpresaResponse)
def estado_empresa(
    request: Request,
    empresa_id: int,
    data: EstadoRequest,
    db: Session = Depends(get_control_db),
    usuario: SaasUsuario = Depends(get_saas_admin),
):
    ip = request.client.host if request.client else None
    cambiar_estado_empresa(db, empresa_id, data.estado, usuario.id, ip)
    return empresa(empresa_id, db, usuario)


@router.patch(
    "/suscripciones/{suscripcion_id}/estado",
    response_model=SuscripcionResponse,
)
def estado_suscripcion(
    request: Request,
    suscripcion_id: int,
    data: EstadoRequest,
    db: Session = Depends(get_control_db),
    usuario: SaasUsuario = Depends(get_saas_admin),
):
    ip = request.client.host if request.client else None
    return cambiar_estado_suscripcion(
        db, suscripcion_id, data.estado, usuario.id, ip,
    )
