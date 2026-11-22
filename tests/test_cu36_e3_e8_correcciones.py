"""CU36 E-3..E-8: correcciones de auditoria PostgreSQL.

Cubre webhooks huerfanos y su conciliacion manual, ausencia de locks durante
llamadas externas, captura tardia de cuotas, anulaciones CU32 centralizadas,
idempotencia por payload completo y bloqueo de correcciones sin precio.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.modules.gestion_pagos.models.models import (
    Pago, PagoAjuste, PagoDetalle, StripeWebhookHuerfano,
)
from app.modules.gestion_pagos.providers import (
    ErrorProveedorPagoDesconocido, IntentoPagoProveedor, ReembolsoProveedor,
)
from app.modules.gestion_pagos.repositories import ajustes as ajustes_repo
from app.modules.gestion_pagos.repositories import webhooks_huerfanos as huerfanos_repo
from app.modules.gestion_pagos.schemas.ajustes import (
    AjusteDetalleCrear, AnularPago, AtribuirReembolsoHuerfano, ConciliarAjuste,
    CorregirPago, ReembolsarPago,
)
from app.modules.gestion_pagos.schemas.planes_cuotas import (
    CuotaPlanCrear, PagoCuotaCrear, PlanPagoCrear,
)
from app.modules.gestion_pagos.services import ajustes as service
from app.modules.gestion_pagos.services import huerfanos as huerfanos_service
from app.modules.gestion_pagos.services import planes_cuotas as planes
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_pagos.services.webhook_tenancy import StripeWebhookScope
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario
from tests.test_base_flujo_pagos import db_pagos  # noqa: F401
from tests.test_cu31_pagos_administrativos import cliente_cu31, db_cu31  # noqa: F401


class ProveedorFalso:
    def __init__(self, *, estado="requires_payment_method",
                 refund_estado="succeeded", reembolsos=None, on_listar=None,
                 falla=None):
        self.estado = estado
        self.refund_estado = refund_estado
        self.reembolsos = list(reembolsos or [])
        self.on_listar = on_listar
        self.falla = falla
        self.creados = []
        self.monto_centavos = 2020
        self.moneda = "bob"

    def validar_configuracion(self, *, webhook=False):
        return None

    def crear_intencion(self, *, amount, currency, metadata, idempotency_key):
        self.creados.append((amount, currency, metadata, idempotency_key))
        return IntentoPagoProveedor(
            id=f"pi_new_{uuid4().hex[:12]}", client_secret="s",
            amount=amount, currency=currency, status="requires_payment_method",
        )

    def recuperar_intencion(self, payment_intent_id):
        return IntentoPagoProveedor(
            id=payment_intent_id, client_secret="s",
            amount=self.monto_centavos, currency=self.moneda, status=self.estado,
        )

    def cancelar_intencion(self, payment_intent_id):
        self.estado = "canceled"
        return self.recuperar_intencion(payment_intent_id)

    def crear_reembolso(self, *, payment_intent_id, amount, metadata,
                        idempotency_key):
        if self.falla is not None:
            raise self.falla
        reembolso = ReembolsoProveedor(
            id=f"re_{len(self.creados) + 1}", payment_intent_id=payment_intent_id,
            amount=amount, currency="bob", status=self.refund_estado,
            metadata=metadata,
        )
        self.reembolsos.append(reembolso)
        return reembolso

    def recuperar_reembolso(self, refund_id):
        return next(item for item in self.reembolsos if item.id == refund_id)

    def listar_reembolsos(self, payment_intent_id):
        if self.on_listar is not None:
            self.on_listar()
        return list(self.reembolsos)


def _admin(db):
    return db.get(Usuario, 13)


def _recepcion(db):
    return db.get(Usuario, 15)


def _paciente(db):
    return db.get(Usuario, 11)


def _servicio(db, servicio_id, precio="20.20"):
    db.execute(text(
        "INSERT INTO servicio_realizado "
        "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
        "fecha_realizacion,observaciones,estado,precio_aplicado) "
        "VALUES (:id,1,1,100,5,'2026-10-10',NULL,1,:precio)"
    ), {"id": servicio_id, "precio": precio})
    db.commit()


def _pago(db, pago_id, *, monto="20.20", estado="APROBADO", metodo="TARJETA",
          pasarela="STRIPE", referencia=None, servicio_id=1001):
    db.execute(text(
        "INSERT INTO pago (id,fecha_creacion,fecha_hora_pago,monto,moneda,"
        "metodo_pago,estado,pasarela,referencia_transaccion,observaciones,"
        "clave_idempotencia) VALUES (:id,:ahora,:ahora,:monto,'BOB',:metodo,"
        ":estado,:pasarela,:referencia,NULL,:clave)"
    ), {
        "id": pago_id, "ahora": datetime.now(timezone.utc),
        "monto": str(Decimal(monto)), "metodo": metodo, "estado": estado,
        "pasarela": pasarela, "referencia": referencia,
        "clave": f"fixture-{pago_id}-{uuid4().hex}",
    })
    db.execute(text(
        "INSERT INTO pago_detalle (id,pago_id,servicio_realizado_id,"
        "monto_aplicado) VALUES (:id,:pago,:servicio,:monto)"
    ), {
        "id": pago_id, "pago": pago_id, "servicio": servicio_id,
        "monto": str(Decimal(monto)),
    })
    db.commit()


def _plan(db, servicio_id, monto="20.20"):
    datos = PlanPagoCrear(
        paciente_id=1, consulta_clinica_id=100,
        servicio_realizado_ids=[servicio_id],
        cuotas=[CuotaPlanCrear(
            numero_cuota=1, monto_programado=Decimal(monto),
            fecha_vencimiento=date(2026, 11, 15),
        )],
    )
    return planes.crear_plan(db, datos, f"plan-{uuid4().hex}", _admin(db))


def _correccion(importe, servicio_id, *, evidencia=None):
    return CorregirPago(
        motivo="Error de digitacion del cajero", naturaleza="ERROR_DE_REGISTRO",
        importe=Decimal(importe), evidencia=evidencia,
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=servicio_id, monto=Decimal(importe),
        )],
    )


def _devolucion(importe, servicio_id):
    return ReembolsarPago(
        motivo="Devolucion autorizada al paciente", naturaleza="SOBREPAGO",
        importe=Decimal(importe), evidencia="RECIBO-DEV-001",
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=servicio_id, monto=Decimal(importe),
        )],
    )


def _evento_refund(event_id, refund_id, *, pi="pi_e3", amount=2020,
                   metadata=None, tipo="refund.created"):
    return {
        "id": event_id,
        "type": tipo,
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": refund_id, "payment_intent": pi, "amount": amount,
            "currency": "bob", "status": "succeeded",
            "metadata": metadata or {},
        }},
    }


# --------------------------------------------------------------------- E-3
def test_e3_refund_sin_metadata_se_registra_durablemente(db_cu31):
    _pago(db_cu31, 900, referencia="pi_e3")

    evento = _evento_refund("evt_e3_1", "re_huerfano_1")
    respuesta = pagos_service.procesar_evento_webhook_stripe(
        db_cu31, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert respuesta.procesado is False

    filas = db_cu31.scalars(select(StripeWebhookHuerfano)).all()
    assert len(filas) == 1
    assert filas[0].refund_id == "re_huerfano_1"
    assert filas[0].estado == "PENDIENTE"
    assert filas[0].amount == 2020
    assert filas[0].currency == "BOB"
    assert db_cu31.scalar(select(func.count()).select_from(PagoAjuste)) == 0
    assert db_cu31.get(Pago, 900).estado == "APROBADO"

    # Reentrega del mismo evento: no duplica ni contabiliza.
    repetido = pagos_service.procesar_evento_webhook_stripe(
        db_cu31, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )
    assert repetido.procesado is False
    assert db_cu31.scalar(
        select(func.count()).select_from(StripeWebhookHuerfano)
    ) == 1


def test_e3_sin_registro_durable_no_hay_respuesta_200(db_cu31, monkeypatch):
    _pago(db_cu31, 900, referencia="pi_e3")

    def falla(*args, **kwargs):
        raise RuntimeError("fallo de persistencia simulado")

    monkeypatch.setattr(huerfanos_repo, "registrar_huerfano", falla)
    with pytest.raises(RuntimeError):
        pagos_service.procesar_evento_webhook_stripe(
            db_cu31, _evento_refund("evt_e3_falla", "re_falla"),
            StripeWebhookScope(is_tenant=False, metadata={}),
        )


def test_e3_atribucion_manual_con_refund_explicito(db_cu31):
    _pago(db_cu31, 900, referencia="pi_e3")
    pagos_service.procesar_evento_webhook_stripe(
        db_cu31, _evento_refund("evt_e3_2", "re_huerfano_2"),
        StripeWebhookScope(is_tenant=False, metadata={}),
    )
    huerfano = db_cu31.scalar(select(StripeWebhookHuerfano))
    proveedor = ProveedorFalso(reembolsos=[ReembolsoProveedor(
        id="re_huerfano_2", payment_intent_id="pi_e3", amount=2020,
        currency="bob", status="succeeded", metadata={},
    )])
    datos = AtribuirReembolsoHuerfano(
        motivo="Reembolso ejecutado desde el dashboard de Stripe",
        naturaleza="SOBREPAGO",
        evidencia="Captura del dashboard 2026-11-17",
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=1001, monto=Decimal("-20.20"),
        )],
    )
    ajuste = huerfanos_service.atribuir_reembolso_huerfano(
        db_cu31, huerfano.id, datos, _admin(db_cu31), proveedor=proveedor,
    )
    assert ajuste.tipo == "REEMBOLSO"
    assert ajuste.estado == "CONFIRMADO"
    assert ajuste.referencia_externa == "re_huerfano_2"
    assert db_cu31.get(Pago, 900).estado == "REEMBOLSADO"
    assert db_cu31.get(StripeWebhookHuerfano, huerfano.id).estado == "ATRIBUIDO"
    assert db_cu31.get(StripeWebhookHuerfano, huerfano.id).ajuste_id == ajuste.id

    repetido = huerfanos_service.atribuir_reembolso_huerfano(
        db_cu31, huerfano.id, datos, _admin(db_cu31), proveedor=proveedor,
    )
    assert repetido.id == ajuste.id
    assert db_cu31.scalar(select(func.count()).select_from(PagoAjuste)) == 1


def test_e3_atribucion_errores_de_negocio(db_cu31):
    _pago(db_cu31, 900, referencia="pi_e3")
    pagos_service.procesar_evento_webhook_stripe(
        db_cu31, _evento_refund("evt_e3_3", "re_huerfano_3"),
        StripeWebhookScope(is_tenant=False, metadata={}),
    )
    huerfano = db_cu31.scalar(select(StripeWebhookHuerfano))
    datos = AtribuirReembolsoHuerfano(
        motivo="Reembolso ejecutado desde el dashboard de Stripe",
        naturaleza="SOBREPAGO", evidencia="Captura del dashboard 2026-11-17",
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=1001, monto=Decimal("-20.20"),
        )],
    )
    with pytest.raises(HTTPException) as rol:
        huerfanos_service.atribuir_reembolso_huerfano(
            db_cu31, huerfano.id, datos, _recepcion(db_cu31),
            proveedor=ProveedorFalso(),
        )
    assert rol.value.status_code == 403

    proveedor = ProveedorFalso(reembolsos=[ReembolsoProveedor(
        id="re_huerfano_3", payment_intent_id="pi_e3", amount=5000,
        currency="bob", status="succeeded", metadata={},
    )])
    with pytest.raises(HTTPException) as importe:
        huerfanos_service.atribuir_reembolso_huerfano(
            db_cu31, huerfano.id, datos, _admin(db_cu31), proveedor=proveedor,
        )
    assert importe.value.status_code == 409

    proveedor = ProveedorFalso(reembolsos=[ReembolsoProveedor(
        id="re_huerfano_3", payment_intent_id="pi_otro", amount=2020,
        currency="bob", status="succeeded", metadata={},
    )])
    with pytest.raises(HTTPException) as pi:
        huerfanos_service.atribuir_reembolso_huerfano(
            db_cu31, huerfano.id, datos, _admin(db_cu31), proveedor=proveedor,
        )
    assert pi.value.status_code == 409


def test_e2_atribucion_automatica_sigue_estricta(db_cu31):
    _pago(db_cu31, 900, referencia="pi_e2")
    proveedor = ProveedorFalso(
        reembolsos=[ReembolsoProveedor(
            id="re_sin_meta", payment_intent_id="pi_e2", amount=1010,
            currency="bob", status="succeeded", metadata={},
        )],
    )
    ajuste = service.reembolsar_pago(
        db_cu31, 900, _devolucion("-10.10", 1001), "e2-timeout",
        _admin(db_cu31), proveedor=ProveedorFalso(
            falla=ErrorProveedorPagoDesconocido("timeout"),
        ),
    )
    assert ajuste.estado == "DESCONOCIDO"
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu31, ajuste.id, ConciliarAjuste(
                resultado="CONFIRMADO",
                evidencia="Revision manual sin refund explicito",
            ),
            _admin(db_cu31), proveedor=proveedor,
        )
    assert error.value.status_code == 409
    assert "inequivoca" in error.value.detail


# --------------------------------------------------------------------- E-4
def test_e4_proveedor_se_consulta_sin_for_update(db_cu31, monkeypatch):
    _pago(db_cu31, 900, referencia="pi_e4")
    ajuste = service.reembolsar_pago(
        db_cu31, 900, _devolucion("-20.20", 1001), "e4-timeout",
        _admin(db_cu31), proveedor=ProveedorFalso(
            falla=ErrorProveedorPagoDesconocido("timeout"),
        ),
    )
    assert ajuste.estado == "DESCONOCIDO"

    lecturas = []
    original = service.admin_repo.obtener_pago_administrativo

    def registrar(db, pago_id, *, bloquear=False):
        lecturas.append(bloquear)
        return original(db, pago_id, bloquear=bloquear)

    monkeypatch.setattr(service.admin_repo, "obtener_pago_administrativo", registrar)

    def proveedor_durante():
        # En el momento de la llamada externa no se pidio ningun FOR UPDATE.
        assert lecturas and all(item is False for item in lecturas)

    proveedor_conciliacion = ProveedorFalso(on_listar=proveedor_durante)
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu31, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu31), proveedor=proveedor_conciliacion,
        )
    assert error.value.status_code == 409
    assert proveedor_conciliacion.on_listar is not None
    assert all(item is False for item in lecturas)


# --------------------------------------------------------------------- E-5
def test_e5_succeeded_tardio_sobre_cuota_anulada(db_cu31):
    _servicio(db_cu31, 3001)
    plan = _plan(db_cu31, 3001)
    pago = planes.registrar_pago_manual(
        db_cu31, plan.cuotas[0].id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-e5", _admin(db_cu31),
    )
    db_cu31.execute(text(
        "UPDATE pago SET referencia_transaccion='pi_cuota_e5' WHERE id=:id"
    ), {"id": pago.pago_id})
    db_cu31.commit()
    service.anular_pago(
        db_cu31, pago.pago_id,
        AnularPago(
            motivo="Pago de cuota registrado por duplicado",
            naturaleza="PAGO_DUPLICADO",
            evidencia="Acta interna 55/2026",
        ),
        "anu-e5", _admin(db_cu31),
    )
    assert db_cu31.get(Pago, pago.pago_id).estado == "ANULADO"

    evento = {
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": "pi_cuota_e5", "amount": 2020, "currency": "bob",
            "status": "succeeded", "metadata": {},
        }},
    }
    scope = StripeWebhookScope(is_tenant=False, metadata={
        "pago_id": str(pago.pago_id), "cuota_id": str(plan.cuotas[0].id),
        "plan_pago_id": str(plan.id),
    })
    primera = pagos_service.procesar_evento_webhook_stripe(db_cu31, evento, scope)
    segunda = pagos_service.procesar_evento_webhook_stripe(db_cu31, evento, scope)
    assert primera.procesado is True and segunda.procesado is True

    db_cu31.expire_all()
    assert db_cu31.get(Pago, pago.pago_id).estado == "APROBADO"
    recons = db_cu31.scalars(select(PagoAjuste).where(
        PagoAjuste.pago_id == pago.pago_id,
        PagoAjuste.tipo == "RECONCILIACION",
    )).all()
    assert len(recons) == 1
    assert recons[0].estado == "PENDIENTE_PROVEEDOR"
    assert recons[0].creado_por is None
    assert db_cu31.scalar(select(func.count()).select_from(Bitacora).where(
        Bitacora.accion == "RECONCILIAR_STRIPE_SUCCEEDED_TARDIO",
    )) == 1
    plan_refrescado = planes.consultar_plan(db_cu31, plan.id, _admin(db_cu31))
    assert plan_refrescado.cuotas[0].estado == "PAGADA"


# --------------------------------------------------------------------- E-6
def test_e6_cu32_anula_intento_pendiente_con_libro(db_cu31):
    _servicio(db_cu31, 3002)
    plan = _plan(db_cu31, 3002)
    inicial = ProveedorFalso()
    primera = planes.crear_intencion_stripe(
        db_cu31, plan.cuotas[0].id, _paciente(db_cu31), inicial,
    )
    pago_id = primera.pago_id
    assert db_cu31.get(Pago, pago_id).estado == "PENDIENTE"

    cancelado = ProveedorFalso(estado="canceled")
    cancelado.monto_centavos = 2020
    cancelado.moneda = "bob"
    planes.crear_intencion_stripe(
        db_cu31, plan.cuotas[0].id, _paciente(db_cu31), cancelado,
    )
    db_cu31.expire_all()
    assert db_cu31.get(Pago, pago_id).estado == "ANULADO"
    ajustes = db_cu31.scalars(select(PagoAjuste).where(
        PagoAjuste.pago_id == pago_id,
    )).all()
    assert len(ajustes) == 1
    assert ajustes[0].tipo == "ANULACION"
    assert ajustes[0].importe == Decimal("0.00")
    assert ajustes[0].clave_idempotencia == f"cu32-intento-cancelado-{pago_id}"
    assert db_cu31.scalar(select(func.count()).select_from(Bitacora).where(
        Bitacora.accion == "ANULAR_PAGO_PROVEEDOR_CANCELADO",
        Bitacora.id_registro_afectado == pago_id,
    )) == 1


def test_e6_cu32_no_anula_pago_aprobado(db_cu31):
    _servicio(db_cu31, 3003)
    plan = _plan(db_cu31, 3003)
    inicial = ProveedorFalso()
    primera = planes.crear_intencion_stripe(
        db_cu31, plan.cuotas[0].id, _paciente(db_cu31), inicial,
    )
    db_cu31.execute(text("UPDATE pago SET estado='APROBADO' WHERE id=:id"),
                    {"id": primera.pago_id})
    db_cu31.commit()

    cancelado = ProveedorFalso(estado="canceled")
    cancelado.monto_centavos = 2020
    cancelado.moneda = "bob"
    with pytest.raises(HTTPException) as error:
        planes.crear_intencion_stripe(
            db_cu31, plan.cuotas[0].id, _paciente(db_cu31), cancelado,
        )
    assert error.value.status_code == 409
    db_cu31.expire_all()
    assert db_cu31.get(Pago, primera.pago_id).estado == "APROBADO"
    assert db_cu31.scalar(select(func.count()).select_from(PagoAjuste)) == 0


# --------------------------------------------------------------------- E-7
def test_e7_idempotencia_compara_payload_completo(db_cu31):
    _pago(db_cu31, 900, monto="25.20", servicio_id=1001)
    db_cu31.execute(text(
        "INSERT INTO pago_detalle (id,pago_id,servicio_realizado_id,"
        "monto_aplicado) VALUES (901,900,1002,5.00)"
    ))
    db_cu31.execute(text(
        "UPDATE pago_detalle SET monto_aplicado=20.20 WHERE id=900"
    ))
    db_cu31.commit()

    primero = service.corregir_pago(
        db_cu31, 900, _correccion("-5.00", 1001), "idem-e7",
        _admin(db_cu31),
    )
    segundo = service.corregir_pago(
        db_cu31, 900, _correccion("-5.00", 1001), "idem-e7",
        _admin(db_cu31),
    )
    assert primero.id == segundo.id

    with pytest.raises(HTTPException) as distribucion:
        service.corregir_pago(
            db_cu31, 900, _correccion("-5.00", 1002), "idem-e7",
            _admin(db_cu31),
        )
    assert distribucion.value.status_code == 409

    with pytest.raises(HTTPException) as cruzado:
        service.reembolsar_pago(
            db_cu31, 900, _devolucion("-5.00", 1001), "idem-e7",
            _admin(db_cu31),
        )
    assert cruzado.value.status_code == 409
    assert "otros datos" in cruzado.value.detail


# --------------------------------------------------------------------- E-8
def test_e8_precio_nulo_bloquea_correcciones_positivas(db_cu31):
    _pago(db_cu31, 900, servicio_id=1004)

    with pytest.raises(HTTPException) as error:
        service.corregir_pago(
            db_cu31, 900, _correccion("5.00", 1004, evidencia="POS 900"),
            "e8-alza", _admin(db_cu31),
        )
    assert error.value.status_code == 409
    assert "precio_aplicado" in error.value.detail
    assert db_cu31.scalar(select(func.count()).select_from(PagoAjuste)) == 0

    # Una correccion a la baja no necesita techo y sigue permitida.
    ajuste = service.corregir_pago(
        db_cu31, 900, _correccion("-5.00", 1004), "e8-baja",
        _admin(db_cu31),
    )
    assert ajuste.importe == Decimal("-5.00")


def test_e8_precio_cero_bloquea_correcciones_positivas(db_cu31):
    _pago(db_cu31, 900, servicio_id=1005)
    with pytest.raises(HTTPException) as error:
        service.corregir_pago(
            db_cu31, 900, _correccion("5.00", 1005, evidencia="POS 901"),
            "e8-cero", _admin(db_cu31),
        )
    assert error.value.status_code == 409
