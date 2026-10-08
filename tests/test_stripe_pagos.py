import hashlib
import hmac
import json
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text

from app.core import config
from app.main import app
from app.modules.gestion_pagos.models.models import Pago, PagoDetalle
from app.modules.gestion_pagos.providers import (
    ESTADOS_INTENCION_REUTILIZABLES,
    ErrorFirmaWebhook,
    ErrorProveedorPago,
    IntentoPagoProveedor,
    ProveedorNoConfigurado,
    ProveedorPagoBase,
    StripeProveedor,
    obtener_proveedor_stripe,
)
from app.modules.gestion_pagos.services.service import monto_a_unidad_minima
from app.modules.gestion_pagos.providers import stripe_provider as stripe_adapter
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from tests.test_base_flujo_pagos import cliente_pagos, db_pagos, token


class ProveedorStripeFalso(ProveedorPagoBase):
    def __init__(self):
        self.intenciones = {}
        self.creaciones = []
        self.recuperaciones = []
        self.cancelaciones = []
        self.evento = None
        self.configurado = True
        self.webhook_configurado = True
        self.firma_valida = True
        self.fallar_creacion = False

    def validar_configuracion(self, *, webhook=False):
        disponible = self.webhook_configurado if webhook else self.configurado
        if not disponible:
            raise ProveedorNoConfigurado("Stripe no esta configurado")

    def crear_intencion(
        self,
        *,
        amount,
        currency,
        metadata,
        idempotency_key,
    ):
        self.creaciones.append(
            {
                "amount": amount,
                "currency": currency,
                "metadata": metadata,
                "idempotency_key": idempotency_key,
            }
        )
        if self.fallar_creacion:
            raise ErrorProveedorPago("Stripe temporalmente no disponible")
        pago_id = metadata["pago_id"]
        intento = IntentoPagoProveedor(
            id=f"pi_test_{pago_id}",
            client_secret=f"pi_test_{pago_id}_secret_prueba",
            amount=amount,
            currency=currency,
            status="requires_payment_method",
        )
        self.intenciones[intento.id] = intento
        return intento

    def recuperar_intencion(self, payment_intent_id):
        self.recuperaciones.append(payment_intent_id)
        return self.intenciones[payment_intent_id]

    def cancelar_intencion(self, payment_intent_id):
        self.cancelaciones.append(payment_intent_id)
        actual = self.intenciones[payment_intent_id]
        cancelado = IntentoPagoProveedor(
            id=actual.id,
            client_secret=actual.client_secret,
            amount=actual.amount,
            currency=actual.currency,
            status="canceled",
        )
        self.intenciones[payment_intent_id] = cancelado
        return cancelado

    def verificar_webhook(self, payload, firma):
        if not self.firma_valida:
            raise ErrorFirmaWebhook("Firma webhook Stripe invalida")
        return self.evento


@pytest.fixture
def proveedor_falso():
    return ProveedorStripeFalso()


@pytest.fixture
def cliente_stripe(cliente_pagos, proveedor_falso):
    app.dependency_overrides[obtener_proveedor_stripe] = lambda: proveedor_falso
    yield cliente_pagos


def limpiar_pagos(db_pagos):
    db_pagos.execute(delete(PagoDetalle))
    db_pagos.execute(delete(Pago))
    db_pagos.commit()


def insertar_pago_stripe(
    db,
    *,
    pago_id=600,
    paciente_servicio_id=1000,
    estado="PENDIENTE",
    referencia="pi_webhook_600",
    monto="10.10",
):
    db.execute(
        text(
            "INSERT INTO pago "
            "(id,fecha_creacion,fecha_hora_pago,monto,moneda,metodo_pago,"
            "estado,pasarela,referencia_transaccion,observaciones) "
            "VALUES (:id,'2026-10-01',NULL,:monto,'BOB','TARJETA',"
            ":estado,'STRIPE',:referencia,NULL)"
        ),
        {
            "id": pago_id,
            "monto": monto,
            "estado": estado,
            "referencia": referencia,
        },
    )
    db.execute(
        text(
            "INSERT INTO pago_detalle "
            "(id,pago_id,servicio_realizado_id,monto_aplicado) "
            "VALUES (:id,:pago_id,:servicio_id,:monto)"
        ),
        {
            "id": pago_id,
            "pago_id": pago_id,
            "servicio_id": paciente_servicio_id,
            "monto": monto,
        },
    )
    db.commit()


