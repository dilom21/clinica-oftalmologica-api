"""CU28: reglas y auditoría atómicas en la sesión de la clínica activa."""

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.repositories import repository as repo
from app.modules.gestion_inventario_proveedores.schemas.schemas import (
    ProveedorActualizar,
    ProveedorCrear,
    ProveedorOperacion,
    ProveedorRespuesta,
    ProveedoresPagina,
)
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def consultar(db: Session, proveedor_id: int, *, bloquear: bool = False):
    proveedor = repo.obtener_por_id(db, proveedor_id, bloquear=bloquear)
    if proveedor is None:
        raise HTTPException(404, "Proveedor no encontrado")
    return proveedor


def listar(db: Session, *, estado: bool | None = True,
           buscar: str | None = None, pagina: int = 1):
    items, total = repo.listar(db, estado=estado, buscar=buscar, pagina=pagina)
    return ProveedoresPagina(items=items, total=total, pagina=pagina)


def _validar_nit(db: Session, nit: str | None, proveedor_id: int | None = None):
    if nit is not None and repo.existe_nit(db, nit, proveedor_id):
        raise HTTPException(409, "El NIT ya está registrado en otro proveedor")


def _auditar(db, usuario, ip, proveedor, accion, descripcion):
    registrar_bitacora(
        db=db,
        usuario_id=usuario.id,
        accion=accion,
        ip=ip,
        entidad_afectada="proveedores",
        id_registro_afectado=proveedor.id,
        descripcion=descripcion,
    )


def _error_integridad(error: IntegrityError):
    diagnostico = getattr(error.orig, "diag", None)
    if getattr(diagnostico, "constraint_name", None) == "ix_proveedores_nit":
        return HTTPException(409, "El NIT ya está registrado en otro proveedor")
    return HTTPException(409, "La operación incumple una restricción de integridad")


def crear(db: Session, datos: ProveedorCrear, usuario: Usuario, ip: str | None = None):
    try:
        _validar_nit(db, datos.nit)
        proveedor = repo.crear(db, datos.model_dump())
        _auditar(db, usuario, ip, proveedor, "CREAR_PROVEEDOR", "Proveedor registrado")
        respuesta = ProveedorOperacion(
            mensaje="Proveedor registrado correctamente",
            proveedor=ProveedorRespuesta.model_validate(proveedor),
        )
        db.commit()
        return respuesta
    except IntegrityError as error:
        db.rollback()
        raise _error_integridad(error) from error
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(500, "No se pudo registrar el proveedor") from error
    except Exception:
        db.rollback()
        raise


def actualizar(
    db: Session,
    proveedor_id: int,
    datos: ProveedorActualizar,
    usuario: Usuario,
    ip: str | None = None,
    *,
    confirmar: bool = False,
):
    try:
        proveedor = consultar(db, proveedor_id, bloquear=True)
        entrada = datos.model_dump(exclude_unset=True)
        cambios = {
            campo: valor for campo, valor in entrada.items()
            if getattr(proveedor, campo) != valor
        }
        if cambios.get("estado") is False and not confirmar:
            raise HTTPException(400, "Confirme la baja del proveedor con confirmar=true")
        if "nit" in cambios:
            _validar_nit(db, cambios["nit"], proveedor_id)
        accion = "ACTUALIZAR_PROVEEDOR"
        mensaje = "Proveedor actualizado correctamente"
        if "estado" in cambios:
            accion = "REACTIVAR_PROVEEDOR" if cambios["estado"] else "BAJA_PROVEEDOR"
            mensaje = (
                "Proveedor reactivado correctamente" if cambios["estado"]
                else "Proveedor dado de baja correctamente"
            )
        if cambios:
            proveedor = repo.actualizar(db, proveedor, cambios)
            _auditar(
                db, usuario, ip, proveedor, accion,
                "Campos modificados: " + ", ".join(sorted(cambios)),
            )
        else:
            mensaje = "No hay cambios que guardar"
        respuesta = ProveedorOperacion(
            mensaje=mensaje, proveedor=ProveedorRespuesta.model_validate(proveedor)
        )
        db.commit()
        return respuesta
    except IntegrityError as error:
        db.rollback()
        raise _error_integridad(error) from error
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(500, "No se pudo actualizar el proveedor") from error
    except Exception:
        db.rollback()
        raise


def dar_baja(
    db: Session,
    proveedor_id: int,
    usuario: Usuario,
    ip: str | None = None,
    *,
    confirmar: bool = False,
):
    if not confirmar:
        raise HTTPException(400, "Confirme la baja del proveedor con confirmar=true")
    # Nunca elimina filas ni modifica las compras que referencian al proveedor.
    return actualizar(
        db, proveedor_id, ProveedorActualizar(estado=False), usuario, ip,
        confirmar=True,
    )