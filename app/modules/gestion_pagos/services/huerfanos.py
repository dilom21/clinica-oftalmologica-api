"""CU36 - Webhooks Stripe huerfanos y conciliacion manual auditada (E-3).

Un evento verificado sin metadata de ajuste ya no se responde con 409 (lo que
provocaba reintentos infinitos de Stripe sin dejar rastro local). Se persiste
primero en ``stripe_webhook_huerfano``; solo si la escritura es durable se
responde a Stripe. La atribucion posterior exige ``refund_id`` explicito,
evidencia, rol administrador y validacion contra el proveedor.

La atribucion automatica del webhook normal no se relaja: un refund sin
``metadata.ajuste_id`` nunca se contabiliza solo.
"""

from __future__ import annotations

import json
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.modules.gestion_pagos.repositories import ajustes as ajustes_repo
from app.modules.gestion_pagos.repositories import saldos
from app.modules.gestion_pagos.repositories import webhooks_huerfanos as repo
from app.modules.gestion_pagos.schemas.ajustes import (
    AtribuirHuerfanoCentral,
    AtribuirReembolsoHuerfano,
    WebhookHuerfanoCentralRespuesta,
    WebhookHuerfanoRespuesta,
)
from app.modules.gestion_pagos.services import ajustes as ajustes_service
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)

ROLES_ADMINISTRADOR = {"admin", "administrador"}


def _admin(usuario) -> None:
    if nombre_rol_actual(usuario) not in ROLES_ADMINISTRADOR:
        raise HTTPException(
            403,
            "Solo un Administrador puede conciliar reembolsos huerfanos",
        )


def _respuesta(registro) -> WebhookHuerfanoRespuesta:
    return WebhookHuerfanoRespuesta(
        id=registro.id,
        stripe_event_id=registro.stripe_event_id,
        event_type=registro.event_type,
        refund_id=registro.refund_id,
        payment_intent_id=registro.payment_intent_id,
        amount=registro.amount,
        currency=registro.currency,
        status=registro.status,
        estado=registro.estado,
        ajuste_id=registro.ajuste_id,
        motivo=registro.motivo,
        creado_en=registro.creado_en,
    )


def _respuesta_central(registro) -> WebhookHuerfanoCentralRespuesta:
    return WebhookHuerfanoCentralRespuesta(
        id=registro.id,
        stripe_event_id=registro.stripe_event_id,
        event_type=registro.event_type,
        refund_id=registro.refund_id,
        payment_intent_id=registro.payment_intent_id,
        amount=registro.amount,
        currency=registro.currency,
        status=registro.status,
        estado=registro.estado,
        ajuste_id=registro.ajuste_id,
        motivo=registro.motivo,
        creado_en=registro.creado_en,
        empresa_id=registro.empresa_id,
        tenant_database_id=registro.tenant_database_id,
        atribuido_por=registro.atribuido_por,
        actualizado_en=registro.actualizado_en,
    )


def _entero_positivo(valor) -> int:
    try:
        numero = int(valor)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Importe del reembolso invalido") from exc
    if numero <= 0:
        raise HTTPException(400, "Importe del reembolso invalido")
    return numero


def registrar_evento_huerfano(db: Session, evento: dict):
    """Persiste el refund huerfano antes de responder a Stripe.

    Cualquier fallo de persistencia se propaga: el endpoint responde 5xx y
    Stripe reintenta. Nunca se devuelve 200 sin registro durable.
    """
    evento_id = evento.get("id")
    if not isinstance(evento_id, str) or not evento_id.strip():
        raise HTTPException(400, "El evento Stripe no tiene identificador")
    tipo = evento.get("type")
    if not isinstance(tipo, str) or not tipo.strip():
        raise HTTPException(400, "El evento Stripe no tiene tipo")
    objeto = evento.get("data", {}).get("object", {})
    if not isinstance(objeto, dict):
        raise HTTPException(400, "El evento no contiene un refund valido")
    refund_id = objeto.get("id")
    if not isinstance(refund_id, str) or not refund_id.strip():
        raise HTTPException(400, "El evento no contiene un refund valido")
    amount = _entero_positivo(objeto.get("amount"))
    currency = str(objeto.get("currency") or "").upper()
    if len(currency) != 3:
        raise HTTPException(400, "Moneda del reembolso invalida")
    status = str(objeto.get("status") or "").strip()
    if not status:
        raise HTTPException(400, "Estado del reembolso invalido")
    payment_intent_id = objeto.get("payment_intent")
    if payment_intent_id is not None and not isinstance(payment_intent_id, str):
        raise HTTPException(400, "PaymentIntent del reembolso invalido")

    registro = repo.registrar_huerfano(
        db,
        stripe_event_id=evento_id,
        event_type=tipo,
        refund_id=refund_id,
        payment_intent_id=payment_intent_id,
        amount=amount,
        currency=currency,
        status=status,
        evento=evento,
        motivo=(
            "Refund verificado sin metadata de ajuste/tenant; "
            "requiere conciliacion manual"
        ),
    )
    if registro.estado == "PENDIENTE" and registro.ajuste_id is None:
        registrar_bitacora(
            db=db,
            usuario_id=None,
            accion="STRIPE_WEBHOOK_HUERFANO_REGISTRADO",
            entidad_afectada="stripe_webhook_huerfano",
            id_registro_afectado=registro.id,
            descripcion=(
                f"Refund {refund_id} registrado para conciliacion manual "
                f"({amount} {currency}, {status})"
            ),
        )
    db.commit()
    return registro


