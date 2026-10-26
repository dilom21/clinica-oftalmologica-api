"""CU26: endpoints administrativos tenant-aware para catálogo oftalmológico."""
from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import obtener_administrador_actual
from app.database.session import get_db
from app.modules.gestion_inventario_proveedores.schemas.catalogo import (
    CategoriaActualizar, CategoriaCrear, CategoriaRespuesta, EstadoActualizar,
    LoteActualizar, LoteCrear, LoteRespuesta, ProductoActualizar, ProductoCrear,
    ProductoRespuesta,
)
from app.modules.gestion_inventario_proveedores.services import catalogo

router = APIRouter(prefix="/inventario-proveedores", tags=["CU26 - Catálogo de productos y lotes"])

# Seguridad inicial: solo administradores de la clínica autenticada.
# En la siguiente entrega conectar CU26 con requerir_permiso(..., LECTURA/ESCRITURA)
# cuando las funciones del catálogo estén dadas de alta en las 7 bases tenant.


@router.get("/categorias", response_model=list[CategoriaRespuesta])
def categorias(incluir_inactivos: bool = False, db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.listar_categorias(db, incluir_inactivos)


@router.post("/categorias", response_model=CategoriaRespuesta, status_code=status.HTTP_201_CREATED)
def crear_categoria(data: CategoriaCrear, db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.crear_categoria(db, data)


@router.get("/categorias/{categoria_id}", response_model=CategoriaRespuesta)
def categoria(categoria_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.obtener_categoria(db, categoria_id)


@router.put("/categorias/{categoria_id}", response_model=CategoriaRespuesta)
def actualizar_categoria(data: CategoriaActualizar, categoria_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.actualizar_categoria(db, categoria_id, data)


@router.patch("/categorias/{categoria_id}/estado", response_model=CategoriaRespuesta)
def estado_categoria(data: EstadoActualizar, categoria_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.cambiar_estado_categoria(db, categoria_id, data)


@router.get("/productos", response_model=list[ProductoRespuesta])
def productos(categoria_id: int | None = Query(default=None, gt=0), incluir_inactivos: bool = False,
              db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.listar_productos(db, categoria_id, incluir_inactivos)


@router.post("/productos", response_model=ProductoRespuesta, status_code=status.HTTP_201_CREATED)
def crear_producto(data: ProductoCrear, db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.crear_producto(db, data)


@router.get("/productos/{producto_id}", response_model=ProductoRespuesta)
def producto(producto_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.obtener_producto(db, producto_id)


@router.put("/productos/{producto_id}", response_model=ProductoRespuesta)
def actualizar_producto(data: ProductoActualizar, producto_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.actualizar_producto(db, producto_id, data)


@router.patch("/productos/{producto_id}/estado", response_model=ProductoRespuesta)
def estado_producto(data: EstadoActualizar, producto_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.cambiar_estado_producto(db, producto_id, data)


@router.get("/lotes", response_model=list[LoteRespuesta])
def lotes(producto_id: int | None = Query(default=None, gt=0), incluir_inactivos: bool = False,
          db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.listar_lotes(db, producto_id, incluir_inactivos)


@router.post("/lotes", response_model=LoteRespuesta, status_code=status.HTTP_201_CREATED)
def crear_lote(data: LoteCrear, db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.crear_lote(db, data)


@router.get("/lotes/{lote_id}", response_model=LoteRespuesta)
def lote(lote_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.obtener_lote(db, lote_id)


@router.put("/lotes/{lote_id}", response_model=LoteRespuesta)
def actualizar_lote(data: LoteActualizar, lote_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.actualizar_lote(db, lote_id, data)


@router.patch("/lotes/{lote_id}/estado", response_model=LoteRespuesta)
def estado_lote(data: EstadoActualizar, lote_id: int = Path(gt=0), db: Session = Depends(get_db), _=Depends(obtener_administrador_actual)):
    return catalogo.cambiar_estado_lote(db, lote_id, data)