def evento_stripe(tipo, *, intento="pi_webhook_600", amount=1010, currency="bob"):
    return {
        "id": "evt_prueba",
        "type": tipo,
        "created": 1790856000,
        "data": {
            "object": {
                "id": intento,
                "amount": amount,
                "currency": currency,
            }
        },
    }


def registrar_intento_falso(
    proveedor,
    *,
    intento_id="pi_webhook_600",
    status="requires_payment_method",
    amount=1010,
):
    proveedor.intenciones[intento_id] = IntentoPagoProveedor(
        id=intento_id,
        client_secret=f"{intento_id}_secret_reutilizable",
        amount=amount,
        currency="bob",
        status=status,
    )


def test_api_general_inicia_sin_configuracion_stripe(cliente_pagos, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", None)
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", None)
    assert cliente_pagos.get("/").status_code == 200


def test_endpoint_intencion_falla_limpio_sin_secret(
    cliente_pagos,
    monkeypatch,
):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", None)
    respuesta = cliente_pagos.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000]},
        headers=token(11),
    )
    assert respuesta.status_code == 500
    assert "sk_test" not in respuesta.text


def test_webhook_falla_limpio_sin_secret(cliente_pagos, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", None)
    respuesta = cliente_pagos.post(
        "/pagos/stripe/webhook",
        content=b"{}",
        headers={"Stripe-Signature": "firma"},
    )
    assert respuesta.status_code == 500
    assert "whsec" not in respuesta.text


@pytest.mark.parametrize(
    ("monto", "esperado"),
    [
        (Decimal("150.00"), 15000),
        (Decimal("80.50"), 8050),
        (Decimal("330.00"), 33000),
    ],
)
def test_conversion_decimal_a_unidad_minima(monto, esperado):
    assert monto_a_unidad_minima(monto) == esperado


def test_adaptador_stripe_usa_sdk_oficial_sin_red(monkeypatch):
    capturado = {}

    def crear(**parametros):
        capturado.update(parametros)
        return SimpleNamespace(
            id="pi_sdk",
            client_secret="pi_sdk_secret_prueba",
            amount=8050,
            currency="bob",
            status="requires_payment_method",
        )

    monkeypatch.setattr(stripe_adapter.stripe.PaymentIntent, "create", crear)
    proveedor = StripeProveedor(secret_key="sk_test_falsa")
    intento = proveedor.crear_intencion(
        amount=8050,
        currency="bob",
        metadata={"pago_id": "1"},
        idempotency_key="clinica-pago-1",
    )
    assert intento.id == "pi_sdk"
    assert capturado["automatic_payment_methods"] == {"enabled": True}
    assert capturado["idempotency_key"] == "clinica-pago-1"


def test_adaptador_verifica_payload_raw_con_sdk(monkeypatch):
    capturado = {}

    class EventoFalso:
        def to_dict(self):
            return {"type": "evento.prueba"}

    def construir(payload, firma, secreto):
        capturado.update(payload=payload, firma=firma, secreto=secreto)
        return EventoFalso()

    monkeypatch.setattr(stripe_adapter.stripe.Webhook, "construct_event", construir)
    proveedor = StripeProveedor(webhook_secret="whsec_falso")
    evento = proveedor.verificar_webhook(b"payload-raw", "firma")
    assert evento == {"type": "evento.prueba"}
    assert capturado == {
        "payload": b"payload-raw",
        "firma": "firma",
        "secreto": "whsec_falso",
    }


def test_crear_intencion_calcula_y_persiste_snapshot(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000, 1001]},
        headers=token(11),
    )
    assert respuesta.status_code == 201, respuesta.text
    datos = respuesta.json()
    assert Decimal(datos["monto"]) == Decimal("30.30")
    assert datos["moneda"] == "BOB"
    assert datos["estado_pago"] == "PENDIENTE"
    assert datos["payment_intent_id"].startswith("pi_test_")
    assert datos["client_secret"].endswith("_secret_prueba")

    pago = db_pagos.get(Pago, datos["pago_id"])
    detalles = db_pagos.scalars(
        select(PagoDetalle)
        .where(PagoDetalle.pago_id == pago.id)
        .order_by(PagoDetalle.servicio_realizado_id)
    ).all()
    assert pago.monto == Decimal("30.30")
    assert pago.metodo_pago == "TARJETA"
    assert pago.pasarela == "STRIPE"
    assert pago.estado == "PENDIENTE"
    assert pago.fecha_hora_pago is None
    assert pago.referencia_transaccion == datos["payment_intent_id"]
    assert [detalle.monto_aplicado for detalle in detalles] == [
        Decimal("10.10"),
        Decimal("20.20"),
    ]
    assert proveedor_falso.creaciones[0]["amount"] == 3030
    assert proveedor_falso.creaciones[0]["currency"] == "bob"
    assert proveedor_falso.creaciones[0]["metadata"] == {
        "pago_id": str(pago.id),
        "consulta_clinica_id": "100",
    }
    assert proveedor_falso.creaciones[0]["idempotency_key"] == (
        f"clinica-pago-{pago.id}"
    )
    assert db_pagos.scalar(
        select(Bitacora).where(Bitacora.accion == "INICIAR_PAGO_STRIPE")
    ) is not None


