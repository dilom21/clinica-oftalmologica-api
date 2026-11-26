"""Regresiones con recursos del SDK real; todas las operaciones son locales."""

import json
import time
from types import SimpleNamespace

import pytest
import stripe
from fastapi import HTTPException
from sqlalchemy import func, select

from app.main import app
from app.modules.gestion_pagos.models.models import IntentoPagoCuota, Pago, PagoAjuste
from app.modules.gestion_pagos.providers import obtener_proveedor_stripe
from app.modules.gestion_pagos.providers.stripe_provider import StripeProveedor
from app.modules.gestion_pagos.schemas.ajustes import ConciliarAjuste
from app.modules.gestion_pagos.services import ajustes, planes_cuotas, service
from app.modules.gestion_pagos.services.webhook_tenancy import (
    StripeWebhookScope,
    StripeWebhookSessionResolver,
)
from tests.test_base_flujo_pagos import cliente_pagos, db_pagos, token
from tests.test_cu31_pagos_administrativos import cliente_cu31, db_cu31
from tests.test_cu32_cu33_planes_cuotas import (
    cliente_cu32, db_cu32, _crear_plan, _paciente, tenant_token,
)
from tests.test_cu36_anulacion_ajustes import db_cu36, _admin, _devolucion
from tests.test_stripe_multitenant import tenant_context
from tests.test_stripe_pagos import firmar_payload


def recurso_sdk(clase, **cambios):
    datos = {
        "id": "pi_local" if clase is stripe.PaymentIntent else "re_local",
        "object": "payment_intent" if clase is stripe.PaymentIntent else "refund",
        "amount": 10000,
        "currency": "BOB",
        "status": "requires_payment_method" if clase is stripe.PaymentIntent else "succeeded",
        "metadata": {"pago_id": "1", "consulta_clinica_id": "100"},
    }
    if clase is stripe.PaymentIntent:
        datos["client_secret"] = "secreto_sintetico_local"
    else:
        datos["payment_intent"] = "pi_local"
    return clase.construct_from({**datos, **cambios}, None)


@pytest.mark.parametrize("clase", [stripe.PaymentIntent, stripe.Refund])
@pytest.mark.parametrize("real", [True, False], ids=["sdk", "doble-con-dict"])
@pytest.mark.parametrize("metadata", [
    {"pago_id": "4", "tenant_id": "101"},
    {"numero": 4, "omitido": None},
    {},
    None,
])
def test_normaliza_metadata_sdk_y_diccionarios(clase, real, metadata):
    objeto = recurso_sdk(clase, metadata=metadata)
    if not real:
        objeto = SimpleNamespace(**objeto.to_dict())
    normalizar = (
        StripeProveedor._normalizar_intencion
        if clase is stripe.PaymentIntent else StripeProveedor._normalizar_reembolso
    )
    resultado = normalizar(objeto)
    assert resultado.metadata == {
        str(clave): str(valor) for clave, valor in (metadata or {}).items()
        if valor is not None
    }
    assert type(resultado.metadata) is dict
    assert resultado.amount == 10000
    assert resultado.currency == "bob"
    assert resultado.id == objeto.id
    if clase is stripe.PaymentIntent:
        assert resultado.client_secret == objeto.client_secret
        assert resultado.status == "requires_payment_method"
    else:
        assert resultado.payment_intent_id == "pi_local"
        assert resultado.confirmed


@pytest.mark.parametrize("clase", [stripe.PaymentIntent, stripe.Refund])
def test_metadata_ausente_en_dobles(clase):
    datos = recurso_sdk(clase).to_dict()
    datos.pop("metadata")
    normalizar = (
        StripeProveedor._normalizar_intencion
        if clase is stripe.PaymentIntent else StripeProveedor._normalizar_reembolso
    )
    assert normalizar(SimpleNamespace(**datos)).metadata == {}


def test_intenciones_sdk_crear_recuperar_cancelar(monkeypatch):
    objeto = recurso_sdk(stripe.PaymentIntent)
    capturado = {}

    def crear(**parametros):
        capturado.update(parametros)
        return objeto

    monkeypatch.setattr(stripe.PaymentIntent, "create", crear)
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", lambda *a, **kw: objeto)
    monkeypatch.setattr(
        stripe.PaymentIntent, "cancel",
        lambda self, **kw: recurso_sdk(stripe.PaymentIntent, status="canceled"),
    )
    proveedor = StripeProveedor(secret_key="sk_test_sintetica_local")
    creado = proveedor.crear_intencion(
        amount=10000, currency="bob", metadata={"pago_id": "1"},
        idempotency_key="clinica-tenant-101-cuota-pago-1",
    )
    assert capturado["idempotency_key"] == "clinica-tenant-101-cuota-pago-1"
    assert capturado["metadata"] == {"pago_id": "1"}
    assert capturado["automatic_payment_methods"] == {"enabled": True}
    assert proveedor.recuperar_intencion(creado.id) == creado
    cancelado = proveedor.cancelar_intencion(creado.id)
    assert cancelado.status == "canceled"
    assert cancelado.metadata == creado.metadata


