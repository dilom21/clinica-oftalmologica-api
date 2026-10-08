from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from app.core.tenancy.dependencies import get_control_db
from ..dependencies import get_saas_admin
from ..models import BackupTenant, RestoreTenant, SaasUsuario
from .. import policy_service, restore_service
from ..backup_service import create_manual_backup, list_backups
from ..repository import SaasRepository
from ..schemas import (BackupCreateRequest, BackupPolicyResponse, BackupPolicyUpdateRequest,
                       BackupResponse, BitacoraResponse, EmpresaResponse, EstadoRequest, PlanResponse,
                       ProvisionamientoResponse, RestoreCreateRequest, RestoreResponse, RestoreValidateRequest,
                       RestoreValidateResponse, SaasLoginRequest, SaasLoginResponse,
                       SuscripcionResponse, TenantResponse)
from ..service import autenticar, cambiar_estado_empresa, cambiar_estado_suscripcion, sanitize_error

router = APIRouter(prefix="/saas", tags=["Administración SaaS"])


@router.post("/auth/login", response_model=SaasLoginResponse)
def login(request: Request, data: SaasLoginRequest, db: Session = Depends(get_control_db)):
    return autenticar(db, data.correo, data.password, request.client.host if request.client else None)


@router.get("/empresas", response_model=list[EmpresaResponse])
def empresas(db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return [{"id": e.id, "codigo": e.codigo, "slug": e.slug,
             "nombre": e.nombre_comercial or e.razon_social, "estado": e.estado,
             "plan": plan, "estado_suscripcion": sub, "database_name": getattr(t, "database_name", None),
             "estado_tenant": getattr(t, "estado", None), "version_schema": getattr(t, "version_schema", None),
             "fecha_provisionamiento": getattr(t, "fecha_provisionamiento", None)}
            for e, plan, sub, t in SaasRepository(db).empresas()]


@router.get("/empresas/{empresa_id}", response_model=EmpresaResponse)
def empresa(empresa_id: int, db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    rows = [x for x in SaasRepository(db).empresas() if x[0].id == empresa_id]
    if not rows:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    e, plan, sub, t = rows[0]
    return {"id": e.id, "codigo": e.codigo, "slug": e.slug, "nombre": e.nombre_comercial or e.razon_social,
            "estado": e.estado, "plan": plan, "estado_suscripcion": sub,
            "database_name": getattr(t, "database_name", None), "estado_tenant": getattr(t, "estado", None),
            "version_schema": getattr(t, "version_schema", None), "fecha_provisionamiento": getattr(t, "fecha_provisionamiento", None)}


@router.get("/planes", response_model=list[PlanResponse])
def planes(db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return SaasRepository(db).planes()


@router.get("/suscripciones", response_model=list[SuscripcionResponse])
def suscripciones(db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return SaasRepository(db).suscripciones()


@router.get("/tenants", response_model=list[TenantResponse])
def tenants(db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return [{"empresa": t.empresa_id, "database_name": t.database_name, "estado": t.estado,
             "version_schema": t.version_schema, "fecha_provisionamiento": t.fecha_provisionamiento,
             "ultima_verificacion": t.ultima_verificacion} for t in SaasRepository(db).tenants()]


@router.get("/provisionamientos", response_model=list[ProvisionamientoResponse])
def provisionamientos(db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return [{"id": p.id, "empresa_id": p.empresa_id, "tenant_database_id": p.tenant_database_id,
             "estado": p.estado, "paso_actual": p.paso_actual, "intentos": p.intentos,
             "fecha_inicio": p.fecha_inicio, "fecha_fin": p.fecha_fin,
             "mensaje_error": sanitize_error(p.mensaje_error)} for p in SaasRepository(db).provisionamientos()]


@router.get("/bitacora", response_model=list[BitacoraResponse])
def bitacora(db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return [BitacoraResponse.model_validate(row).model_copy(
        update={"descripcion": sanitize_error(row.descripcion)}
    ) for row in SaasRepository(db).bitacora()]


@router.patch("/empresas/{empresa_id}/estado", response_model=EmpresaResponse)
def estado_empresa(request: Request, empresa_id: int, data: EstadoRequest,
                   db: Session = Depends(get_control_db), user: SaasUsuario = Depends(get_saas_admin)):
    cambiar_estado_empresa(db, empresa_id, data.estado, user.id, request.client.host if request.client else None)
    return empresa(empresa_id, db, user)


@router.patch("/suscripciones/{suscripcion_id}/estado", response_model=SuscripcionResponse)
def estado_suscripcion(request: Request, suscripcion_id: int, data: EstadoRequest,
                       db: Session = Depends(get_control_db), user: SaasUsuario = Depends(get_saas_admin)):
    return cambiar_estado_suscripcion(db, suscripcion_id, data.estado, user.id,
                                       request.client.host if request.client else None)


@router.post("/backups", response_model=BackupResponse, status_code=201)
def crear_backup(request: Request, data: BackupCreateRequest,
                 db: Session = Depends(get_control_db), user: SaasUsuario = Depends(get_saas_admin)):
    user_id = user.id
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()  # Do not hold the authentication read transaction during pg_dump.
    try:
        return create_manual_backup(factory, data.empresa_id, user_id,
                                    request.client.host if request.client else None)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Manual backup service is unavailable") from None


@router.get("/backups", response_model=list[BackupResponse])
def backups(empresa_id: int | None = Query(None, gt=0),
            estado: str | None = Query(None, pattern="^(PENDIENTE|EN_PROCESO|COMPLETADO|ERROR)$"),
            tipo: str | None = Query(None, pattern="^(MANUAL|AUTOMATICO|PRE_RESTORE)$"),
            db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return list_backups(db, empresa_id, estado, tipo)


@router.get("/backups/{backup_id}", response_model=BackupResponse)
def backup_detail(backup_id: int, db: Session = Depends(get_control_db),
                  _: SaasUsuario = Depends(get_saas_admin)):
    if backup_id <= 0:
        raise HTTPException(404, "Backup not found")
    record = db.get(BackupTenant, backup_id)
    if record is None:
        raise HTTPException(404, "Backup not found")
    return record


@router.get("/backup-policies", response_model=list[BackupPolicyResponse])
def backup_policies(db: Session = Depends(get_control_db),
                    _: SaasUsuario = Depends(get_saas_admin)):
    return policy_service.list_backup_policies(db)


@router.put("/backup-policies/{empresa_id}", response_model=BackupPolicyResponse)
def actualizar_backup_policy(request: Request, empresa_id: int, data: BackupPolicyUpdateRequest,
                             db: Session = Depends(get_control_db),
                             user: SaasUsuario = Depends(get_saas_admin)):
    if empresa_id <= 0:
        raise HTTPException(404, "Empresa no encontrada")
    return policy_service.upsert_backup_policy(
        db, empresa_id, data, user.id,
        request.client.host if request.client else None)


def _restore_response(row: RestoreTenant) -> RestoreResponse:
    return RestoreResponse.model_validate(row).model_copy(update={
        "mensaje_error": sanitize_error(row.mensaje_error),
        "rollback_mensaje": sanitize_error(row.rollback_mensaje),
    })


@router.post("/restores/validate", response_model=RestoreValidateResponse)
def validar_restore(request: Request, data: RestoreValidateRequest,
                    db: Session = Depends(get_control_db), user: SaasUsuario = Depends(get_saas_admin)):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()  # Do not hold the authentication read transaction during validation.
    try:
        dependencies = restore_service.build_restore_dependencies()
    except Exception:
        raise HTTPException(503, "Restore service is unavailable") from None
    try:
        return restore_service.validate_restore(
            factory, data.backup_id, user.id,
            request.client.host if request.client else None,
            backend=dependencies.backend, storage=dependencies.storage)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Restore validation is unavailable") from None


@router.post("/restores", response_model=RestoreResponse, status_code=201)
def crear_restore(request: Request, data: RestoreCreateRequest,
                  db: Session = Depends(get_control_db), user: SaasUsuario = Depends(get_saas_admin)):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    db.rollback()
    try:
        dependencies = restore_service.build_restore_dependencies()
    except Exception:
        raise HTTPException(503, "Restore service is unavailable") from None
    try:
        row = restore_service.create_restore(
            factory, data.backup_id, data.confirmacion, user.id,
            request.client.host if request.client else None,
            backend=dependencies.backend, backup_runner=dependencies.backup_runner,
            storage=dependencies.storage, engine_disposer=restore_service.get_engine_disposer())
        return _restore_response(row)
    except HTTPException:
        raise
    except restore_service.RestoreReservationError:
        raise HTTPException(409, "A restore is already in progress for this tenant") from None
    except restore_service.RestoreExecutionError as exc:
        raise HTTPException(503, sanitize_error(str(exc)) or "Restore failed") from None
    except Exception:
        raise HTTPException(503, "Restore service is unavailable") from None


@router.get("/restores", response_model=list[RestoreResponse])
def restores(empresa_id: int | None = Query(None, gt=0),
             estado: str | None = Query(
                 None,
                 pattern="^(PENDIENTE|EN_PROCESO|COMPLETADO|ERROR|ROLLBACK_EN_PROCESO|ROLLBACK_COMPLETADO|ROLLBACK_ERROR)$"),
             db: Session = Depends(get_control_db), _: SaasUsuario = Depends(get_saas_admin)):
    return [_restore_response(row) for row in restore_service.list_restores(db, empresa_id, estado)]


@router.get("/restores/{restore_id}", response_model=RestoreResponse)
def restore_detail(restore_id: int, db: Session = Depends(get_control_db),
                   _: SaasUsuario = Depends(get_saas_admin)):
    if restore_id <= 0:
        raise HTTPException(404, "Restore not found")
    row = db.get(RestoreTenant, restore_id)
    if row is None:
        raise HTTPException(404, "Restore not found")
    return _restore_response(row)