@pytest.mark.parametrize("campo", ["monto", "paciente_id", "estado"])
def test_request_no_acepta_campos_controlados(cliente_stripe, campo):
    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000], campo: "1"},
        headers=token(11),
    )
    assert respuesta.status_code == 422


def test_dos_requests_simulados_reutilizan_pago_e_intencion(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    cuerpo = {"servicio_realizado_ids": [1000, 1001]}
    primera = cliente_stripe.post(
        "/pagos/stripe/intencion", json=cuerpo, headers=token(11),
    )
    segunda = cliente_stripe.post(
        "/pagos/stripe/intencion", json=cuerpo, headers=token(11),
    )
    assert primera.status_code == segunda.status_code == 201
    assert primera.json()["pago_id"] == segunda.json()["pago_id"]
    assert len(proveedor_falso.creaciones) == 1
    assert proveedor_falso.recuperaciones == [
        primera.json()["payment_intent_id"]
    ]
    assert len(db_pagos.scalars(select(Pago)).all()) == 1


def test_estados_reutilizables_estan_centralizados():
    assert ESTADOS_INTENCION_REUTILIZABLES == {
        "requires_payment_method",
        "requires_confirmation",
        "requires_action",
        "requires_capture",
        "processing",
    }


@pytest.mark.parametrize(
    "estado_intento",
    ["requires_payment_method", "requires_action"],
)
def test_rechazado_con_intento_activo_reutiliza_pago_e_intent(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
    estado_intento,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos, estado="RECHAZADO")
    registrar_intento_falso(proveedor_falso, status=estado_intento)

    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000]},
        headers=token(11),
    )
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["pago_id"] == 600
    assert respuesta.json()["payment_intent_id"] == "pi_webhook_600"
    assert proveedor_falso.creaciones == []
    assert proveedor_falso.recuperaciones == ["pi_webhook_600"]
    pago = db_pagos.get(Pago, 600)
    db_pagos.refresh(pago)
    assert pago.estado == "PENDIENTE"
    assert len(db_pagos.scalars(select(Pago)).all()) == 1


def test_rechazado_con_intento_cancelado_crea_un_intento_nuevo(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos, estado="RECHAZADO")
    registrar_intento_falso(proveedor_falso, status="canceled")

    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000]},
        headers=token(11),
    )
    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["pago_id"] != 600
    assert len(proveedor_falso.creaciones) == 1
    anterior = db_pagos.get(Pago, 600)
    db_pagos.refresh(anterior)
    assert anterior.estado == "ANULADO"
    assert len(db_pagos.scalars(select(Pago)).all()) == 2