@pytest.mark.parametrize("lista_real", [True, False], ids=["sdk", "dict"])
def test_reembolsos_sdk_crear_recuperar_listar_y_payment_intent_expandido(
    monkeypatch, lista_real,
):
    objeto = recurso_sdk(
        stripe.Refund, payment_intent={"id": "pi_local", "object": "payment_intent"},
        metadata={"ajuste_id": "7", "tenant_id": "101"},
    )
    capturado = {}

    def crear(**parametros):
        capturado.update(parametros)
        return objeto

    monkeypatch.setattr(stripe.Refund, "create", crear)
    monkeypatch.setattr(stripe.Refund, "retrieve", lambda *a, **kw: objeto)
    lista = stripe.ListObject.construct_from({
        "object": "list", "data": [objeto.to_dict()], "has_more": False,
    }, None)
    if not lista_real:
        lista = {"data": [objeto]}
    monkeypatch.setattr(stripe.Refund, "list", lambda **kw: lista)
    proveedor = StripeProveedor(secret_key="sk_test_sintetica_local")
    creado = proveedor.crear_reembolso(
        payment_intent_id="pi_local", amount=10000,
        metadata={"ajuste_id": "7", "tenant_id": "101"},
        idempotency_key="clinica-tenant-101-ajuste-7",
    )
    assert capturado["idempotency_key"] == "clinica-tenant-101-ajuste-7"
    assert creado.payment_intent_id == "pi_local"
    assert creado.metadata == {"ajuste_id": "7", "tenant_id": "101"}
    assert proveedor.recuperar_reembolso(creado.id) == creado
    assert proveedor.listar_reembolsos("pi_local") == [creado]


@pytest.mark.parametrize("flujo", ["directo", "cuota"])
@pytest.mark.parametrize("tenant", [False, True], ids=["legacy", "tenant"])
def test_recupera_creacion_previa_al_error_sin_duplicar(
    db_cu32, monkeypatch, flujo, tenant,
):
    """Simula Stripe aceptando create antes del TypeError historico.

    El almacen local emula la retencion de idempotencia de Stripe, no su red.
    """
    db = db_cu32
    if tenant:
        db.info["tenant_context"] = tenant_context(10, "CLINICA_A", 101, "clinica_a")
    if flujo == "cuota":
        cuota_id = _crear_plan(db).cuotas[0].id

    proveedor = StripeProveedor(secret_key="sk_test_sintetica_local")
    almacen = {}
    solicitudes = []
    recuperaciones = []

    def crear(**parametros):
        solicitudes.append(parametros)
        clave = parametros["idempotency_key"]
        if clave not in almacen:
            almacen[clave] = recurso_sdk(
                stripe.PaymentIntent, amount=parametros["amount"],
                currency=parametros["currency"], metadata=parametros["metadata"],
            )
        return almacen[clave]

    def recuperar(identificador, **kw):
        recuperaciones.append(identificador)
        return next(objeto for objeto in almacen.values() if objeto.id == identificador)

    monkeypatch.setattr(stripe.PaymentIntent, "create", crear)
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", recuperar)

    def iniciar():
        if flujo == "cuota":
            return planes_cuotas.crear_intencion_stripe(db, cuota_id, _paciente(db), proveedor)
        return service.crear_intencion_stripe(db, [2001], _paciente(db), proveedor)

    def normalizacion_anterior(objeto):
        # Reproduce el fallo original DESPUES de la creacion externa simulada.
        return dict(objeto.metadata)

    with monkeypatch.context() as contexto:
        contexto.setattr(proveedor, "_normalizar_intencion", normalizacion_anterior)
        with pytest.raises(TypeError, match="to_dict"):
            iniciar()

    db.rollback()  # Igual que cerrar la sesion de una solicitud fallida.
    pendiente = db.scalar(select(Pago).where(Pago.pasarela == "STRIPE"))
    assert pendiente.estado == "PENDIENTE"
    assert pendiente.referencia_transaccion is None
    pago_id = pendiente.id
    recuperado = iniciar()
    repetido = iniciar()  # Ahora usa retrieve con el ID ya persistido.
    assert recuperado.pago_id == repetido.pago_id == pago_id
    assert recuperado.payment_intent_id == repetido.payment_intent_id == "pi_local"
    assert solicitudes[0] == solicitudes[1]
    prefijo = "clinica-tenant-101" if tenant else "clinica"
    operacion = "cuota-pago" if flujo == "cuota" else "pago"
    assert solicitudes[0]["idempotency_key"] == f"{prefijo}-{operacion}-{pago_id}"
    if tenant:
        assert solicitudes[0]["metadata"]["tenant_id"] == "101"
    assert len(almacen) == 1
    assert recuperaciones == ["pi_local"]
    assert db.scalar(select(func.count()).select_from(Pago).where(Pago.pasarela == "STRIPE")) == 1
    if flujo == "cuota":
        assert db.scalar(select(func.count()).select_from(IntentoPagoCuota)) == 1

    # Si Stripe ya capturo el pago, no se crea otro mientras llega el webhook.
    next(iter(almacen.values())).status = "succeeded"
    with pytest.raises(HTTPException) as error:
        iniciar()
    assert error.value.status_code == 409
    assert len(solicitudes) == 2
    assert len(almacen) == 1