def listar_huerfanos(db: Session, usuario) -> list[WebhookHuerfanoRespuesta]:
    _admin(usuario)
    return [_respuesta(item) for item in repo.listar_pendientes(db)]


def _validar_refund_proveedor(
    huerfano,
    pago,
    reembolso,
    *,
    importe_esperado: Decimal,
) -> None:
    from app.modules.gestion_pagos.services import service as pagos_service

    if reembolso.id != huerfano.refund_id:
        raise HTTPException(409, "El refund no coincide con el evento registrado")
    if not reembolso.confirmed:
        raise HTTPException(
            409,
            f"El proveedor mantiene el reembolso en {reembolso.status}; "
            "no se puede confirmar",
        )
    if int(reembolso.amount or 0) != pagos_service.monto_a_unidad_minima(
        importe_esperado,
    ):
        raise HTTPException(409, "El importe del refund no coincide con el ajuste")
    if str(reembolso.currency or "").upper() != huerfano.currency:
        raise HTTPException(409, "La moneda del refund no coincide con el evento")
    if not huerfano.payment_intent_id or not reembolso.payment_intent_id:
        raise HTTPException(409, "El refund no identifica su PaymentIntent")
    if reembolso.payment_intent_id != huerfano.payment_intent_id:
        raise HTTPException(409, "El PaymentIntent del refund no coincide")
    if (
        pago.referencia_transaccion
        and reembolso.payment_intent_id != pago.referencia_transaccion
    ):
        raise HTTPException(409, "El refund no pertenece al pago conciliado")
    metadata = reembolso.metadata or {}
    if metadata.get("pago_id") not in (None, str(pago.id)):
        raise HTTPException(409, "La metadata del refund apunta a otro pago")
    if metadata.get("ajuste_id") is not None:
        # Si trae ajuste, no es huerfano: debe conciliarse por el flujo normal.
        raise HTTPException(409, "El refund no es huerfano: trae ajuste_id")