def test_rechazado_con_intento_succeeded_bloquea_nuevo_cobro(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos, estado="RECHAZADO")
    registrar_intento_falso(proveedor_falso, status="succeeded")

    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000]},
        headers=token(11),
    )
    assert respuesta.status_code == 409
    assert proveedor_falso.creaciones == []
    assert len(db_pagos.scalars(select(Pago)).all()) == 1
    evidencia = db_pagos.scalar(
        select(Bitacora).where(
            Bitacora.accion == "PAGO_STRIPE_SUCCEEDED_PENDIENTE_WEBHOOK"
        )
    )
    assert evidencia is not None


def test_rechazado_activo_con_solapamiento_parcial_devuelve_409(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos, estado="RECHAZADO")
    registrar_intento_falso(proveedor_falso, status="requires_payment_method")

    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000, 1001]},
        headers=token(11),
    )
    assert respuesta.status_code == 409
    assert proveedor_falso.creaciones == []
    assert len(db_pagos.scalars(select(Pago)).all()) == 1


def test_fallo_externo_deja_pago_recuperable_con_misma_idempotencia(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    proveedor_falso.fallar_creacion = True
    primera = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000, 1001]},
        headers=token(11),
    )
    assert primera.status_code == 502
    pago = db_pagos.scalar(select(Pago))
    assert pago.estado == "PENDIENTE"
    assert pago.referencia_transaccion is None

    proveedor_falso.fallar_creacion = False
    segunda = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000, 1001]},
        headers=token(11),
    )
    assert segunda.status_code == 201
    assert segunda.json()["pago_id"] == pago.id
    assert [item["idempotency_key"] for item in proveedor_falso.creaciones] == [
        f"clinica-pago-{pago.id}",
        f"clinica-pago-{pago.id}",
    ]
    assert len(db_pagos.scalars(select(Pago)).all()) == 1


def test_solapamiento_parcial_pendiente_devuelve_conflicto(
    cliente_stripe,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    primera = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000, 1001]},
        headers=token(11),
    )
    assert primera.status_code == 201
    segunda = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000]},
        headers=token(11),
    )
    assert segunda.status_code == 409


@pytest.mark.parametrize(
    ("ids", "esperado"),
    [([1001], 409), ([1002], 403), ([1000, 1002], 422), ([1000, 1000], 422),
     ([1004], 422), ([1005], 422), ([1003], 422)],
)
def test_intencion_rechaza_selecciones_no_pagables(
    cliente_stripe,
    ids,
    esperado,
):
    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": ids},
        headers=token(11),
    )
    assert respuesta.status_code == esperado


def test_webhook_rechaza_firma_invalida(cliente_stripe, proveedor_falso):
    proveedor_falso.firma_valida = False
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"sin-parsear",
        headers={"Stripe-Signature": "invalida"},
    )
    assert respuesta.status_code == 400


@pytest.mark.parametrize(
    ("tipo", "esperado"),
    [
        ("payment_intent.succeeded", "APROBADO"),
        ("payment_intent.payment_failed", "RECHAZADO"),
        ("payment_intent.processing", "PENDIENTE"),
        ("payment_intent.canceled", "ANULADO"),
    ],
)
def test_webhook_transiciones(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
    tipo,
    esperado,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos)
    proveedor_falso.evento = evento_stripe(tipo)
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 200, respuesta.text
    pago = db_pagos.get(Pago, 600)
    db_pagos.refresh(pago)
    assert pago.estado == esperado
    assert (pago.fecha_hora_pago is not None) is (esperado == "APROBADO")


