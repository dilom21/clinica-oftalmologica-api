from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.tenancy.dependencies import get_control_db
from app.modules.administracion_saas import policy_service, restore_service
from app.modules.administracion_saas.backup_service import create_manual_backup, list_backups
from app.modules.administracion_saas.dependencies import get_saas_admin, get_saas_superadmin
from app.modules.administracion_saas.models import BackupTenant, RestoreTenant, SaasUsuario
from app.modules.administracion_saas.repository import SaasRepository
from app.modules.administracion_saas.schemas import (
    BitacoraResponse,
    BackupCreateRequest,
    BackupPolicyResponse,
    BackupPolicyUpdateRequest,
    BackupResponse,
    EmpresaResponse,
    EstadoRequest,
    PlanResponse,
    ProvisionamientoResponse,
    RestoreCreateRequest,
    RestoreResponse,
    RestoreValidateRequest,
    RestoreValidateResponse,
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


@router.post("/backups", response_model=BackupResponse, status_code=201)
def crear_backup(
    request: Request,
    data: BackupCreateRequest,
    db: Session = Depends(get_control_db),
    usuario: SaasUsuario = Depends(get_saas_admin),
):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        return create_manual_backup(
            factory,
            data.empresa_id,
            usuario.id,
            request.client.host if request.client else None,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Manual backup service is unavailable") from None


@router.get("/backups", response_model=list[BackupResponse])
def backups(
    empresa_id: int | None = Query(None, gt=0),
    estado: str | None = Query(
        None, pattern="^(PENDIENTE|EN_PROCESO|COMPLETADO|ERROR)$",
    ),
    tipo: str | None = Query(
        None, pattern="^(MANUAL|AUTOMATICO|PRE_RESTORE)$",
    ),
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return list_backups(db, empresa_id, estado, tipo)


@router.get("/backups/{backup_id}", response_model=BackupResponse)
def backup_detail(
    backup_id: int,
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    if backup_id <= 0:
        raise HTTPException(404, "Backup not found")
    record = db.get(BackupTenant, backup_id)
    if record is None:
        raise HTTPException(404, "Backup not found")
    return record


@router.get("/backup-policies", response_model=list[BackupPolicyResponse])
def backup_policies(
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return policy_service.list_backup_policies(db)


@router.put("/backup-policies/{empresa_id}", response_model=BackupPolicyResponse)
def actualizar_backup_policy(
    request: Request,
    empresa_id: int,
    data: BackupPolicyUpdateRequest,
    db: Session = Depends(get_control_db),
    usuario: SaasUsuario = Depends(get_saas_superadmin),
):
    if empresa_id <= 0:
        raise HTTPException(404, "Empresa no encontrada")
    return policy_service.upsert_backup_policy(
        db,
        empresa_id,
        data,
        usuario.id,
        request.client.host if request.client else None,
    )


def _restore_response(row: RestoreTenant) -> RestoreResponse:
    return RestoreResponse.model_validate(row).model_copy(update={
        "mensaje_error": sanitize_error(row.mensaje_error),
        "rollback_mensaje": sanitize_error(row.rollback_mensaje),
    })


@router.post("/restores/validate", response_model=RestoreValidateResponse)
def validar_restore(
    request: Request,
    data: RestoreValidateRequest,
    db: Session = Depends(get_control_db),
    usuario: SaasUsuario = Depends(get_saas_admin),
):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        dependencies = restore_service.build_restore_dependencies()
    except Exception:
        raise HTTPException(503, "Restore service is unavailable") from None
    try:
        return restore_service.validate_restore(
            factory,
            data.backup_id,
            usuario.id,
            request.client.host if request.client else None,
            backend=dependencies.backend,
            storage=dependencies.storage,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Restore validation is unavailable") from None


@router.post("/restores", response_model=RestoreResponse, status_code=201)
def crear_restore(
    request: Request,
    data: RestoreCreateRequest,
    db: Session = Depends(get_control_db),
    usuario: SaasUsuario = Depends(get_saas_superadmin),
):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        dependencies = restore_service.build_restore_dependencies()
    except Exception:
        raise HTTPException(503, "Restore service is unavailable") from None
    try:
        row = restore_service.create_restore(
            factory,
            data.backup_id,
            data.confirmacion,
            usuario.id,
            request.client.host if request.client else None,
            backend=dependencies.backend,
            backup_runner=dependencies.backup_runner,
            storage=dependencies.storage,
            engine_disposer=restore_service.get_engine_disposer(),
        )
        return _restore_response(row)
    except HTTPException:
        raise
    except restore_service.RestoreReservationError:
        raise HTTPException(
            409, "A restore is already in progress for this tenant",
        ) from None
    except restore_service.RestoreExecutionError as exc:
        raise HTTPException(
            503, sanitize_error(str(exc)) or "Restore failed",
        ) from None
    except Exception:
        raise HTTPException(503, "Restore service is unavailable") from None


@router.get("/restores", response_model=list[RestoreResponse])
def restores(
    empresa_id: int | None = Query(None, gt=0),
    estado: str | None = Query(
        None,
        pattern=(
            "^(PENDIENTE|EN_PROCESO|COMPLETADO|ERROR|ROLLBACK_EN_PROCESO|"
            "ROLLBACK_COMPLETADO|ROLLBACK_ERROR)$"
        ),
    ),
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    return [
        _restore_response(row)
        for row in restore_service.list_restores(db, empresa_id, estado)
    ]


@router.get("/restores/{restore_id}", response_model=RestoreResponse)
def restore_detail(
    restore_id: int,
    db: Session = Depends(get_control_db),
    _usuario: SaasUsuario = Depends(get_saas_admin),
):
    if restore_id <= 0:
        raise HTTPException(404, "Restore not found")
    row = db.get(RestoreTenant, restore_id)
    if row is None:
        raise HTTPException(404, "Restore not found")
    return _restore_response(row)