def atribuir_reembolso_huerfano(
    db: Session,
    huerfano_id: int,
    datos: AtribuirReembolsoHuerfano,
    usuario,
    ip: str | None = None,
    proveedor=None,
):
    """E-3: vincula un refund huerfano a un pago con evidencia y refund_id.

    La llamada al proveedor se ejecuta sin locks abiertos: se lee el evento,
    se cierra la transaccion, se consulta Stripe y solo despues se reabre para
    bloquear pago/evento y persistir el ajuste (E-4).
    """
    _admin(usuario)
    if proveedor is None:
        raise HTTPException(500, "Proveedor de pagos no disponible")
    huerfano = repo.obtener_huerfano(db, huerfano_id)
    if huerfano is None:
        raise HTTPException(404, "Reembolso huerfano no encontrado")
    if huerfano.estado == "DESCARTADO":
        raise HTTPException(409, "El reembolso huerfano fue descartado")
    if huerfano.estado == "ATRIBUIDO":
        if huerfano.ajuste_id is not None:
            db.rollback()
            return ajustes_service._respuesta_ajuste(
                ajustes_repo.obtener_ajuste(db, huerfano.ajuste_id)
            )
        raise HTTPException(409, "El reembolso huerfano ya fue atribuido")

    if not huerfano.payment_intent_id:
        raise HTTPException(409, "El evento huerfano no identifica PaymentIntent")
    from app.modules.gestion_pagos.repositories import repository as pago_repo

    pago = pago_repo.obtener_pago_por_referencia_transaccion(
        db, huerfano.payment_intent_id,
    )
    if pago is None:
        raise HTTPException(404, "Pago asociado al PaymentIntent no encontrado")
    if pago.pasarela != "STRIPE":
        raise HTTPException(409, "El pago no corresponde a Stripe")

    importe = sum(
        (ajustes_service._decimal(item.monto) for item in datos.servicios),
        Decimal("0.00"),
    )
    if importe >= 0:
        raise HTTPException(409, "La distribucion del reembolso debe ser negativa")
    esperado = abs(importe)
    from app.modules.gestion_pagos.services import service as pagos_service

    if huerfano.amount != pagos_service.monto_a_unidad_minima(esperado):
        raise HTTPException(
            409,
            "La distribucion por servicio no coincide con el importe del refund",
        )
    if huerfano.currency != pago.moneda.upper():
        raise HTTPException(409, "La moneda del refund no coincide con el pago")

    existente = repo.obtener_ajuste_por_refund(db, huerfano.refund_id)
    if existente is not None:
        db.rollback()
        return ajustes_service._respuesta_ajuste(existente)

    # Sin locks abiertos durante la llamada externa.
    db.commit()
    try:
        proveedor.validar_configuracion()
        reembolso = proveedor.recuperar_reembolso(huerfano.refund_id)
    except Exception as error:  # noqa: BLE001 - se traduce abajo
        from app.modules.gestion_pagos.providers import (
            ErrorProveedorPagoDesconocido, ProveedorNoConfigurado,
        )

        if isinstance(error, ProveedorNoConfigurado):
            raise HTTPException(500, str(error)) from error
        if isinstance(error, ErrorProveedorPagoDesconocido):
            raise HTTPException(
                502,
                "Resultado desconocido al consultar el refund; no se confirma",
            ) from error
        raise
    _validar_refund_proveedor(
        huerfano, pago, reembolso, importe_esperado=esperado,
    )

    try:
        huerfano = repo.obtener_huerfano(db, huerfano_id, bloquear=True)
        if huerfano is None:
            raise HTTPException(404, "Reembolso huerfano no encontrado")
        if huerfano.estado == "ATRIBUIDO":
            db.rollback()
            return ajustes_service._respuesta_ajuste(
                ajustes_repo.obtener_ajuste(db, huerfano.ajuste_id)
            )
        if huerfano.estado == "DESCARTADO":
            raise HTTPException(409, "El reembolso huerfano fue descartado")

        cuota, intento, pago = ajustes_service._bloquear_pago(db, pago.id)
        if pago.estado != "APROBADO":
            raise HTTPException(
                409,
                "Solo un pago APROBADO con dinero capturado puede conciliarse "
                f"(estado: {pago.estado})",
            )
        vigente = saldos.monto_vigente_pago(db, pago)
        if esperado > vigente:
            raise HTTPException(
                409, "El reembolso supera el importe vigente del pago",
            )
        if cuota is not None and esperado < vigente:
            raise HTTPException(
                409,
                "Un pago aplicado a una cuota no admite devolucion parcial: "
                "el modelo de cuota no representa cobertura parcial (D4)",
            )
        filas = ajustes_service._filas_detalle(pago, importe, datos.servicios)
        ajustes_service._validar_topes(db, pago, filas)
        if saldos.pago_tiene_ajuste_abierto(db, pago.id):
            raise HTTPException(
                409, "Existe otro ajuste abierto; concilielo antes de continuar",
            )

        existente = repo.obtener_ajuste_por_refund(db, huerfano.refund_id)
        if existente is not None:
            repo.marcar_atribuido(
                db, huerfano, ajuste_id=existente.id,
                motivo="Reintento idempotente de atribucion",
            )
            db.commit()
            return ajustes_service._respuesta_ajuste(
                ajustes_repo.obtener_ajuste(db, existente.id)
            )

        ajuste = ajustes_service._registrar(
            db,
            pago=pago,
            tipo="REEMBOLSO",
            naturaleza=datos.naturaleza,
            efecto_caja="DEVOLUCION_STRIPE",
            estado="PENDIENTE_PROVEEDOR",
            importe=importe,
            motivo=datos.motivo,
            evidencia=datos.evidencia,
            clave_idempotencia=f"cu36-huerfano-{huerfano.id}",
            usuario=usuario,
            filas=filas,
            revertir_cuota=bool(
                intento and intento.aplicado_a_cuota and esperado >= vigente
            ),
            referencia_externa=huerfano.refund_id,
        )
        ajuste = ajustes_service.aplicar_reembolso_proveedor(
            db, ajuste, reembolso, usuario_id=usuario.id,
        )
        if ajuste.estado != "CONFIRMADO":
            raise HTTPException(
                409, f"El reembolso quedo en {ajuste.estado}; no se confirma",
            )
        marcados = repo.marcar_atribuidos_por_refund(
            db, huerfano.refund_id, ajuste_id=ajuste.id,
            motivo=(
                f"Atribuido manualmente por administrador {usuario.id}: "
                f"{datos.motivo}"
            ),
        )
        registrar_bitacora(
            db=db,
            usuario_id=usuario.id,
            ip=ip,
            accion="STRIPE_REFUND_HUERFANO_ATRIBUIDO",
            entidad_afectada="pago_ajuste",
            id_registro_afectado=ajuste.id,
            descripcion=(
                f"Refund {huerfano.refund_id} conciliado manualmente sobre "
                f"pago {pago.id}; eventos marcados: {marcados}"
            ),
        )
        db.commit()
        return ajustes_service._respuesta_ajuste(
            ajustes_repo.obtener_ajuste(db, ajuste.id)
        )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "El refund ya fue vinculado a otro ajuste",
        ) from exc
    except Exception:
        db.rollback()
        raise