def test_webhook_evento_desconocido_no_modifica(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    proveedor_falso.evento = evento_stripe("charge.succeeded")
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["procesado"] is False
    assert db_pagos.get(Pago, 500).estado == "APROBADO"


def test_webhook_permite_rechazado_a_aprobado_del_mismo_intento(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos, estado="RECHAZADO")
    proveedor_falso.evento = evento_stripe("payment_intent.succeeded")
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 200
    pago = db_pagos.get(Pago, 600)
    db_pagos.refresh(pago)
    assert pago.estado == "APROBADO"
    assert pago.fecha_hora_pago is not None


def test_webhook_tardio_detecta_otro_pago_aprobado_y_no_aprueba(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(
        db_pagos,
        pago_id=600,
        estado="APROBADO",
        referencia="pi_primero",
    )
    insertar_pago_stripe(
        db_pagos,
        pago_id=601,
        estado="RECHAZADO",
        referencia="pi_segundo",
    )
    proveedor_falso.evento = evento_stripe(
        "payment_intent.succeeded",
        intento="pi_segundo",
    )
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 409
    segundo = db_pagos.get(Pago, 601)
    db_pagos.refresh(segundo)
    assert segundo.estado == "RECHAZADO"
    evidencia = db_pagos.scalar(
        select(Bitacora).where(
            Bitacora.accion == "CONFLICTO_DOBLE_COBRO_STRIPE"
        )
    )
    assert evidencia is not None


@pytest.mark.parametrize(
    "evento_posterior",
    [
        "payment_intent.succeeded",
        "payment_intent.processing",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    ],
)
def test_webhook_idempotente_no_degrada_aprobado(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
    evento_posterior,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos, estado="APROBADO")
    proveedor_falso.evento = evento_stripe(evento_posterior)
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 200
    pago = db_pagos.get(Pago, 600)
    db_pagos.refresh(pago)
    assert pago.estado == "APROBADO"


@pytest.mark.parametrize(
    ("amount", "currency"),
    [(999, "bob"), (1010, "usd")],
)
def test_webhook_inconsistente_no_aprueba(
    cliente_stripe,
    proveedor_falso,
    db_pagos,
    amount,
    currency,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos)
    proveedor_falso.evento = evento_stripe(
        "payment_intent.succeeded", amount=amount, currency=currency,
    )
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 409
    pago = db_pagos.get(Pago, 600)
    db_pagos.refresh(pago)
    assert pago.estado == "PENDIENTE"


def test_webhook_payment_intent_desconocido_controlado(
    cliente_stripe,
    proveedor_falso,
):
    proveedor_falso.evento = evento_stripe(
        "payment_intent.succeeded", intento="pi_desconocido",
    )
    respuesta = cliente_stripe.post(
        "/pagos/stripe/webhook",
        content=b"raw",
        headers={"Stripe-Signature": "valida"},
    )
    assert respuesta.status_code == 404


def test_estado_pago_solo_propietario_y_sin_client_secret(
    cliente_stripe,
    db_pagos,
):
    limpiar_pagos(db_pagos)
    insertar_pago_stripe(db_pagos)
    propia = cliente_stripe.get(
        "/pagos/stripe/pagos/600/estado",
        headers=token(11),
    )
    assert propia.status_code == 200
    assert propia.json() == {
        "pago_id": 600,
        "consulta_clinica_id": 100,
        "estado_pago": "PENDIENTE",
        "monto": "10.10",
        "moneda": "BOB",
        "payment_intent_id": "pi_webhook_600",
    }
    assert "client_secret" not in propia.json()
    ajena = cliente_stripe.get(
        "/pagos/stripe/pagos/600/estado",
        headers=token(12),
    )
    assert ajena.status_code == 403
    assert cliente_stripe.get(
        "/pagos/stripe/pagos/600/estado",
    ).status_code == 401


def test_crear_intencion_requiere_autenticacion(cliente_stripe):
    respuesta = cliente_stripe.post(
        "/pagos/stripe/intencion",
        json={"servicio_realizado_ids": [1000]},
    )
    assert respuesta.status_code == 401


def test_openapi_stripe_sin_colisiones():
    paths = app.openapi()["paths"]
    assert set(paths["/pagos/stripe/intencion"]) == {"post"}
    assert set(paths["/pagos/stripe/webhook"]) == {"post"}
    assert set(paths["/pagos/stripe/pagos/{pago_id}/estado"]) == {"get"}


# ---------------------------------------------------------------------------
# Verificacion con el SDK REAL de Stripe de un evento firmado localmente.
# No hay conexion a Stripe ni mocks de metodos del SDK: se usa HMAC-SHA256
# para firmar el payload y el propio SDK para verificarlo y convertirlo.
# Estas pruebas blindan la regresion causada por evento.to_dict_recursive().
# ---------------------------------------------------------------------------


def firmar_payload(payload: bytes, secreto: str, timestamp: int) -> str:
    firmado = b"%d.%s" % (timestamp, payload)
    firma = hmac.new(
        secreto.encode("utf-8"), firmado, hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={firma}"


def evento_sdk_real() -> dict:
    return {
        "id": "evt_firmado_local",
        "object": "event",
        "created": 1791416221,
        "livemode": False,
        "pending_webhooks": 1,
        "type": "payment_intent.succeeded",
        "data": {
            "object": {
                "id": "pi_firmado_local",
                "object": "payment_intent",
                "amount": 21000,
                "amount_received": 21000,
                "currency": "bob",
                "status": "succeeded",
                "livemode": False,
                "metadata": {"pago_id": "1", "consulta_clinica_id": "3"},
            }
        },
    }


def test_adaptador_verifica_evento_firmado_con_sdk_real_sin_red():
    secreto = "whsec_prueba_local_etapa6"
    evento = evento_sdk_real()
    payload = json.dumps(evento, separators=(",", ":")).encode("utf-8")
    cabecera = firmar_payload(payload, secreto, int(time.time()))

    proveedor = StripeProveedor(webhook_secret=secreto)
    convertido = proveedor.verificar_webhook(payload, cabecera)

    assert isinstance(convertido, dict)
    assert convertido["id"] == "evt_firmado_local"
    assert convertido["type"] == "payment_intent.succeeded"
    assert convertido["livemode"] is False
    objeto = convertido["data"]["object"]
    assert objeto["id"] == "pi_firmado_local"
    assert objeto["status"] == "succeeded"
    assert objeto["amount"] == 21000
    assert objeto["amount_received"] == 21000
    assert objeto["currency"] == "bob"
    assert objeto["metadata"]["pago_id"] == "1"

    # El adaptador no debe depender de metodos inexistentes en el SDK real
    # (la causa exacta del 500 en produccion fue to_dict_recursive()).
    fuente = Path(stripe_adapter.__file__).read_text(encoding="utf-8")
    assert "to_dict_recursive" not in fuente


def test_adaptador_rechaza_firma_de_otro_secreto():
    secreto = "whsec_prueba_local_etapa6"
    payload = json.dumps(evento_sdk_real(), separators=(",", ":")).encode("utf-8")
    cabecera = firmar_payload(payload, "whsec_otro_secreto", int(time.time()))

    proveedor = StripeProveedor(webhook_secret=secreto)
    with pytest.raises(ErrorFirmaWebhook):
        proveedor.verificar_webhook(payload, cabecera)


def test_adaptador_rechaza_payload_alterado_tras_firmar():
    secreto = "whsec_prueba_local_etapa6"
    payload = b'{"id":"evt_firmado_local","type":"payment_intent.succeeded"}'
    cabecera = firmar_payload(payload, secreto, int(time.time()))
    alterado = b'{"id":"evt_alterado","type":"payment_intent.succeeded"}'

    proveedor = StripeProveedor(webhook_secret=secreto)
    with pytest.raises(ErrorFirmaWebhook):
        proveedor.verificar_webhook(alterado, cabecera)


def test_adaptador_rechaza_payload_no_json_firmado():
    secreto = "whsec_prueba_local_etapa6"
    payload = b"esto-no-es-json"
    cabecera = firmar_payload(payload, secreto, int(time.time()))

    proveedor = StripeProveedor(webhook_secret=secreto)
    with pytest.raises(ErrorFirmaWebhook):
        proveedor.verificar_webhook(payload, cabecera)


def test_adaptador_rechaza_firma_con_timestamp_viejo():
    secreto = "whsec_prueba_local_etapa6"
    payload = json.dumps(evento_sdk_real(), separators=(",", ":")).encode("utf-8")
    cabecera = firmar_payload(payload, secreto, int(time.time()) - 100000)

    proveedor = StripeProveedor(webhook_secret=secreto)
    with pytest.raises(ErrorFirmaWebhook):
        proveedor.verificar_webhook(payload, cabecera)


def test_adaptador_rechaza_cabecera_sin_formato():
    secreto = "whsec_prueba_local_etapa6"
    payload = json.dumps(evento_sdk_real(), separators=(",", ":")).encode("utf-8")

    proveedor = StripeProveedor(webhook_secret=secreto)
    with pytest.raises(ErrorFirmaWebhook):
        proveedor.verificar_webhook(payload, "firma-invalida-sin-formato")

