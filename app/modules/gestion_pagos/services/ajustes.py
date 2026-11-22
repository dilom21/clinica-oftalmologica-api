"""CU36 - Anular o corregir pagos con historial financiero estructurado.

Reglas (D1/D2 aprobadas):

* `Pago.monto` y `PagoDetalle.monto_aplicado` nunca se sobrescriben.
* Toda operacion se registra en `pago_ajuste` (+ detalle por servicio) en la
  misma transaccion local que la bitacora.
* Anular solo es valido cuando no hubo captura de dinero; si hubo dinero real
  la unica salida es una devolucion/reembolso con evidencia.
* Un pago `ANULADO` aporta cero y no recibe ademas un ajuste negativo.
* Un reembolso total confirmado deja el pago `REEMBOLSADO` con vigente cero.
* Nada se marca `REEMBOLSADO` sin confirmacion del proveedor o evidencia
  manual explicita del responsable que ejecuto la devolucion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pagos.models.models import (
    ESTADOS_PAGO_CAPTURADO,
    PagoAjuste,
)
from app.modules.gestion_pagos.providers import (
    ErrorProveedorPago,
    ErrorProveedorPagoDesconocido,
    ProveedorNoConfigurado,
    ProveedorPagoBase,
    ReembolsoProveedor,
)
from app.modules.gestion_pagos.repositories import administrativos as admin_repo
from app.modules.gestion_pagos.repositories import ajustes as repo
from app.modules.gestion_pagos.repositories import planes_cuotas as planes_repo
from app.modules.gestion_pagos.repositories import saldos
from app.modules.gestion_pagos.repositories import webhooks_huerfanos as huerfanos_repo
from app.modules.gestion_pagos.schemas.ajustes import (
    AjusteDetalleRespuesta,
    AnularPago,
    ConciliarAjuste,
    CorregirPago,
    HistorialAjustesRespuesta,
    PagoAjusteRespuesta,
    ReembolsarPago,
)
from app.modules.gestion_pagos.services import planes_cuotas
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


CENT = Decimal("0.01")
ROLES_ADMINISTRADOR = {"admin", "administrador"}
ROLES_ADMINISTRATIVOS = {"admin", "administrador", "recepcionista"}
ESTADOS_CON_DINERO = frozenset(ESTADOS_PAGO_CAPTURADO)


def _decimal(valor) -> Decimal:
    try:
        return Decimal(str(valor)).quantize(CENT)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(422, "Importe invalido") from exc


def _actor_administrativo(usuario) -> str:
    rol = nombre_rol_actual(usuario)
    if rol not in ROLES_ADMINISTRATIVOS:
        raise HTTPException(403, "Se requiere rol Administrador o Recepcionista")
    return rol


def _actor_admin(usuario) -> str:
    rol = _actor_administrativo(usuario)
    if rol not in ROLES_ADMINISTRADOR:
        raise HTTPException(
            403,
            "Solo un Administrador puede anular, devolver o reembolsar dinero "
            "capturado o conciliar operaciones externas",
        )
    return rol


def _bloquear_pago(db: Session, pago_id: int):
    """Bloquea el pago respetando plan -> cuota -> intento -> pago."""
    intento_previo = planes_repo.obtener_intento_por_pago(db, pago_id)
    if intento_previo is not None:
        cuota, intento, pago = planes_repo.bloquear_operacion_por_pago(
            db, pago_id,
        )
        if cuota is None or intento is None or pago is None:
            raise HTTPException(409, "El pago de cuota cambio durante la operacion")
        return intento.cuota, intento, pago
    pago = admin_repo.obtener_pago_administrativo(db, pago_id, bloquear=True)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    return None, None, pago


def _contexto_pago(pago):
    if not pago.detalles:
        raise HTTPException(409, "El pago no tiene servicios asociados")
    servicios = [detalle.servicio_realizado for detalle in pago.detalles]
    if any(item is None for item in servicios):
        raise HTTPException(409, "El pago tiene detalles inconsistentes")
    pacientes = {item.paciente_id for item in servicios}
    consultas = {item.consulta_clinica_id for item in servicios}
    if len(pacientes) != 1 or None in consultas or len(consultas) != 1:
        raise HTTPException(409, "El pago no tiene paciente y consulta consistentes")
    return next(iter(pacientes)), next(iter(consultas))


def _servicio_ids_pago(pago) -> set[int]:
    return {detalle.servicio_realizado_id for detalle in pago.detalles}


def _respuesta_ajuste(ajuste: PagoAjuste) -> PagoAjusteRespuesta:
    return PagoAjusteRespuesta(
        id=ajuste.id,
        pago_id=ajuste.pago_id,
        tipo=ajuste.tipo,
        naturaleza=ajuste.naturaleza,
        efecto_caja=ajuste.efecto_caja,
        estado=ajuste.estado,
        importe=_decimal(ajuste.importe),
        moneda=ajuste.moneda,
        motivo=ajuste.motivo,
        evidencia=ajuste.evidencia,
        referencia_externa=ajuste.referencia_externa,
        revertir_aplicacion_cuota=ajuste.revertir_aplicacion_cuota,
        creado_por=ajuste.creado_por,
        confirmado_por=ajuste.confirmado_por,
        conciliado_por=ajuste.conciliado_por,
        fecha_creacion=ajuste.fecha_creacion,
        fecha_confirmacion=ajuste.fecha_confirmacion,
        fecha_conciliacion=ajuste.fecha_conciliacion,
        observaciones=ajuste.observaciones,
        detalles=[
            AjusteDetalleRespuesta(
                servicio_realizado_id=detalle.servicio_realizado_id,
                monto=_decimal(detalle.monto),
            )
            for detalle in ajuste.detalles
        ],
    )


def _idempotente(
    existente: PagoAjuste | None,
    *,
    pago_id: int,
    tipo: str,
    naturaleza: str | None = None,
    importe=None,
    moneda: str | None = None,
    efecto_caja: str | None = None,
    filas: list[tuple[int, Decimal]] | None = None,
) -> PagoAjuste | None:
    """E-7: compara el payload financiero completo, no solo el importe.

    La misma clave idempotente solo puede devolver el resultado original cuando
    coinciden pago, tipo, naturaleza, importe, moneda, efecto de caja y la
    distribucion por servicio. Cualquier diferencia es HTTP 409, incluido el
    replay cruzado CORRECCION -> REEMBOLSO.
    """
    if existente is None:
        return None
    mismo = (
        existente.pago_id == pago_id
        and existente.tipo == tipo
        and (naturaleza is None or existente.naturaleza == naturaleza)
    )
    if importe is not None:
        mismo = mismo and _decimal(existente.importe) == _decimal(importe)
    if moneda is not None:
        mismo = mismo and existente.moneda.upper() == moneda.upper()
    if efecto_caja is not None:
        mismo = mismo and existente.efecto_caja == efecto_caja
    if filas is not None:
        esperado = {
            int(servicio_id): _decimal(monto)
            for servicio_id, monto in filas
        }
        actual = {
            detalle.servicio_realizado_id: _decimal(detalle.monto)
            for detalle in existente.detalles
        }
        mismo = mismo and actual == esperado
    if not mismo:
        raise HTTPException(409, "La clave idempotente ya fue usada con otros datos")
    return existente


def _validar_moneda(pago, moneda: str | None) -> str:
    esperada = pago.moneda.upper()
    if moneda is not None and moneda.upper() != esperada:
        raise HTTPException(409, "La moneda del ajuste no coincide con el pago")
    return esperada


def _filas_detalle(pago, importe_total: Decimal, servicios) -> list[tuple[int, Decimal]]:
    ids_pago = _servicio_ids_pago(pago)
    filas: list[tuple[int, Decimal]] = []
    for item in servicios:
        if item.servicio_realizado_id not in ids_pago:
            raise HTTPException(
                409,
                "El ajuste incluye un servicio que no pertenece al pago",
            )
        filas.append((item.servicio_realizado_id, _decimal(item.monto)))
    total = sum((monto for _, monto in filas), Decimal("0.00"))
    if total != _decimal(importe_total):
        raise HTTPException(
            409, "La distribucion por servicio debe sumar el importe del ajuste",
        )
    return filas


def _validar_topes(
    db: Session,
    pago,
    filas: list[tuple[int, Decimal]],
    *,
    permitir_sobre_precio: bool = False,
) -> None:
    """Impide dejar saldos negativos o inventar cobertura por encima del precio."""
    desglose = saldos.desglose_vigente_pago(db, pago)
    for servicio_id, monto in filas:
        vigente = _decimal(desglose.get(servicio_id, 0))
        resultado = vigente + monto
        if resultado < 0:
            raise HTTPException(
                409,
                f"El ajuste supera el importe vigente del servicio {servicio_id}",
            )
        if monto > 0 and not permitir_sobre_precio:
            detalle = next(
                (item for item in pago.detalles
                 if item.servicio_realizado_id == servicio_id),
                None,
            )
            precio = (
                _decimal(detalle.servicio_realizado.precio_aplicado)
                if detalle is not None
                and detalle.servicio_realizado.precio_aplicado is not None
                else None
            )
            # E-8: sin precio aplicado no se puede validar el tope. Nunca se
            # inventa un precio ni se modifica el registro historico.
            if precio is None or precio <= 0:
                raise HTTPException(
                    409,
                    "No se puede validar el tope de la correccion: el servicio "
                    f"{servicio_id} no tiene precio_aplicado valido",
                )
            if resultado > precio:
                raise HTTPException(
                    409,
                    "La correccion supera el precio aplicado del servicio "
                    f"{servicio_id}; el exceso debe devolverse, no contabilizarse",
                )


def _registrar(
    db: Session,
    *,
    pago,
    tipo: str,
    naturaleza: str,
    efecto_caja: str,
    estado: str,
    importe: Decimal,
    motivo: str,
    evidencia: str | None,
    clave_idempotencia: str,
    usuario,
    filas: list[tuple[int, Decimal]],
    revertir_cuota: bool = False,
    confirmado: bool = False,
    referencia_externa: str | None = None,
) -> PagoAjuste:
    ahora = datetime.now(timezone.utc)
    ajuste = repo.crear_ajuste(
        db,
        pago_id=pago.id,
        tipo=tipo,
        naturaleza=naturaleza,
        efecto_caja=efecto_caja,
        estado=estado,
        importe=importe,
        moneda=pago.moneda,
        motivo=motivo,
        evidencia=evidencia,
        clave_idempotencia=clave_idempotencia,
        revertir_aplicacion_cuota=revertir_cuota,
        creado_por=usuario.id,
        confirmado_por=usuario.id if confirmado else None,
        fecha_confirmacion=ahora if confirmado else None,
    )
    if referencia_externa is not None:
        ajuste.referencia_externa = referencia_externa
    if filas:
        repo.crear_detalles(db, ajuste.id, filas)
    return ajuste


def _aplicar_reversion_cuota(
    db: Session,
    intento,
    ajuste: PagoAjuste,
    *,
    usuario_id: int | None,
) -> None:
    if intento is None or not ajuste.revertir_aplicacion_cuota:
        return
    planes_cuotas.revertir_aplicacion_pago(
        db,
        intento,
        motivo=ajuste.motivo,
        usuario_id=usuario_id,
    )


def _confirmar_ajuste(
    db: Session,
    pago,
    ajuste: PagoAjuste,
    intento,
    *,
    usuario_id: int | None,
) -> None:
    ahora = datetime.now(timezone.utc)
    ajuste.estado = "CONFIRMADO"
    ajuste.confirmado_por = usuario_id or ajuste.creado_por
    ajuste.fecha_confirmacion = ahora
    ajuste.fecha_actualizacion = ahora
    db.flush()
    if ajuste.tipo in {"DEVOLUCION", "REEMBOLSO"}:
        _aplicar_reversion_cuota(db, intento, ajuste, usuario_id=usuario_id)
        if saldos.monto_vigente_pago(db, pago) <= 0:
            pago.estado = "REEMBOLSADO"
        elif pago.estado == "REEMBOLSADO":
            pago.estado = "APROBADO"
    registrar_bitacora(
        db=db,
        usuario_id=usuario_id,
        accion="CONFIRMAR_AJUSTE_PAGO",
        entidad_afectada="pago_ajuste",
        id_registro_afectado=ajuste.id,
        descripcion=(
            f"Ajuste {ajuste.tipo} confirmado sobre pago {pago.id}; "
            f"estado del pago: {pago.estado}"
        ),
    )
    db.flush()


def _marcar_terminal(ajuste: PagoAjuste, estado: str, *, usuario_id: int | None, observaciones=None):
    ahora = datetime.now(timezone.utc)
    ajuste.estado = estado
    ajuste.conciliado_por = usuario_id
    ajuste.fecha_conciliacion = ahora
    ajuste.fecha_actualizacion = ahora
    if observaciones is not None:
        ajuste.observaciones = observaciones


def anular_pago(
    db: Session,
    pago_id: int,
    datos: AnularPago,
    clave_idempotencia: str,
    usuario,
    ip: str | None = None,
    proveedor: ProveedorPagoBase | None = None,
) -> PagoAjusteRespuesta:
    rol = _actor_administrativo(usuario)
    try:
        existente = repo.obtener_ajuste_por_clave(db, clave_idempotencia)
        if existente is not None:
            ajuste = _idempotente(
                existente, pago_id=pago_id, tipo="ANULACION",
                naturaleza=datos.naturaleza, importe=Decimal("0.00"),
                efecto_caja="SIN_EFECTO", filas=[],
            )
            db.rollback()
            return _respuesta_ajuste(ajuste)

        pago = admin_repo.obtener_pago_administrativo(db, pago_id)
        if pago is None:
            raise HTTPException(404, "Pago no encontrado")
        if pago.estado not in {"PENDIENTE", "RECHAZADO", "APROBADO"}:
            raise HTTPException(
                409, f"No se puede anular un pago {pago.estado}",
            )
        if pago.estado == "APROBADO":
            if rol not in ROLES_ADMINISTRADOR:
                raise HTTPException(
                    403,
                    "Solo un Administrador puede anular un pago aprobado",
                )
            if not datos.evidencia:
                raise HTTPException(
                    409,
                    "Anular un pago aprobado exige evidencia de que no hubo "
                    "captura real de dinero",
                )
            if pago.pasarela == "STRIPE":
                raise HTTPException(
                    409,
                    "Un pago Stripe aprobado representa dinero capturado: "
                    "registre un reembolso, no una anulacion",
                )
            resumen = saldos.resumen_pago(db, pago)
            if resumen.ajustes_confirmados < 0:
                raise HTTPException(
                    409,
                    "El pago ya tiene devoluciones registradas; use el flujo "
                    "de reembolso",
                )
        if pago.pasarela == "STRIPE" and pago.estado in {"PENDIENTE", "RECHAZADO"}:
            if proveedor is None:
                raise HTTPException(
                    500, "No se pudo verificar la cancelacion en Stripe",
                )
            _confirmar_cancelacion_stripe(proveedor, pago)

        cuota, intento, pago = _bloquear_pago(db, pago_id)
        if pago.estado not in {"PENDIENTE", "RECHAZADO", "APROBADO"}:
            raise HTTPException(
                409, f"El pago cambio de estado a {pago.estado}; vuelva a intentar",
            )
        evidencia = datos.evidencia
        if pago.estado == "APROBADO" and not evidencia:
            raise HTTPException(
                409, "Anular un pago aprobado exige evidencia obligatoria",
            )
        ajuste = _registrar(
            db,
            pago=pago,
            tipo="ANULACION",
            naturaleza=datos.naturaleza,
            efecto_caja="SIN_EFECTO",
            estado="CONFIRMADO",
            importe=Decimal("0.00"),
            motivo=datos.motivo,
            evidencia=evidencia,
            clave_idempotencia=clave_idempotencia,
            usuario=usuario,
            filas=[],
            revertir_cuota=bool(intento and intento.aplicado_a_cuota),
            confirmado=True,
        )
        _aplicar_reversion_cuota(db, intento, ajuste, usuario_id=usuario.id)
        pago.estado = "ANULADO"
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="ANULAR_PAGO",
            entidad_afectada="pago",
            id_registro_afectado=pago.id,
            descripcion=f"Pago anulado ({datos.naturaleza}): {datos.motivo}",
        )
        db.commit()
        return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Clave idempotente duplicada para un ajuste",
        ) from exc
    except Exception:
        db.rollback()
        raise


def anular_intento_no_capturado(
    db: Session,
    pago,
    *,
    motivo: str,
    clave_idempotencia: str,
    usuario_id: int | None = None,
    ip: str | None = None,
    evidencia: str | None = None,
) -> PagoAjuste:
    """E-6: unica via para anular un intento no capturado fuera de la API CU36.

    Los pagos PENDIENTE/RECHAZADO se registran en el libro CU36 con su bitacora.
    Un pago APROBADO nunca se anula automaticamente: el proveedor pudo capturar
    dinero real y se exige conciliacion administrativa.
    """
    if pago.estado == "APROBADO":
        raise HTTPException(
            409,
            "El pago Stripe ya fue capturado; requiere conciliacion administrativa",
        )
    if pago.estado not in {"PENDIENTE", "RECHAZADO"}:
        raise HTTPException(
            409, f"No se puede anular un pago {pago.estado}",
        )
    existente = repo.obtener_ajuste_por_clave(db, clave_idempotencia)
    if existente is not None:
        return existente
    ajuste = repo.crear_ajuste(
        db,
        pago_id=pago.id,
        tipo="ANULACION",
        naturaleza="OTRO",
        efecto_caja="SIN_EFECTO",
        estado="CONFIRMADO",
        importe=Decimal("0.00"),
        moneda=pago.moneda,
        motivo=motivo,
        evidencia=evidencia
        or "PaymentIntent cancelado confirmado en el proveedor",
        clave_idempotencia=clave_idempotencia,
        creado_por=usuario_id,
        confirmado_por=usuario_id,
        fecha_confirmacion=datetime.now(timezone.utc),
        revertir_aplicacion_cuota=False,
        observaciones=(
            f"Anulacion automatica de intento no capturado (pago {pago.id})"
        ),
    )
    pago.estado = "ANULADO"
    registrar_bitacora(
        db=db,
        usuario_id=usuario_id,
        ip=ip,
        accion="ANULAR_PAGO_PROVEEDOR_CANCELADO",
        entidad_afectada="pago",
        id_registro_afectado=pago.id,
        descripcion=f"{motivo} (referencia {pago.referencia_transaccion})",
    )
    db.flush()
    return ajuste


def _confirmar_cancelacion_stripe(proveedor: ProveedorPagoBase, pago) -> None:
    """Confirma en Stripe que el pago no fue capturado antes de anularlo."""
    try:
        proveedor.validar_configuracion()
        if not pago.referencia_transaccion:
            raise HTTPException(
                409, "El pago Stripe no tiene referencia de PaymentIntent",
            )
        intento = proveedor.recuperar_intencion(pago.referencia_transaccion)
        if intento.status == "succeeded":
            raise HTTPException(
                409,
                "Stripe confirma que el pago fue capturado; registre un reembolso",
            )
        if intento.status != "canceled":
            intento = proveedor.cancelar_intencion(pago.referencia_transaccion)
        if intento.status != "canceled":
            raise HTTPException(
                409, "Stripe no confirmo la cancelacion del PaymentIntent",
            )
    except ProveedorNoConfigurado as error:
        raise HTTPException(500, str(error)) from error
    except ErrorProveedorPagoDesconocido as error:
        raise HTTPException(
            502,
            "Resultado desconocido al cancelar en Stripe; concilie antes de reintentar",
        ) from error
    except ErrorProveedorPago as error:
        raise HTTPException(502, str(error)) from error


def corregir_pago(
    db: Session,
    pago_id: int,
    datos: CorregirPago,
    clave_idempotencia: str,
    usuario,
    ip: str | None = None,
) -> PagoAjusteRespuesta:
    rol = _actor_administrativo(usuario)
    try:
        pago = admin_repo.obtener_pago_administrativo(db, pago_id)
        if pago is None:
            raise HTTPException(404, "Pago no encontrado")
        moneda = _validar_moneda(pago, datos.moneda)
        importe = _decimal(datos.importe)
        if importe > 0 and rol not in ROLES_ADMINISTRADOR:
            raise HTTPException(
                403,
                "Solo un Administrador puede corregir el importe al alza",
            )
        filas = _filas_detalle(pago, importe, datos.servicios)

        existente = repo.obtener_ajuste_por_clave(db, clave_idempotencia)
        if existente is not None:
            ajuste = _idempotente(
                existente, pago_id=pago_id, tipo="CORRECCION",
                naturaleza=datos.naturaleza, importe=importe, moneda=moneda,
                efecto_caja="SIN_EFECTO", filas=filas,
            )
            db.rollback()
            return _respuesta_ajuste(ajuste)

        cuota, intento, pago = _bloquear_pago(db, pago_id)
        if cuota is not None:
            raise HTTPException(
                409,
                "Un pago aplicado a una cuota no admite correccion de importe; "
                "use anulacion o devolucion total",
            )
        if pago.estado != "APROBADO":
            raise HTTPException(
                409,
                f"Solo un pago APROBADO admite correccion (estado: {pago.estado})",
            )
        if saldos.pago_tiene_ajuste_abierto(db, pago.id):
            raise HTTPException(
                409, "Existe una operacion externa pendiente de conciliar",
            )
        if importe > 0 and not datos.evidencia:
            raise HTTPException(
                409,
                "Una correccion al alza exige evidencia del dinero recibido",
            )
        _validar_topes(db, pago, filas)
        vigente = saldos.monto_vigente_pago(db, pago)
        if vigente + importe <= 0:
            raise HTTPException(
                409,
                "La correccion dejaria el pago sin importe vigente; "
                "use el flujo de anulacion",
            )
        ajuste = _registrar(
            db,
            pago=pago,
            tipo="CORRECCION",
            naturaleza=datos.naturaleza,
            efecto_caja="SIN_EFECTO",
            estado="CONFIRMADO",
            importe=importe,
            motivo=datos.motivo,
            evidencia=datos.evidencia,
            clave_idempotencia=clave_idempotencia,
            usuario=usuario,
            filas=filas,
            confirmado=True,
        )
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="CORREGIR_PAGO",
            entidad_afectada="pago",
            id_registro_afectado=pago.id,
            descripcion=(
                f"Correccion de importe {importe} {moneda} "
                f"({datos.naturaleza}): {datos.motivo}"
            ),
        )
        db.commit()
        return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Clave idempotente duplicada para un ajuste",
        ) from exc
    except Exception:
        db.rollback()
        raise


def reembolsar_pago(
    db: Session,
    pago_id: int,
    datos: ReembolsarPago,
    clave_idempotencia: str,
    usuario,
    ip: str | None = None,
    proveedor: ProveedorPagoBase | None = None,
) -> PagoAjusteRespuesta:
    _actor_admin(usuario)
    try:
        pago = admin_repo.obtener_pago_administrativo(db, pago_id)
        if pago is None:
            raise HTTPException(404, "Pago no encontrado")
        moneda = _validar_moneda(pago, datos.moneda)
        importe = _decimal(datos.importe)
        filas = _filas_detalle(pago, importe, datos.servicios)
        es_stripe = pago.pasarela == "STRIPE"
        tipo = "REEMBOLSO" if es_stripe else "DEVOLUCION"
        efecto = "DEVOLUCION_STRIPE" if es_stripe else "DEVOLUCION_MANUAL"

        existente = repo.obtener_ajuste_por_clave(db, clave_idempotencia)
        if existente is not None:
            ajuste = _idempotente(
                existente, pago_id=pago_id, tipo=tipo,
                naturaleza=datos.naturaleza, importe=importe,
                moneda=moneda, efecto_caja=efecto, filas=filas,
            )
            db.rollback()
            return _respuesta_ajuste(ajuste)

        cuota, intento, pago = _bloquear_pago(db, pago_id)
        if pago.estado != "APROBADO":
            raise HTTPException(
                409,
                "Solo un pago APROBADO con dinero capturado puede devolverse "
                f"(estado: {pago.estado})",
            )
        if saldos.pago_tiene_ajuste_abierto(db, pago.id):
            raise HTTPException(
                409, "Existe un ajuste pendiente; concilielo antes de reintentar",
            )
        vigente = saldos.monto_vigente_pago(db, pago)
        if abs(importe) > vigente:
            raise HTTPException(
                409, "La devolucion supera el importe vigente del pago",
            )
        if cuota is not None and abs(importe) < vigente:
            raise HTTPException(
                409,
                "Un pago aplicado a una cuota no admite devolucion parcial: "
                "el modelo de cuota no representa cobertura parcial (D4)",
            )
        _validar_topes(db, pago, filas)
        total = abs(importe) >= vigente
        if not es_stripe and not (datos.evidencia or datos.referencia_devolucion):
            raise HTTPException(
                409,
                "Una devolucion manual exige evidencia o referencia del "
                "comprobante de devolucion",
            )

        ajuste = _registrar(
            db,
            pago=pago,
            tipo=tipo,
            naturaleza=datos.naturaleza,
            efecto_caja=efecto,
            estado="PENDIENTE_PROVEEDOR" if es_stripe else "CONFIRMADO",
            importe=importe,
            motivo=datos.motivo,
            evidencia=datos.evidencia or datos.referencia_devolucion,
            clave_idempotencia=clave_idempotencia,
            usuario=usuario,
            filas=filas,
            revertir_cuota=bool(intento and intento.aplicado_a_cuota and total),
            confirmado=not es_stripe,
        )
        if not es_stripe:
            _confirmar_ajuste(
                db, pago, ajuste, intento, usuario_id=usuario.id,
            )
            registrar_bitacora(
                db=db,
                usuario_id=usuario.id,
                ip=ip,
                accion="DEVOLVER_PAGO",
                entidad_afectada="pago",
                id_registro_afectado=pago.id,
                descripcion=(
                    f"Devolucion {moneda} {abs(importe)} registrada con evidencia: "
                    f"{datos.motivo}"
                ),
            )
            db.commit()
            return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))

        if proveedor is None:
            raise HTTPException(500, "Proveedor de pagos no disponible")
        if not pago.referencia_transaccion:
            raise HTTPException(
                409, "El pago Stripe no tiene referencia de PaymentIntent",
            )
        metadata = _metadata_reembolso(db, pago, ajuste)
        ajuste_id = ajuste.id
        payment_intent_id = pago.referencia_transaccion
        # Sin locks de base durante la llamada externa.
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Clave idempotente duplicada para un ajuste",
        ) from exc
    except Exception:
        db.rollback()
        raise

    return _ejecutar_reembolso_stripe(
        db, ajuste_id, payment_intent_id, abs(importe), metadata,
        usuario_id=usuario.id, ip=ip, proveedor=proveedor,
    )


def _metadata_reembolso(db: Session, pago, ajuste) -> dict[str, str]:
    _, consulta_id = _contexto_pago(pago)
    scope = pagos_service._stripe_payment_scope(db)
    return {
        **scope.metadata,
        "pago_id": str(pago.id),
        "consulta_clinica_id": str(consulta_id),
        "ajuste_id": str(ajuste.id),
    }


def _ejecutar_reembolso_stripe(
    db: Session,
    ajuste_id: int,
    payment_intent_id: str,
    importe: Decimal,
    metadata: dict[str, str],
    *,
    usuario_id: int,
    ip: str | None,
    proveedor: ProveedorPagoBase,
) -> PagoAjusteRespuesta:
    prefijo = metadata.get("tenant_id", "clinica")
    clave = f"clinica-tenant-{prefijo}-ajuste-{ajuste_id}"
    reembolso = None
    error_desconocido = False
    try:
        proveedor.validar_configuracion()
        reembolso = proveedor.crear_reembolso(
            payment_intent_id=payment_intent_id,
            amount=pagos_service.monto_a_unidad_minima(importe),
            metadata=metadata,
            idempotency_key=clave,
        )
    except ErrorProveedorPagoDesconocido:
        error_desconocido = True
    except ProveedorNoConfigurado as error:
        _persistir_fallo_reembolso(db, ajuste_id, "Stripe no esta configurado")
        raise HTTPException(500, str(error)) from error
    except ErrorProveedorPago as error:
        _persistir_fallo_reembolso(db, ajuste_id, str(error))
        raise HTTPException(502, str(error)) from error

    try:
        ajuste = repo.obtener_ajuste(db, ajuste_id, bloquear=True)
        if ajuste is None:
            raise HTTPException(404, "Ajuste no encontrado")
        _, intento, pago = _bloquear_pago(db, ajuste.pago_id)
        if error_desconocido:
            ajuste.estado = "DESCONOCIDO"
            ajuste.fecha_actualizacion = datetime.now(timezone.utc)
            ajuste.observaciones = (
                "Resultado del reembolso desconocido; conciliar con el proveedor"
            )
            registrar_bitacora(
                db=db, usuario_id=usuario_id, ip=ip,
                accion="REEMBOLSO_STRIPE_DESCONOCIDO",
                entidad_afectada="pago_ajuste", id_registro_afectado=ajuste.id,
                descripcion="Stripe no respondio; no se marca como reembolsado",
            )
            db.commit()
            return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))

        ajuste.referencia_externa = reembolso.id
        ajuste.fecha_actualizacion = datetime.now(timezone.utc)
        if reembolso.confirmed:
            _confirmar_ajuste(db, pago, ajuste, intento, usuario_id=usuario_id)
            registrar_bitacora(
                db=db, usuario_id=usuario_id, ip=ip,
                accion="REEMBOLSO_STRIPE_CONFIRMADO",
                entidad_afectada="pago_ajuste", id_registro_afectado=ajuste.id,
                descripcion=(
                    f"Stripe confirmo reembolso {reembolso.id} "
                    f"({reembolso.status})"
                ),
            )
        elif reembolso.status in {"failed", "canceled"}:
            ajuste.estado = "FALLIDO" if reembolso.status == "failed" else "CANCELADO"
            ajuste.observaciones = reembolso.failure_reason
        else:
            ajuste.estado = "PENDIENTE_PROVEEDOR"
            ajuste.observaciones = f"Estado proveedor: {reembolso.status}"
        db.commit()
        return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))
    except Exception:
        db.rollback()
        raise


def _persistir_fallo_reembolso(db: Session, ajuste_id: int, detalle: str) -> None:
    try:
        ajuste = repo.obtener_ajuste(db, ajuste_id, bloquear=True)
        if ajuste is None:
            return
        ajuste.estado = "FALLIDO"
        ajuste.fecha_actualizacion = datetime.now(timezone.utc)
        ajuste.observaciones = detalle[:2000]
        db.commit()
    except Exception:
        db.rollback()


def aplicar_reembolso_proveedor(
    db: Session,
    ajuste: PagoAjuste,
    reembolso: ReembolsoProveedor,
    *,
    usuario_id: int | None = None,
) -> PagoAjuste:
    """Aplica el estado confirmado por Stripe (webhook o conciliacion)."""
    intento = planes_repo.obtener_intento_por_pago(db, ajuste.pago_id)
    _, _, pago = (
        planes_repo.bloquear_operacion_por_pago(db, ajuste.pago_id)
        if intento is not None
        else (None, None, admin_repo.obtener_pago_administrativo(
            db, ajuste.pago_id, bloquear=True,
        ))
    )
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    if ajuste.referencia_externa and ajuste.referencia_externa != reembolso.id:
        raise HTTPException(409, "El reembolso no corresponde al ajuste")
    ajuste.referencia_externa = reembolso.id
    ajuste.fecha_actualizacion = datetime.now(timezone.utc)
    if reembolso.status == "succeeded":
        if ajuste.estado != "CONFIRMADO":
            _confirmar_ajuste(db, pago, ajuste, intento, usuario_id=usuario_id)
    elif reembolso.status == "failed":
        ajuste.estado = "FALLIDO"
        ajuste.observaciones = reembolso.failure_reason
    elif reembolso.status == "canceled":
        ajuste.estado = "CANCELADO"
    else:
        ajuste.estado = "PENDIENTE_PROVEEDOR"
        ajuste.observaciones = f"Estado proveedor: {reembolso.status}"
    db.flush()
    return ajuste


def conciliar_ajuste(
    db: Session,
    ajuste_id: int,
    datos: ConciliarAjuste,
    usuario,
    ip: str | None = None,
    proveedor: ProveedorPagoBase | None = None,
) -> PagoAjusteRespuesta:
    _actor_admin(usuario)
    # E-4: ninguna llamada externa se ejecuta con FOR UPDATE retenido. Se lee y
    # valida sin bloquear, se cierra la transaccion, se consulta al proveedor y
    # solo despues se reabre para bloquear, revalidar y persistir.
    ajuste = repo.obtener_ajuste(db, ajuste_id)
    if ajuste is None:
        raise HTTPException(404, "Ajuste no encontrado")
    if ajuste.estado == "CONFIRMADO":
        db.rollback()
        return _respuesta_ajuste(ajuste)
    if ajuste.estado not in {"PENDIENTE_PROVEEDOR", "DESCONOCIDO", "FALLIDO"}:
        raise HTTPException(
            409, f"El ajuste esta {ajuste.estado} y no requiere conciliacion",
        )
    es_stripe = ajuste.efecto_caja == "DEVOLUCION_STRIPE"
    reembolso = None
    if es_stripe:
        if proveedor is None:
            raise HTTPException(500, "Proveedor de pagos no disponible")
        if datos.refund_id is not None and not datos.evidencia:
            raise HTTPException(
                409,
                "La conciliacion manual con refund_id exige evidencia "
                "auditable y rol administrador",
            )
        db.commit()
        try:
            reembolso = _buscar_reembolso_proveedor(
                db, ajuste, proveedor, refund_id=datos.refund_id,
            )
        except Exception:
            db.rollback()
            raise
        if reembolso is None:
            raise HTTPException(
                409,
                "Stripe no reporta un reembolso atribuible de forma "
                "inequivoca a este ajuste (metadata, importe y moneda); "
                "no se puede confirmar",
            )

    try:
        ajuste = repo.obtener_ajuste(db, ajuste_id, bloquear=True)
        if ajuste is None:
            raise HTTPException(404, "Ajuste no encontrado")
        if ajuste.estado == "CONFIRMADO":
            db.rollback()
            return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))
        if ajuste.estado not in {"PENDIENTE_PROVEEDOR", "DESCONOCIDO", "FALLIDO"}:
            raise HTTPException(
                409, f"El ajuste esta {ajuste.estado} y no requiere conciliacion",
            )
        if es_stripe:
            if datos.evidencia:
                ajuste.evidencia = datos.evidencia
            ajuste = aplicar_reembolso_proveedor(
                db, ajuste, reembolso, usuario_id=usuario.id,
            )
            if ajuste.estado == "PENDIENTE_PROVEEDOR":
                raise HTTPException(
                    409, f"El proveedor mantiene el reembolso en {reembolso.status}",
                )
            marcados = huerfanos_repo.marcar_atribuidos_por_refund(
                db, reembolso.id, ajuste_id=ajuste.id,
                motivo=(
                    f"Conciliado manualmente por administrador {usuario.id}"
                    if datos.refund_id is not None
                    else f"Conciliado por webhook verificado ({ajuste.id})"
                ),
            )
            if datos.refund_id is not None:
                registrar_bitacora(
                    db=db, usuario_id=usuario.id, ip=ip,
                    accion="STRIPE_REFUND_CONCILIADO_MANUAL",
                    entidad_afectada="pago_ajuste",
                    id_registro_afectado=ajuste.id,
                    descripcion=(
                        f"Refund {reembolso.id} atribuido manualmente con "
                        f"evidencia; eventos huerfanos marcados: {marcados}"
                    ),
                )
        elif datos.resultado == "CONFIRMADO":
            if not datos.evidencia:
                raise HTTPException(
                    409, "Confirmar un ajuste manual exige evidencia",
                )
            pago = admin_repo.obtener_pago_administrativo(
                db, ajuste.pago_id, bloquear=True,
            )
            intento = planes_repo.obtener_intento_por_pago(db, ajuste.pago_id)
            ajuste.evidencia = datos.evidencia
            if ajuste.tipo in {"DEVOLUCION"}:
                _confirmar_ajuste(
                    db, pago, ajuste, intento, usuario_id=usuario.id,
                )
            else:
                ajuste.estado = "CONFIRMADO"
                ajuste.confirmado_por = usuario.id
                ajuste.fecha_confirmacion = datetime.now(timezone.utc)
        else:
            _marcar_terminal(
                ajuste, datos.resultado, usuario_id=usuario.id,
                observaciones=datos.observaciones,
            )
        ajuste.conciliado_por = usuario.id
        ajuste.fecha_conciliacion = datetime.now(timezone.utc)
        ajuste.fecha_actualizacion = datetime.now(timezone.utc)
        if datos.observaciones:
            ajuste.observaciones = datos.observaciones
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="CONCILIAR_AJUSTE_PAGO",
            entidad_afectada="pago_ajuste",
            id_registro_afectado=ajuste.id,
            descripcion=f"Ajuste conciliado como {ajuste.estado}",
        )
        db.commit()
        return _respuesta_ajuste(repo.obtener_ajuste(db, ajuste.id))
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def _reembolso_coincide_con_ajuste(
    ajuste: PagoAjuste,
    pago,
    reembolso: ReembolsoProveedor,
    *,
    monto_esperado: int,
    moneda_esperada: str,
) -> bool:
    """Un refund solo pertenece al ajuste si metadata, importe y moneda cuadran."""
    metadata = reembolso.metadata or {}
    ajuste_metadata = metadata.get("ajuste_id")
    if ajuste_metadata is not None and str(ajuste_metadata) != str(ajuste.id):
        return False
    pago_metadata = metadata.get("pago_id")
    if pago_metadata is not None and str(pago_metadata) != str(pago.id):
        return False
    if (
        reembolso.payment_intent_id
        and pago.referencia_transaccion
        and reembolso.payment_intent_id != pago.referencia_transaccion
    ):
        return False
    if int(reembolso.amount or 0) != monto_esperado:
        return False
    return str(reembolso.currency or "").upper() == moneda_esperada


def _buscar_reembolso_proveedor(
    db: Session,
    ajuste: PagoAjuste,
    proveedor: ProveedorPagoBase,
    *,
    refund_id: str | None = None,
) -> ReembolsoProveedor | None:
    """Devuelve el refund inequivocamente atribuible al ajuste, o None.

    Nunca se toma el primer refund de la lista: multiples reembolsos sobre el
    mismo PaymentIntent son normales. Sin coincidencia exacta (metadata,
    importe y moneda) el ajuste queda sin confirmar.

    ``refund_id`` solo puede venir de una conciliacion manual de un
    administrador con evidencia (E-3). En ese caso se valida PaymentIntent,
    importe, moneda y que la metadata no apunte a otro ajuste/pago; nunca se
    relaja la atribucion automatica (E-2).
    """
    pago = admin_repo.obtener_pago_administrativo(db, ajuste.pago_id)
    if pago is None or not pago.referencia_transaccion:
        return None
    monto_esperado = pagos_service.monto_a_unidad_minima(
        abs(_decimal(ajuste.importe)),
    )
    moneda_esperada = pago.moneda.upper()
    try:
        proveedor.validar_configuracion()
        if refund_id is not None:
            reembolso = proveedor.recuperar_reembolso(refund_id)
            metadata = reembolso.metadata or {}
            if metadata.get("ajuste_id") not in (None, str(ajuste.id)):
                raise HTTPException(
                    409, "El refund explicito pertenece a otro ajuste",
                )
            if metadata.get("pago_id") not in (None, str(pago.id)):
                raise HTTPException(
                    409, "El refund explicito pertenece a otro pago",
                )
            if not reembolso.payment_intent_id:
                raise HTTPException(
                    409, "El refund explicito no identifica su PaymentIntent",
                )
            if reembolso.payment_intent_id != pago.referencia_transaccion:
                raise HTTPException(
                    409,
                    "El PaymentIntent del refund no coincide con el pago del ajuste",
                )
            if int(reembolso.amount or 0) != monto_esperado:
                raise HTTPException(
                    409, "El importe del refund no coincide con el ajuste",
                )
            if str(reembolso.currency or "").upper() != moneda_esperada:
                raise HTTPException(
                    409, "La moneda del refund no coincide con el ajuste",
                )
            return reembolso

        if ajuste.referencia_externa:
            reembolso = proveedor.recuperar_reembolso(ajuste.referencia_externa)
            if not _reembolso_coincide_con_ajuste(
                ajuste, pago, reembolso,
                monto_esperado=monto_esperado,
                moneda_esperada=moneda_esperada,
            ):
                raise HTTPException(
                    409,
                    "El reembolso vinculado no coincide con la metadata, el "
                    "importe y la moneda del ajuste; no se confirma",
                )
            return reembolso

        candidatos = [
            reembolso
            for reembolso in proveedor.listar_reembolsos(
                pago.referencia_transaccion,
            )
            if _reembolso_coincide_con_ajuste(
                ajuste, pago, reembolso,
                monto_esperado=monto_esperado,
                moneda_esperada=moneda_esperada,
            )
        ]
        if not candidatos:
            return None
        # Solo un refund con metadata.ajuste_id propio es atribuible sin
        # ambiguedad; un refund sin metadata (p. ej. creado en el dashboard de
        # Stripe) no se atribuye automaticamente.
        atribuidos = [
            reembolso
            for reembolso in candidatos
            if str((reembolso.metadata or {}).get("ajuste_id", "")) == str(ajuste.id)
        ]
        if len(atribuidos) == 1:
            return atribuidos[0]
        return None
    except ProveedorNoConfigurado as error:
        raise HTTPException(500, str(error)) from error
    except ErrorProveedorPagoDesconocido as error:
        raise HTTPException(
            502, "Resultado desconocido al consultar el reembolso en Stripe",
        ) from error
    except ErrorProveedorPago as error:
        raise HTTPException(502, str(error)) from error


def historial_ajustes(db: Session, pago_id: int, usuario) -> HistorialAjustesRespuesta:
    _actor_administrativo(usuario)
    pago = admin_repo.obtener_pago_administrativo(db, pago_id)
    if pago is None:
        raise HTTPException(404, "Pago no encontrado")
    resumen = saldos.resumen_pago(db, pago)
    ajustes = repo.listar_ajustes_pago(db, pago_id)
    return HistorialAjustesRespuesta(
        pago_id=pago.id,
        importe_base=resumen.base,
        importe_vigente=resumen.vigente,
        importe_reembolsado=resumen.reembolsado,
        ajuste_abierto=resumen.ajuste_abierto,
        ajustes=[_respuesta_ajuste(item) for item in ajustes],
    )


def consultar_ajuste(db: Session, ajuste_id: int, usuario) -> PagoAjusteRespuesta:
    _actor_administrativo(usuario)
    ajuste = repo.obtener_ajuste(db, ajuste_id)
    if ajuste is None:
        raise HTTPException(404, "Ajuste no encontrado")
    return _respuesta_ajuste(ajuste)
