from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.repositories import proveedor as repo
from app.modules.gestion_inventario_proveedores.schemas.proveedor import (
    ProveedorActualizar,
    ProveedorCrear,
)
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


ENTIDAD = "proveedores"
_NIT_DUPLICADO = "El NIT ya esta registrado en otro proveedor"


def _obtener_o_404(db: Session, proveedor_id: int, *, bloquear: bool = False):
    proveedor = repo.obtener_por_id(db, proveedor_id, bloquear=bloquear)
    if proveedor is None:
        raise HTTPException(404, "Proveedor no encontrado")
    return proveedor


def _validar_nit_disponible(db: Session, nit: str | None, *, excluir_id: int | None = None):
    if nit is not None and repo.existe_nit(db, nit, excluir_id=excluir_id):
        raise HTTPException(409, _NIT_DUPLICADO)


def _auditar(db: Session, usuario, ip: str | None, accion: str, proveedor, descripcion: str):
    registrar_bitacora(
        db=db,
        usuario_id=usuario.id,
        ip=ip,
        accion=accion,
        entidad_afectada=ENTIDAD,
        id_registro_afectado=proveedor.id,
        descripcion=descripcion,
    )


def listar(
    db: Session,
    *,
    estado: bool | None = True,
    busqueda: str | None = None,
    skip: int = 0,
    limit: int = 100,
):
    return repo.listar(db, estado=estado, busqueda=busqueda, skip=skip, limit=limit)


def consultar(db: Session, proveedor_id: int):
    return _obtener_o_404(db, proveedor_id)


def crear(db: Session, datos: ProveedorCrear, usuario, ip: str | None = None):
    try:
        _validar_nit_disponible(db, datos.nit)
        proveedor = repo.crear(db, datos.model_dump())
        _auditar(
            db, usuario, ip, "CREAR_PROVEEDOR", proveedor,
            f"Proveedor '{proveedor.razon_social}' creado",
        )
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError as exc:
        # Carrera entre dos altas con el mismo NIT: decide el indice unico.
        db.rollback()
        raise HTTPException(409, _NIT_DUPLICADO) from exc
    except Exception:
        db.rollback()
        raise


def actualizar(
    db: Session,
    proveedor_id: int,
    datos: ProveedorActualizar,
    usuario,
    ip: str | None = None,
):
    try:
        proveedor = _obtener_o_404(db, proveedor_id, bloquear=True)
        cambios = datos.model_dump(exclude_unset=True)
        if "nit" in cambios:
            _validar_nit_disponible(db, cambios["nit"], excluir_id=proveedor.id)
        proveedor = repo.actualizar(db, proveedor, cambios)
        _auditar(
            db, usuario, ip, "ACTUALIZAR_PROVEEDOR", proveedor,
            f"Proveedor '{proveedor.razon_social}' actualizado",
        )
        db.commit()
        db.refresh(proveedor)
        return proveedor
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, _NIT_DUPLICADO) from exc
    except Exception:
        db.rollback()
        raise


def desactivar(db: Session, proveedor_id: int, usuario, ip: str | None = None):
    # Baja logica: compra.proveedor_id referencia la fila con ON DELETE RESTRICT
    # y el historial de compras debe conservarse.
    try:
        proveedor = _obtener_o_404(db, proveedor_id, bloquear=True)
        if proveedor.estado is not True:
            raise HTTPException(409, "El proveedor ya esta inactivo")
        proveedor = repo.actualizar(db, proveedor, {"estado": False})
        _auditar(
            db, usuario, ip, "DESACTIVAR_PROVEEDOR", proveedor,
            f"Proveedor '{proveedor.razon_social}' desactivado",
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