def registrar_evento_huerfano_central(db: Session, evento: dict):
    """I-2: persiste el refund en la cuarentena central del Control Plane.

    Se usa cuando el PaymentIntent no demuestra a que tenant pertenece el
    refund. La escritura es durable y ocurre antes de responder 2xx; si falla,
    la excepcion propaga 5xx y Stripe reintenta. Nunca se escribe en la base
    legacy.
    """
    evento_id = evento.get("id")
    if not isinstance(evento_id, str) or not evento_id.strip():
        raise HTTPException(400, "El evento Stripe no tiene identificador")
    tipo = evento.get("type")
    if not isinstance(tipo, str) or not tipo.strip():
        raise HTTPException(400, "El evento Stripe no tiene tipo")
    objeto = evento.get("data", {}).get("object", {})
    if not isinstance(objeto, dict):
        raise HTTPException(400, "El evento no contiene un refund valido")
    refund_id = objeto.get("id")
    if not isinstance(refund_id, str) or not refund_id.strip():
        raise HTTPException(400, "El evento no contiene un refund valido")
    amount = _entero_positivo(objeto.get("amount"))
    currency = str(objeto.get("currency") or "").upper()
    if len(currency) != 3:
        raise HTTPException(400, "Moneda del reembolso invalida")
    status = str(objeto.get("status") or "").strip()
    if not status:
        raise HTTPException(400, "Estado del reembolso invalido")
    payment_intent_id = objeto.get("payment_intent")
    if payment_intent_id is not None and not isinstance(payment_intent_id, str):
        raise HTTPException(400, "PaymentIntent del reembolso invalido")

    registro = repo.registrar_huerfano_central(
        db,
        stripe_event_id=evento_id,
        event_type=tipo,
        refund_id=refund_id,
        payment_intent_id=payment_intent_id,
        amount=amount,
        currency=currency,
        status=status,
        evento=evento,
        motivo=(
            "Refund verificado sin tenant resoluble; cuarentena central, "
            "requiere atribucion manual explicita"
        ),
    )
    if registro.estado == "PENDIENTE" and registro.empresa_id is None:
        from app.modules.administracion_saas.models import SaasBitacora

        db.add(SaasBitacora(
            saas_usuario_id=None,
            accion="STRIPE_WEBHOOK_HUERFANO_CENTRAL_REGISTRADO",
            entidad_afectada="stripe_webhook_huerfano",
            id_registro_afectado=registro.id,
            descripcion=(
                f"Refund {refund_id} sin tenant resoluble registrado en "
                f"cuarentena central ({amount} {currency}, {status})"
            ),
            resultado="EXITO",
        ))
    db.commit()
    return registro


def listar_huerfanos_central(db: Session) -> list[WebhookHuerfanoCentralRespuesta]:
    return [_respuesta_central(item) for item in repo.listar_pendientes_central(db)]


def _payload_central(huerfano) -> dict:
    try:
        payload = json.loads(huerfano.payload or "")
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        return payload
    return {"id": huerfano.stripe_event_id, "type": huerfano.event_type}


