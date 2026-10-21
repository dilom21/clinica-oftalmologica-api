from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

# Importamos la sesión de BD y nuestros archivos recién creados
from app.database.session import get_db  # Ajustado según la estructura de tu carpeta database
from app.modules.gestion_inventario_proveedores.models.proveedor import Proveedor
from app.modules.gestion_inventario_proveedores.schemas.proveedor import ProveedorCreate, ProveedorUpdate, ProveedorResponse

router = APIRouter(
    prefix="/proveedores",
    tags=["Gestionar Proveedores (CU28)"]
)

@router.post("/", response_model=ProveedorResponse, status_code=status.HTTP_201_CREATED)
def crear_proveedor(proveedor: ProveedorCreate, db: Session = Depends(get_db)):
    # Validamos que el NIT no exista previamente
    if proveedor.nit:
        db_proveedor = db.query(Proveedor).filter(Proveedor.nit == proveedor.nit).first()
        if db_proveedor:
            raise HTTPException(status_code=400, detail="El NIT ya está registrado en otro proveedor")
    
    nuevo_proveedor = Proveedor(**proveedor.model_dump())
    db.add(nuevo_proveedor)
    db.commit()
    db.refresh(nuevo_proveedor)
    return nuevo_proveedor

@router.get("/", response_model=List[ProveedorResponse])
def listar_proveedores(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Proveedor).offset(skip).limit(limit).all()

@router.get("/{proveedor_id}", response_model=ProveedorResponse)
def obtener_proveedor(proveedor_id: int, db: Session = Depends(get_db)):
    proveedor = db.query(Proveedor).filter(Proveedor.id == proveedor_id).first()
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    return proveedor

@router.put("/{proveedor_id}", response_model=ProveedorResponse)
def actualizar_proveedor(proveedor_id: int, proveedor_in: ProveedorUpdate, db: Session = Depends(get_db)):
    proveedor = db.query(Proveedor).filter(Proveedor.id == proveedor_id).first()
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    
    update_data = proveedor_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(proveedor, key, value)
        
    db.commit()
    db.refresh(proveedor)
    return proveedor

@router.delete("/{proveedor_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_proveedor(proveedor_id: int, db: Session = Depends(get_db)):
    proveedor = db.query(Proveedor).filter(Proveedor.id == proveedor_id).first()
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado")
    
    # Eliminación lógica cambiando el estado a False en lugar de borrar el registro físico
    proveedor.estado = False
    db.commit()
    return None