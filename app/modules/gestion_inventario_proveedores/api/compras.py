"""Rutas del CU29 con autenticación tenant y permisos de lectura/escritura."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.core.dependencies import (
    ACCION_ESCRITURA, ACCION_LECTURA, get_db, get_tenant_claims, requerir_permiso,
)
from app.modules.gestion_inventario_proveedores.schemas.compras import (
    MAX_ID, MAX_INT, CompraCrear, CompraOperacion, CompraRespuesta,
    ComprasPagina, LotesPagina, ProductosPagina,
)
from app.modules.gestion_inventario_proveedores.schemas.schemas import ProveedoresPagina
from app.modules.gestion_inventario_proveedores.services import compras as service
from app.modules.gestion_usuarios_seguridad.models.models import Usuario


def _exigir_bearer(request: Request):
    esquema, _, token = request.headers.get("Authorization", "").partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        raise HTTPException(
            401, "Se requiere una sesión válida", headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix="/compras", tags=["Registrar compras a proveedor"],
    dependencies=[Depends(_exigir_bearer), Depends(get_tenant_claims)],
)
permiso_lectura = requerir_permiso("Registrar compras a proveedor", ACCION_LECTURA)
permiso_escritura = requerir_permiso("Registrar compras a proveedor", ACCION_ESCRITURA)
IdRuta = Annotated[int, Path(gt=0, le=MAX_ID)]


@router.get("/proveedores", response_model=ProveedoresPagina)
def listar_proveedores(
    buscar: str | None = Query(default=None, max_length=150),
    pagina: int = Query(default=1, ge=1, le=MAX_INT),
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.listar_proveedores(db, buscar=buscar, pagina=pagina)


@router.get("/productos", response_model=ProductosPagina)
def listar_productos(
    buscar: str | None = Query(default=None, max_length=160),
    pagina: int = Query(default=1, ge=1, le=MAX_INT),
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.listar_productos(db, buscar=buscar, pagina=pagina)


@router.get("/productos/{producto_id}/lotes", response_model=LotesPagina)
def listar_lotes(
    producto_id: IdRuta,
    pagina: int = Query(default=1, ge=1, le=MAX_INT),
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.listar_lotes(db, producto_id, pagina=pagina)


@router.get("/", response_model=ComprasPagina)
def listar_compras(
    pagina: int = Query(default=1, ge=1, le=MAX_INT),
    proveedor_id: int | None = Query(default=None, gt=0, le=MAX_INT),
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.listar(db, pagina=pagina, proveedor_id=proveedor_id)


@router.get("/{compra_id}", response_model=CompraRespuesta)
def consultar_compra(
    compra_id: IdRuta,
    db: Session = Depends(get_db),
    _usuario: Usuario = Depends(permiso_lectura),
):
    return service.consultar(db, compra_id)


@router.post("/", response_model=CompraOperacion, status_code=201)
def registrar_compra(
    datos: CompraCrear,
    request: Request,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(permiso_escritura),
):
    return service.crear(db, datos, usuario, request.client.host if request.client else None)
