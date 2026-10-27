from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA, ACCION_LECTURA, get_db, get_tenant_claims, requerir_permiso,
)
from app.modules.gestion_inventario_proveedores.schemas.schemas import (
    ProveedorActualizar, ProveedorCrear, ProveedorOperacion,
    ProveedorRespuesta, ProveedoresPagina,
)
from app.modules.gestion_inventario_proveedores.services import service
from app.modules.gestion_usuarios_seguridad.models.models import Usuario


def _exigir_bearer(request: Request):
    esquema, _, token = request.headers.get("Authorization", "").partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise HTTPException(
            401, "Se requiere una sesión válida",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix="/proveedores",
    tags=["Gestionar proveedores"],
    dependencies=[Depends(_exigir_bearer), Depends(get_tenant_claims)],
)
permiso_lectura = requerir_permiso("Gestionar proveedores", ACCION_LECTURA)
permiso_escritura = requerir_permiso("Gestionar proveedores", ACCION_ESCRITURA)
IdProveedor = Annotated[int, Path(gt=0, le=2**31 - 1)]


@router.get("/", response_model=ProveedoresPagina)
def listar_proveedores(
    estado: bool | None = True,
    buscar: str | None = Query(default=None, max_length=150),
    pagina: int = Query(default=1, ge=1, le=2**31 - 1),
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.listar(db, estado=estado, buscar=buscar, pagina=pagina)


@router.get("/{proveedor_id}", response_model=ProveedorRespuesta)
def consultar_proveedor(
    proveedor_id: IdProveedor,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.consultar(db, proveedor_id)


@router.post("/", response_model=ProveedorOperacion, status_code=201)
def registrar_proveedor(
    datos: ProveedorCrear,
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(permiso_escritura),
):
    return service.crear(
        db, datos, usuario, request.client.host if request.client else None,
    )


@router.put("/{proveedor_id}", response_model=ProveedorOperacion)
def actualizar_proveedor(
    proveedor_id: IdProveedor,
    datos: ProveedorActualizar,
    request: Request,
    confirmar: bool = Query(default=False),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(permiso_escritura),
):
    return service.actualizar(
        db, proveedor_id, datos, usuario,
        request.client.host if request.client else None, confirmar=confirmar,
    )


@router.delete("/{proveedor_id}", status_code=204, response_class=Response)
def dar_baja_proveedor(
    proveedor_id: IdProveedor,
    request: Request,
    confirmar: bool = Query(default=False),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(permiso_escritura),
):
    service.dar_baja(
        db, proveedor_id, usuario,
        request.client.host if request.client else None, confirmar=confirmar,
    )
    return Response(status_code=204)