def test_refund_dashboard_recupera_metadata_tenant_del_sdk(monkeypatch):
    metadata = {
        "clinica_contexto": "tenant", "empresa_id": "10",
        "empresa_codigo": "CLINICA_A", "tenant_id": "101",
        "pago_id": "1", "consulta_clinica_id": "100",
    }
    objeto = recurso_sdk(stripe.PaymentIntent, metadata=metadata)
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", lambda *a, **kw: objeto)
    resultado = StripeWebhookSessionResolver._metadata_desde_payment_intent(
        {"data": {"object": {"payment_intent": "pi_local"}}},
        StripeProveedor(secret_key="sk_test_sintetica_local"),
    )
    assert resultado == metadata
    assert type(resultado) is dict


@pytest.mark.parametrize("flujo", ["directo", "cuota"])
def test_endpoints_intencion_con_metadata_del_sdk(cliente_cu32, db_cu32, monkeypatch, flujo):
    def crear(**parametros):
        return recurso_sdk(
            stripe.PaymentIntent, amount=parametros["amount"],
            currency=parametros["currency"], metadata=parametros["metadata"],
        )

    monkeypatch.setattr(stripe.PaymentIntent, "create", crear)
    proveedor = StripeProveedor(secret_key="sk_test_sintetica_local")
    monkeypatch.setitem(app.dependency_overrides, obtener_proveedor_stripe, lambda: proveedor)
    if flujo == "cuota":
        cuota = _crear_plan(db_cu32).cuotas[0]
        respuesta = cliente_cu32.post(
            f"/pagos/cuotas/{cuota.id}/stripe/intencion", headers=tenant_token(11),
        )
    else:
        respuesta = cliente_cu32.post(
            "/pagos/stripe/intencion", headers=token(11),
            json={"servicio_realizado_ids": [2001]},
        )
    assert respuesta.status_code == 201
    pago = db_cu32.get(Pago, respuesta.json()["pago_id"])
    assert pago.referencia_transaccion == "pi_local"
    assert pago.estado == "PENDIENTE"


def test_concilia_refund_creado_antes_del_error_y_webhook_sdk_idempotente(
    db_cu36, monkeypatch,
):
    proveedor = StripeProveedor(secret_key="sk_test_sintetica_local", webhook_secret="whsec_local")
    creados = []

    def crear(**parametros):
        objeto = recurso_sdk(
            stripe.Refund, amount=parametros["amount"], currency="bob",
            payment_intent=parametros["payment_intent"], metadata=parametros["metadata"],
        )
        creados.append(objeto)
        return objeto

    monkeypatch.setattr(stripe.Refund, "create", crear)

    def normalizacion_anterior(objeto):
        return dict(objeto.metadata)

    with monkeypatch.context() as contexto:
        contexto.setattr(proveedor, "_normalizar_reembolso", normalizacion_anterior)
        with pytest.raises(TypeError, match="to_dict"):
            ajustes.reembolsar_pago(
                db_cu36, 900, _devolucion("-20.20", servicio=1100),
                "refund-error-normalizacion", _admin(db_cu36), proveedor=proveedor,
            )
    db_cu36.rollback()
    ajuste = db_cu36.scalar(select(PagoAjuste).where(PagoAjuste.pago_id == 900))
    assert ajuste.estado == "PENDIENTE_PROVEEDOR"
    assert ajuste.referencia_externa is None
    assert db_cu36.get(Pago, 900).estado == "APROBADO"
    lista = stripe.ListObject.construct_from({
        "object": "list", "data": [creados[0].to_dict()], "has_more": False,
    }, None)
    monkeypatch.setattr(stripe.Refund, "list", lambda **kw: lista)
    conciliado = ajustes.conciliar_ajuste(
        db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
        _admin(db_cu36), proveedor=proveedor,
    )
    assert conciliado.estado == "CONFIRMADO"
    assert conciliado.referencia_externa == "re_local"
    assert db_cu36.get(Pago, 900).estado == "REEMBOLSADO"

    # Firma local verificada por el SDK; .to_dict() debe convertir tambien
    # data.object.metadata a dict antes de entrar al servicio de webhooks.
    evento = {
        "id": "evt_refund_local", "object": "event", "type": "refund.updated",
        "created": int(time.time()), "data": {"object": creados[0].to_dict()},
    }
    payload = json.dumps(evento).encode()
    firma = firmar_payload(payload, "whsec_local", int(time.time()))
    convertido = proveedor.verificar_webhook(payload, firma)
    metadata = convertido["data"]["object"]["metadata"]
    assert type(metadata) is dict
    scope = StripeWebhookScope(is_tenant=False, metadata=metadata)
    for _ in range(2):
        assert service.procesar_evento_webhook_stripe(db_cu36, convertido, scope).procesado
    assert len(creados) == 1
    assert db_cu36.scalar(select(func.count()).select_from(PagoAjuste).where(
        PagoAjuste.pago_id == 900,
    )) == 1