def atribuir_huerfano_central(
    db: Session,
    huerfano_id: int,
    datos: AtribuirHuerfanoCentral,
    usuario,
    registry,
):
    """I-2: atribucion manual de un refund central a una empresa concreta.

    Es deliberadamente explicita: el SUPERADMIN indica la empresa y el sistema
    valida que el PaymentIntent exista alli, que sea Stripe y que el importe no
    supere el importe vigente. No mueve dinero: copia el evento a la cuarentena
    del tenant para que su administrador complete el ajuste CU36 auditado.
    """
    from app.core.tenancy.repository import TenantControlPlaneRepository
    from app.core.tenancy.resolver import TenantResolver

    huerfano = repo.obtener_huerfano_central(db, huerfano_id, bloquear=True)
    if huerfano is None:
        raise HTTPException(404, "Reembolso huerfano central no encontrado")
    if huerfano.estado == "DESCARTADO":
        raise HTTPException(409, "El reembolso huerfano fue descartado")
    if huerfano.estado == "ATRIBUIDO":
        db.rollback()
        actual = repo.obtener_huerfano_central(db, huerfano_id)
        if actual is None:
            raise HTTPException(404, "Reembolso huerfano central no encontrado")
        return _respuesta_central(actual)
    if not huerfano.payment_intent_id:
        raise HTTPException(409, "El evento huerfano no identifica PaymentIntent")
    if registry is None:
        raise HTTPException(500, "Registro de tenants no disponible")

    resolver = TenantResolver(TenantControlPlaneRepository(db))
    try:
        context = resolver.resolver_por_codigo_empresa(datos.empresa_codigo)
        resolver.require_active_database(context)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            409,
            "La empresa indicada no esta activa o no tiene base disponible",
        ) from exc

    from app.modules.gestion_pagos.repositories import repository as pago_repo
    from app.modules.gestion_pagos.services import service as pagos_service

    tenant_db = registry.get_session(context)
    try:
        tenant_db.info["tenant_context"] = context
        pago = pago_repo.obtener_pago_por_referencia_transaccion(
            tenant_db, huerfano.payment_intent_id,
        )
        if pago is None:
            raise HTTPException(
                409,
                "El PaymentIntent no existe en la empresa indicada; "
                "atribucion ambigua rechazada",
            )
        if pago.pasarela != "STRIPE":
            raise HTTPException(409, "El pago no corresponde a Stripe")
        if pago.moneda.upper() != huerfano.currency:
            raise HTTPException(409, "La moneda del refund no coincide con el pago")
        vigente = saldos.monto_vigente_pago(tenant_db, pago)
        if huerfano.amount > pagos_service.monto_a_unidad_minima(vigente):
            raise HTTPException(
                409, "El importe del refund supera el importe vigente del pago",
            )
        repo.registrar_huerfano(
            tenant_db,
            stripe_event_id=huerfano.stripe_event_id,
            event_type=huerfano.event_type,
            refund_id=huerfano.refund_id,
            payment_intent_id=huerfano.payment_intent_id,
            amount=huerfano.amount,
            currency=huerfano.currency,
            status=huerfano.status,
            evento=_payload_central(huerfano),
            motivo=(
                f"Atribuido desde cuarentena central por SUPERADMIN "
                f"{usuario.id}: {datos.motivo} | Evidencia: {datos.evidencia}"
            ),
        )
        tenant_db.commit()
    except HTTPException:
        tenant_db.rollback()
        raise
    except Exception:
        tenant_db.rollback()
        raise
    finally:
        tenant_db.close()

    repo.marcar_atribuido_central(
        db,
        huerfano,
        empresa_id=context.empresa_id,
        tenant_database_id=context.tenant_database_id,
        motivo=datos.motivo,
        usuario_id=usuario.id,
    )
    from app.modules.administracion_saas.models import SaasBitacora

    db.add(SaasBitacora(
        saas_usuario_id=usuario.id,
        accion="STRIPE_REFUND_HUERFANO_ATRIBUIDO_TENANT",
        entidad_afectada="stripe_webhook_huerfano",
        id_registro_afectado=huerfano.id,
        descripcion=(
            f"Refund {huerfano.refund_id} atribuido a {context.empresa_codigo} "
            f"para conciliacion en el tenant; evidencia: {datos.evidencia}"
        ),
        resultado="EXITO",
    ))
    db.commit()
    return _respuesta_central(repo.obtener_huerfano_central(db, huerfano_id))


__all__ = [
    "atribuir_huerfano_central",
    "atribuir_reembolso_huerfano",
    "listar_huerfanos",
    "listar_huerfanos_central",
    "registrar_evento_huerfano",
    "registrar_evento_huerfano_central",
]
