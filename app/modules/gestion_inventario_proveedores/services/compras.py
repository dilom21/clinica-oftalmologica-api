"""CU29: recepción atómica de compras en la base de la empresa autenticada."""

from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.time import fecha_hora_utc, fecha_local_aplicacion
from app.modules.gestion_inventario_proveedores.models.compras import (
    Compra, DetalleCompra, Inventario, Lote, MovimientoInventario,
)
from app.modules.gestion_inventario_proveedores.repositories import compras as repo
from app.modules.gestion_inventario_proveedores.schemas.compras import (
    MAX_INT, CompraCrear, CompraOperacion, CompraRespuesta, CompraResumen,
    ComprasPagina, DetalleCompraCrear, DetalleCompraRespuesta, LotesPagina, ProductosPagina,
)
from app.modules.gestion_inventario_proveedores.services import service as proveedores
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


def _contexto(db: Session):
    contexto = db.info.get("tenant_context")
    if contexto is None:
        raise HTTPException(401, "Se requiere una sesión de empresa válida")
    return contexto


def _resolver_lote(db: Session, detalle: DetalleCompraCrear, hoy: date) -> Lote:
    lote = repo.bloquear_lote(
        db, detalle.producto_id, lote_id=detalle.lote_id, numero_lote=detalle.numero_lote,
    )
    if lote is None:
        if detalle.lote_id is not None:
            raise HTTPException(404, "Lote no encontrado para el producto en esta empresa")
        lote = Lote(
            producto_id=detalle.producto_id, numero_lote=detalle.numero_lote,
            fecha_fabricacion=detalle.fecha_fabricacion,
            fecha_vencimiento=detalle.fecha_vencimiento, estado=True,
        )
        db.add(lote)
    else:
        if not lote.estado:
            raise HTTPException(409, "El lote seleccionado está inactivo")
        for campo in ("fecha_fabricacion", "fecha_vencimiento"):
            if campo in detalle.model_fields_set and getattr(detalle, campo) != getattr(lote, campo):
                raise HTTPException(409, "Las fechas indicadas no coinciden con las del lote registrado")
    if lote.fecha_vencimiento is not None and lote.fecha_vencimiento < hoy:
        raise HTTPException(422, "No se pueden recibir productos de un lote vencido")
    if lote.fecha_fabricacion is not None and lote.fecha_fabricacion > hoy:
        raise HTTPException(422, "La fabricación no puede ser posterior a la recepción")
    db.flush()
    return lote


def _resumen(compra: Compra, razon_social: str, empresa_id: int, total: Decimal):
    return CompraResumen(
        id=compra.id, empresa_id=empresa_id, proveedor_id=compra.proveedor_id,
        proveedor_razon_social=razon_social, usuario_id=compra.usuario_id,
        numero_documento=compra.numero_documento, fecha_compra=compra.fecha_compra,
        fecha_recepcion=compra.fecha_recepcion, moneda=compra.moneda,
        estado=compra.estado, observaciones=compra.observaciones, total=total,
    )


def consultar(db: Session, compra_id: int) -> CompraRespuesta:
    contexto = _contexto(db)
    fila = repo.obtener_compra(db, compra_id)
    if fila is None:
        raise HTTPException(404, "Compra no encontrada en esta empresa")
    compra, razon_social = fila
    filas = repo.obtener_detalles(db, compra.id)
    detalles = [DetalleCompraRespuesta(
        id=d.id, producto_id=lote.producto_id, producto_nombre=nombre,
        lote_id=lote.id, numero_lote=lote.numero_lote,
        fecha_fabricacion=lote.fecha_fabricacion, fecha_vencimiento=lote.fecha_vencimiento,
        cantidad=d.cantidad, costo_unitario=d.costo_unitario,
        subtotal=d.costo_unitario * d.cantidad, observaciones=d.observaciones,
    ) for d, lote, nombre in filas]
    total = sum((d.subtotal for d in detalles), Decimal("0.00"))
    resumen = _resumen(compra, razon_social, contexto.empresa_id, total)
    return CompraRespuesta(**resumen.model_dump(), detalles=detalles)


