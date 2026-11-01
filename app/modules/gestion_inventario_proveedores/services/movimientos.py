"""CU27: registro transaccional de movimientos y consulta de existencias.

Solo usa la sesión tenant entregada por FastAPI; no abre conexiones externas.
Las operaciones automáticas (compras/devoluciones) se integran mediante
registrar_movimiento_automatico() dentro de la MISMA transacción del llamador.
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.models.catalogo import Categoria, Lote, Producto
from app.modules.gestion_inventario_proveedores.models.movimientos import Inventario, MovimientoInventario
from app.modules.gestion_inventario_proveedores.schemas.movimientos import (
    ExistenciaRespuesta, MovimientoManualCrear, MovimientoRespuesta,
)

_ENTRADAS = frozenset({"STOCK_INICIAL", "ENTRADA_COMPRA", "AJUSTE_ENTRADA", "DEVOLUCION_ENTRADA"})
_SALIDAS = frozenset({"SALIDA_USO", "AJUSTE_SALIDA", "DEVOLUCION_PROVEEDOR", "SALIDA_VENCIMIENTO"})
_AUTOMATICOS = frozenset({"ENTRADA_COMPRA", "DEVOLUCION_ENTRADA", "DEVOLUCION_PROVEEDOR"})
_AJUSTES = frozenset({"AJUSTE_ENTRADA", "AJUSTE_SALIDA", "SALIDA_VENCIMIENTO"})
_INT_MAX = 2_147_483_647


def _lote_habilitado(db: Session, lote_id: int, *, bloquear: bool) -> Lote:
    consulta = select(Lote).where(Lote.id == lote_id)
    if bloquear:
        # El bloqueo del lote serializa también la primera creación de Inventario.
        consulta = consulta.with_for_update()
    lote = db.scalar(consulta)
    if lote is None:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
    producto = db.get(Producto, lote.producto_id)
    if not lote.estado or producto is None or not producto.estado:
        raise HTTPException(status_code=409, detail="El lote o su producto están inactivos")
    categoria = db.get(Categoria, producto.categoria_id)
    if categoria is None or not categoria.estado:
        raise HTTPException(status_code=409, detail="La categoría del producto está inactiva")
    return lote


def _inventario_bloqueado(db: Session, lote_id: int) -> Inventario | None:
    return db.scalar(
        select(Inventario).where(Inventario.lote_id == lote_id).with_for_update()
    )


def _representar(mov: MovimientoInventario, lote_id: int) -> MovimientoRespuesta:
    return MovimientoRespuesta(
        id=mov.id, inventario_id=mov.inventario_id, lote_id=lote_id,
        usuario_id=mov.usuario_id, tipo=mov.tipo, cantidad=mov.cantidad,
        stock_anterior=mov.stock_anterior, stock_posterior=mov.stock_posterior,
        fecha_hora=mov.fecha_hora, observaciones=mov.observaciones,
        referencia_tipo=mov.referencia_tipo, referencia_id=mov.referencia_id,
    )


def _registrar(
    db: Session, *, lote_id: int, tipo: str, cantidad: int,
    usuario_id: int, observaciones: str | None,
    referencia_tipo: str | None = None, referencia_id: int | None = None,
) -> MovimientoRespuesta:
    # No commit aquí: así la actualización y la inserción son atómicas, y un
    # módulo de compras puede agrupar todos sus efectos en una transacción.
    if tipo not in (_ENTRADAS | _SALIDAS) or cantidad <= 0 or cantidad > _INT_MAX:
        raise HTTPException(status_code=422, detail="Tipo o cantidad inválidos")
    if (referencia_tipo is None) != (referencia_id is None):
        raise HTTPException(status_code=422, detail="Referencia incompleta")
    if referencia_id is not None and (referencia_id <= 0 or not referencia_tipo or not referencia_tipo.strip()):
        raise HTTPException(status_code=422, detail="Referencia inválida")
    if tipo in _AJUSTES and not (observaciones or "").strip():
        raise HTTPException(status_code=422, detail="Debe indicar el motivo del ajuste")

    lote = _lote_habilitado(db, lote_id, bloquear=True)
    if tipo == "SALIDA_VENCIMIENTO" and (
        lote.fecha_vencimiento is None or lote.fecha_vencimiento >= date.today()
    ):
        raise HTTPException(status_code=409, detail="El lote no está vencido")
    if tipo == "SALIDA_USO" and (
        lote.fecha_vencimiento is not None and lote.fecha_vencimiento < date.today()
    ):
        raise HTTPException(status_code=409, detail="No se puede consumir un lote vencido")

    inventario = _inventario_bloqueado(db, lote_id)
    if tipo == "STOCK_INICIAL":
        # Impide usar 'stock inicial' para ocultar operaciones posteriores.
        if inventario is not None:
            raise HTTPException(status_code=409, detail="El lote ya tiene inventario inicializado")
    elif inventario is None and tipo in _SALIDAS:
        raise HTTPException(status_code=409, detail="No existen existencias para este lote")

    if inventario is None:
        inventario = Inventario(lote_id=lote_id, stock_actual=0, stock_reservado=0)
        db.add(inventario)
        db.flush()

    previo = inventario.stock_actual
    posterior = previo + cantidad if tipo in _ENTRADAS else previo - cantidad
    if posterior < inventario.stock_reservado:
        raise HTTPException(status_code=409, detail="Stock insuficiente: existen unidades reservadas")
    if posterior < 0:
        raise HTTPException(status_code=409, detail="Stock insuficiente")
    if posterior > _INT_MAX:
        raise HTTPException(status_code=409, detail="El stock excede el límite permitido")
    inventario.stock_actual = posterior
    inventario.fecha_actualizacion = datetime.now(timezone.utc)

    mov = MovimientoInventario(
        inventario_id=inventario.id, usuario_id=usuario_id, tipo=tipo,
        cantidad=cantidad, stock_anterior=previo, stock_posterior=posterior,
        observaciones=observaciones,
        referencia_tipo=referencia_tipo, referencia_id=referencia_id,
    )
    db.add(mov)
    db.flush()
    db.refresh(mov)
    return _representar(mov, lote_id)


def registrar_manual(db: Session, datos: MovimientoManualCrear, usuario_id: int) -> MovimientoRespuesta:
    try:
        resultado = _registrar(
            db, lote_id=datos.lote_id, tipo=datos.tipo, cantidad=datos.cantidad,
            usuario_id=usuario_id, observaciones=datos.observaciones,
        )
        db.commit()
        return resultado
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Conflicto de integridad del inventario") from exc
    except Exception:
        db.rollback()
        raise


def registrar_movimiento_automatico(
    db: Session, *, lote_id: int, tipo: str, cantidad: int, usuario_id: int,
    referencia_tipo: str, referencia_id: int, observaciones: str | None = None,
) -> MovimientoRespuesta:
    """Integración interna para CU29. El llamador administra COMMIT/ROLLBACK.

    No se expone como ruta HTTP, impidiendo que el cliente falsifique una compra.
    """
    if tipo not in _AUTOMATICOS:
        raise ValueError("El tipo no corresponde a un movimiento automático")
    if not referencia_tipo or referencia_id <= 0:
        raise ValueError("La referencia de origen es obligatoria")
    return _registrar(
        db, lote_id=lote_id, tipo=tipo, cantidad=cantidad,
        usuario_id=usuario_id, observaciones=observaciones,
        referencia_tipo=referencia_tipo, referencia_id=referencia_id,
    )


def consultar_existencia(db: Session, lote_id: int) -> ExistenciaRespuesta:
    lote = db.get(Lote, lote_id)
    if lote is None:
        raise HTTPException(status_code=404, detail="Lote no encontrado")
    inv = db.scalar(select(Inventario).where(Inventario.lote_id == lote_id))
    return ExistenciaRespuesta(
        lote_id=lote_id, inventario_id=inv.id if inv else None,
        stock_actual=inv.stock_actual if inv else 0,
        stock_reservado=inv.stock_reservado if inv else 0,
        stock_disponible=(inv.stock_actual - inv.stock_reservado) if inv else 0,
    )


def listar_movimientos(
    db: Session, *, lote_id: int | None, skip: int, limit: int,
) -> list[MovimientoRespuesta]:
    stmt = select(MovimientoInventario, Inventario.lote_id).join(
        Inventario, Inventario.id == MovimientoInventario.inventario_id
    )
    if lote_id is not None:
        stmt = stmt.where(Inventario.lote_id == lote_id)
    stmt = stmt.order_by(MovimientoInventario.id.desc()).offset(skip).limit(limit)
    return [_representar(mov, lote) for mov, lote in db.execute(stmt).all()]