def crear(db: Session, datos: CompraCrear, usuario: Usuario, ip: str | None = None):
    """La sesión y el usuario deben provenir de las dependencias autenticadas."""
    try:
        _contexto(db)
        proveedor = repo.bloquear_proveedor(db, datos.proveedor_id)
        if proveedor is None:
            raise HTTPException(404, "Proveedor no encontrado en esta empresa")
        if proveedor.estado is not True:
            raise HTTPException(409, "El proveedor seleccionado está inactivo")
        if datos.numero_documento is not None and repo.existe_documento(
            db, proveedor.id, datos.numero_documento,
        ):
            raise HTTPException(409, "Ya existe una compra con ese documento para el proveedor")

        # Orden fijo de bloqueo; serializa también la creación de lotes e
        # inventarios ausentes para un producto en compras concurrentes.
        for producto_id in sorted({d.producto_id for d in datos.detalles}):
            producto = repo.bloquear_producto(db, producto_id)
            if producto is None:
                raise HTTPException(404, "Producto no encontrado en esta empresa")
            if not producto.estado:
                raise HTTPException(409, "Uno de los productos seleccionados está inactivo")

        ahora = fecha_hora_utc()
        hoy = fecha_local_aplicacion()
        compra = Compra(
            proveedor_id=proveedor.id, usuario_id=usuario.id,
            numero_documento=datos.numero_documento, moneda=datos.moneda,
            observaciones=datos.observaciones, estado="RECIBIDA",
            fecha_compra=ahora, fecha_recepcion=ahora,
            fecha_creacion=ahora, fecha_actualizacion=ahora,
        )
        repo.guardar(db, compra)
        lotes_usados = set()
        for entrada in datos.detalles:
            lote = _resolver_lote(db, entrada, hoy)
            # Detecta también el mismo lote enviado una vez por id y otra por número.
            if lote.id in lotes_usados:
                raise HTTPException(422, "Un lote solo puede aparecer una vez en la compra")
            lotes_usados.add(lote.id)
            inventario = repo.bloquear_inventario(db, lote.id)
            if inventario is None:
                inventario = Inventario(lote_id=lote.id, stock_actual=0, stock_reservado=0)
                db.add(inventario)
            anterior = inventario.stock_actual
            posterior = anterior + entrada.cantidad
            if posterior > MAX_INT:
                raise HTTPException(422, "La cantidad supera la capacidad de stock del lote")
            inventario.stock_actual = posterior
            inventario.fecha_actualizacion = ahora
            detalle = DetalleCompra(
                compra_id=compra.id, lote_id=lote.id, cantidad=entrada.cantidad,
                costo_unitario=entrada.costo_unitario, observaciones=entrada.observaciones,
            )
            repo.guardar(db, detalle)
            repo.guardar(db, MovimientoInventario(
                inventario_id=inventario.id, usuario_id=usuario.id,
                tipo="ENTRADA_COMPRA", cantidad=entrada.cantidad,
                stock_anterior=anterior, stock_posterior=posterior, fecha_hora=ahora,
                referencia_tipo="DETALLE_COMPRA", referencia_id=detalle.id,
                observaciones=f"Recepción de compra {compra.id}",
            ))
        registrar_bitacora(
            db=db, usuario_id=usuario.id, accion="REGISTRAR_COMPRA", ip=ip,
            entidad_afectada="compra", id_registro_afectado=compra.id,
            descripcion="Compra recibida y existencias actualizadas",
        )
        respuesta = CompraOperacion(
            mensaje="Compra registrada correctamente",
            compra=consultar(db, compra.id),
        )
        db.commit()
        return respuesta
    except IntegrityError as error:
        db.rollback()
        restriccion = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        mensaje = (
            "Ya existe una compra con ese documento para el proveedor"
            if restriccion == "uq_compra_proveedor_documento"
            else "La compra entra en conflicto con los datos registrados; revise los datos"
        )
        raise HTTPException(409, mensaje) from error
    except SQLAlchemyError as error:
        db.rollback()
        raise HTTPException(500, "No se pudo registrar la compra; no se guardaron cambios") from error
    except Exception:
        db.rollback()
        raise


def listar(db: Session, *, pagina: int = 1, proveedor_id: int | None = None):
    contexto = _contexto(db)
    filas, total = repo.listar(db, pagina=pagina, proveedor_id=proveedor_id)
    return ComprasPagina(
        items=[_resumen(c, nombre, contexto.empresa_id, importe) for c, nombre, importe in filas],
        total=total, pagina=pagina,
    )


def listar_proveedores(db: Session, *, buscar: str | None = None, pagina: int = 1):
    _contexto(db)
    return proveedores.listar(db, estado=True, buscar=buscar, pagina=pagina)


def listar_productos(db: Session, *, buscar: str | None = None, pagina: int = 1):
    _contexto(db)
    items, total = repo.listar_productos(db, buscar=buscar, pagina=pagina)
    return ProductosPagina(items=items, total=total, pagina=pagina)


def listar_lotes(db: Session, producto_id: int, *, pagina: int = 1):
    _contexto(db)
    if repo.obtener_producto_activo(db, producto_id) is None:
        raise HTTPException(404, "Producto activo no encontrado en esta empresa")
    items, total = repo.listar_lotes(
        db, producto_id, hoy=fecha_local_aplicacion(), pagina=pagina,
    )
    return LotesPagina(items=items, total=total, pagina=pagina)
