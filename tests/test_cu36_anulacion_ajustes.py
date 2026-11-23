"""CU36: anulaciones, correcciones, devoluciones, reembolsos y conciliacion."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from tests.test_base_flujo_pagos import cliente_pagos, db_pagos, token
from tests.test_cu31_pagos_administrativos import cliente_cu31, db_cu31
from app.modules.gestion_pagos.models.models import (
    Pago,
    PagoAjuste,
    PagoAjusteDetalle,
)
from app.modules.gestion_pagos.providers import (
    ErrorProveedorPagoDesconocido,
    ReembolsoProveedor,
)
from app.modules.gestion_pagos.repositories import repository as pago_repo
from app.modules.gestion_pagos.repositories import saldos
from app.modules.gestion_pagos.schemas.ajustes import (
    AjusteDetalleCrear,
    AnularPago,
    ConciliarAjuste,
    CorregirPago,
    ReembolsarPago,
)
from app.modules.gestion_pagos.services import ajustes as service
from app.modules.gestion_pagos.services import administrativos
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_pagos.services.webhook_tenancy import StripeWebhookScope
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario


class ProveedorFalso:
    def __init__(self, *, estado="canceled", refund_estado="succeeded", falla=None):
        self.estado = estado
        self.refund_estado = refund_estado
        self.falla = falla
        self.reembolsos = []
        self.creados = []

    def validar_configuracion(self, *, webhook=False):
        return None

    def recuperar_intencion(self, payment_intent_id):
        return SimpleNamespace(
            id=payment_intent_id, amount=2020, currency="bob",
            status=self.estado, client_secret="s",
        )

    def cancelar_intencion(self, payment_intent_id):
        self.estado = "canceled"
        return self.recuperar_intencion(payment_intent_id)

    def crear_reembolso(self, *, payment_intent_id, amount, metadata, idempotency_key):
        if self.falla is not None:
            raise self.falla
        self.creados.append((payment_intent_id, amount, metadata, idempotency_key))
        reembolso = ReembolsoProveedor(
            id=f"re_{len(self.creados)}",
            payment_intent_id=payment_intent_id,
            amount=amount,
            currency="bob",
            status=self.refund_estado,
            failure_reason=None,
        )
        self.reembolsos.append(reembolso)
        return reembolso

    def recuperar_reembolso(self, refund_id):
        return next(item for item in self.reembolsos if item.id == refund_id)

    def listar_reembolsos(self, payment_intent_id):
        return list(self.reembolsos)


@pytest.fixture
def db_cu36(db_cu31):
    db_cu31.execute(text(
        "INSERT INTO servicio_realizado "
        "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
        "fecha_realizacion,observaciones,estado,precio_aplicado) VALUES "
        "(1100,1,1,100,5,'2026-10-10',NULL,1,20.20),"
        "(1101,1,1,100,5,'2026-10-10',NULL,1,30.00),"
        "(1102,1,1,100,5,'2026-10-10',NULL,1,20.20),"
        "(1103,1,1,100,5,'2026-10-10',NULL,1,20.20)"
    ))
    db_cu31.execute(text(
        "INSERT INTO pago "
        "(id,fecha_creacion,fecha_hora_pago,monto,moneda,metodo_pago,estado,"
        "pasarela,referencia_transaccion,observaciones,clave_idempotencia) VALUES "
        "(900,'2026-10-10 12:00:00','2026-10-10 12:00:00',20.20,'BOB','TARJETA',"
        "'APROBADO','STRIPE','pi_cu36',NULL,'stripe-900'),"
        "(901,'2026-10-10 13:00:00','2026-10-10 13:00:00',20.20,'BOB','EFECTIVO',"
        "'APROBADO',NULL,NULL,NULL,'efectivo-901'),"
        "(910,'2026-10-10 14:00:00',NULL,20.20,'BOB','TRANSFERENCIA',"
        "'PENDIENTE',NULL,'REF-910',NULL,'transfer-910')"
    ))
    db_cu31.execute(text(
        "INSERT INTO pago_detalle (id,pago_id,servicio_realizado_id,monto_aplicado) "
        "VALUES (900,900,1100,20.20),(901,901,1101,20.20),"
        "(910,910,1103,20.20)"
    ))
    db_cu31.commit()
    return db_cu31


def _admin(db):
    return db.get(Usuario, 13)


def _recepcion(db):
    return db.get(Usuario, 15)


def _pago(db, pago_id):
    return db.get(Pago, pago_id)


def _anular(motivo="Registro duplicado sin segundo cobro", **extra):
    return AnularPago(motivo=motivo, naturaleza="PAGO_DUPLICADO", **extra)


def _correccion(importe, servicio=1001, motivo="Error de digitacion del cajero"):
    return CorregirPago(
        motivo=motivo, naturaleza="ERROR_DE_REGISTRO", importe=Decimal(importe),
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=servicio, monto=Decimal(importe),
        )],
    )


def _devolucion(importe, servicio=1001, **extra):
    return ReembolsarPago(
        motivo="Devolucion autorizada al paciente",
        naturaleza="SOBREPAGO",
        importe=Decimal(importe),
        evidencia=extra.pop("evidencia", "RECIBO-DEV-001"),
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=servicio, monto=Decimal(importe),
        )],
        **extra,
    )


# ---------------------------------------------------------------- anulacion
def test_anular_pendiente_no_cobrado(db_cu36):
    ajuste = service.anular_pago(
        db_cu36, 501, _anular(), "anu-501", _admin(db_cu36), "127.0.0.1",
    )
    assert ajuste.tipo == "ANULACION" and ajuste.importe == Decimal("0.00")
    assert ajuste.estado == "CONFIRMADO"
    assert _pago(db_cu36, 501).estado == "ANULADO"
    assert db_cu36.scalar(select(Bitacora.accion).where(
        Bitacora.accion == "ANULAR_PAGO",
    )) == "ANULAR_PAGO"
    assert saldos.monto_vigente_pago(db_cu36, _pago(db_cu36, 501)) == Decimal("0.00")


def test_anular_aprobado_exige_evidencia_y_administrador(db_cu36):
    with pytest.raises(HTTPException) as sin_evidencia:
        service.anular_pago(db_cu36, 500, _anular(), "anu-500-a", _admin(db_cu36))
    assert sin_evidencia.value.status_code == 409

    with pytest.raises(HTTPException) as recepcion:
        service.anular_pago(
            db_cu36, 500,
            _anular(evidencia="Acta interna 45/2026 sin movimiento bancario"),
            "anu-500-b", _recepcion(db_cu36),
        )
    assert recepcion.value.status_code == 403
    assert _pago(db_cu36, 500).estado == "APROBADO"


def test_anular_aprobado_con_evidencia_libera_servicio(db_cu36):
    service.anular_pago(
        db_cu36, 500,
        _anular(evidencia="Acta interna 45/2026 sin movimiento bancario"),
        "anu-500", _admin(db_cu36),
    )
    assert _pago(db_cu36, 500).estado == "ANULADO"
    pendientes = [
        item.id for item in pago_repo.listar_servicios_pendientes_pago(
            db_cu36, paciente_id=1, consulta_clinica_id=100,
        )
    ]
    assert 1001 in pendientes
    # CU31 puede volver a cobrar el servicio liberado
    respuesta = administrativos.registrar_pago(
        db_cu36,
        administrativos.PagoAdministrativoCrear(
            paciente_id=1, servicio_realizado_ids=[1001], metodo_pago="EFECTIVO",
        ),
        "reintento-1001", _admin(db_cu36),
    )
    assert respuesta.estado == "APROBADO"


def test_anular_stripe_aprobado_rechazado(db_cu36):
    with pytest.raises(HTTPException) as error:
        service.anular_pago(
            db_cu36, 900,
            _anular(evidencia="Intento de anulacion directa"),
            "anu-900", _admin(db_cu36), proveedor=ProveedorFalso(),
        )
    assert error.value.status_code == 409
    assert "reembolso" in error.value.detail


def test_anular_stripe_pendiente_requiere_confirmacion_proveedor(db_cu36):
    db_cu36.execute(text(
        "UPDATE pago SET estado='PENDIENTE', pasarela='STRIPE', "
        "referencia_transaccion='pi_cu36' WHERE id=900"
    ))
    db_cu36.commit()
    proveedor = ProveedorFalso(estado="requires_payment_method")
    ajuste = service.anular_pago(
        db_cu36, 900, _anular(), "anu-900-pend", _admin(db_cu36),
        proveedor=proveedor,
    )
    assert ajuste.estado == "CONFIRMADO"
    assert _pago(db_cu36, 900).estado == "ANULADO"

    db_cu36.execute(text(
        "UPDATE pago SET estado='PENDIENTE' WHERE id=900"
    ))
    db_cu36.commit()
    with pytest.raises(HTTPException) as capturado:
        service.anular_pago(
            db_cu36, 900, _anular(), "anu-900-cap", _admin(db_cu36),
            proveedor=ProveedorFalso(estado="succeeded"),
        )
    assert capturado.value.status_code == 409
    assert "reembolso" in capturado.value.detail


# --------------------------------------------------------------- correccion
def test_correccion_a_la_baja_conserva_original(db_cu36):
    ajuste = service.corregir_pago(
        db_cu36, 500, _correccion("-5.00"), "cor-500", _admin(db_cu36),
    )
    assert ajuste.importe == Decimal("-5.00")
    assert ajuste.detalles[0].monto == Decimal("-5.00")
    pago = _pago(db_cu36, 500)
    assert Decimal(pago.monto) == Decimal("20.20")
    assert pago.detalles[0].monto_aplicado == Decimal("20.20")
    assert pago.estado == "APROBADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("15.20")
    assert Decimal(db_cu36.scalar(
        select(func.sum(PagoAjusteDetalle.monto))
    )) == Decimal("-5.00")


def test_correccion_al_alza_exige_admin_y_evidencia(db_cu36):
    with pytest.raises(HTTPException) as recepcion:
        service.corregir_pago(
            db_cu36, 901, _correccion("5.00", servicio=1101),
            "cor-alza-a", _recepcion(db_cu36),
        )
    assert recepcion.value.status_code == 403

    datos = CorregirPago(
        motivo="El paciente pago un adicional real", naturaleza="ERROR_DE_REGISTRO",
        importe=Decimal("5.00"), evidencia="Comprobante POS 778",
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=1101, monto=Decimal("5.00"),
        )],
    )
    service.corregir_pago(db_cu36, 901, datos, "cor-alza-b", _admin(db_cu36))
    assert saldos.monto_vigente_pago(db_cu36, _pago(db_cu36, 901)) == (
        Decimal("25.20")
    )


def test_correccion_no_deja_vigente_cero_ni_supera_precio(db_cu36):
    with pytest.raises(HTTPException) as cero:
        service.corregir_pago(
            db_cu36, 500, _correccion("-20.20"), "cor-cero", _admin(db_cu36),
        )
    assert cero.value.status_code == 409
    with pytest.raises(HTTPException) as tope:
        service.corregir_pago(
            db_cu36, 500,
            CorregirPago(
                motivo="Correccion por encima del precio aplicado",
                naturaleza="ERROR_DE_REGISTRO", importe=Decimal("1.00"),
                evidencia="Evidencia sintetica",
                servicios=[AjusteDetalleCrear(
                    servicio_realizado_id=1001, monto=Decimal("1.00"),
                )],
            ),
            "cor-tope", _admin(db_cu36),
        )
    assert tope.value.status_code == 409


def test_correccion_servicio_ajeno_al_pago(db_cu36):
    with pytest.raises(HTTPException) as error:
        service.corregir_pago(
            db_cu36, 500, _correccion("-1.00", servicio=1000),
            "cor-ajeno", _admin(db_cu36),
        )
    assert error.value.status_code == 409


# -------------------------------------------------------------- devolucion
def test_devolucion_parcial_deja_pago_aprobado(db_cu36):
    ajuste = service.reembolsar_pago(
        db_cu36, 500, _devolucion("-5.00"), "dev-500-parcial", _admin(db_cu36),
    )
    assert ajuste.tipo == "DEVOLUCION" and ajuste.estado == "CONFIRMADO"
    pago = _pago(db_cu36, 500)
    assert pago.estado == "APROBADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("15.20")
    assert saldos.reembolsado_confirmado_pago(db_cu36, pago.id) == Decimal("5.00")


def test_devolucion_total_marca_reembolsado_y_libera_servicio(db_cu36):
    service.reembolsar_pago(
        db_cu36, 500, _devolucion("-20.20"), "dev-500-total", _admin(db_cu36),
    )
    pago = _pago(db_cu36, 500)
    assert pago.estado == "REEMBOLSADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("0.00")
    assert pago.monto == Decimal("20.20")  # el importe base no se sobrescribe
    assert not pago_repo.servicio_cubierto_por_pago_aprobado(db_cu36, 1001)


def test_devolucion_manual_sin_evidencia_rechazada(db_cu36):
    datos = ReembolsarPago(
        motivo="Devolucion sin comprobante", naturaleza="SOBREPAGO",
        importe=Decimal("-1.00"), evidencia=None,
        servicios=[AjusteDetalleCrear(
            servicio_realizado_id=1001, monto=Decimal("-1.00"),
        )],
    )
    with pytest.raises(HTTPException) as error:
        service.reembolsar_pago(
            db_cu36, 500, datos, "dev-sin-evidencia", _admin(db_cu36),
        )
    assert error.value.status_code == 409


def test_recepcionista_no_puede_reembolsar(db_cu36):
    with pytest.raises(HTTPException) as error:
        service.reembolsar_pago(
            db_cu36, 500, _devolucion("-1.00"), "dev-recep", _recepcion(db_cu36),
        )
    assert error.value.status_code == 403


# ------------------------------------------------------------------ stripe
def test_reembolso_stripe_confirmado_y_webhook_idempotente(db_cu36):
    proveedor = ProveedorFalso(refund_estado="succeeded")
    ajuste = service.reembolsar_pago(
        db_cu36, 900, _devolucion("-20.20", servicio=1100),
        "ref-900", _admin(db_cu36), proveedor=proveedor,
    )
    assert ajuste.tipo == "REEMBOLSO"
    assert ajuste.efecto_caja == "DEVOLUCION_STRIPE"
    assert ajuste.referencia_externa == "re_1"
    assert ajuste.estado == "CONFIRMADO"
    assert _pago(db_cu36, 900).estado == "REEMBOLSADO"

    evento = {
        "type": "refund.updated",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": "re_1", "amount": 2020, "currency": "bob",
            "status": "succeeded", "payment_intent": "pi_cu36",
            "metadata": {"ajuste_id": str(ajuste.id), "pago_id": "900"},
        }},
    }
    scope = StripeWebhookScope(
        is_tenant=False,
        metadata={"ajuste_id": str(ajuste.id), "pago_id": "900"},
    )
    primera = pagos_service.procesar_evento_webhook_stripe(db_cu36, evento, scope)
    segunda = pagos_service.procesar_evento_webhook_stripe(db_cu36, evento, scope)
    assert primera.procesado is True and segunda.procesado is True
    assert _pago(db_cu36, 900).estado == "REEMBOLSADO"
    assert db_cu36.scalar(select(func.count()).select_from(PagoAjuste).where(
        PagoAjuste.pago_id == 900,
    )) == 1


def test_reembolso_stripe_pendiente_y_timeout_conciliable(db_cu36):
    proveedor = ProveedorFalso(refund_estado="pending")
    ajuste = service.reembolsar_pago(
        db_cu36, 900, _devolucion("-20.20", servicio=1100),
        "ref-900-pend", _admin(db_cu36), proveedor=proveedor,
    )
    assert ajuste.estado == "PENDIENTE_PROVEEDOR"
    pago = _pago(db_cu36, 900)
    assert pago.estado == "APROBADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("20.20")

    with pytest.raises(HTTPException) as pendiente:
        service.conciliar_ajuste(
            db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu36), proveedor=proveedor,
        )
    assert pendiente.value.status_code == 409

    proveedor.refund_estado = "succeeded"
    proveedor.reembolsos[0] = ReembolsoProveedor(
        id="re_1", payment_intent_id="pi_cu36", amount=2020,
        currency="bob", status="succeeded",
    )
    conciliado = service.conciliar_ajuste(
        db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
        _admin(db_cu36), proveedor=proveedor,
    )
    assert conciliado.estado == "CONFIRMADO"
    assert _pago(db_cu36, 900).estado == "REEMBOLSADO"


def test_reembolso_stripe_timeout_queda_desconocido_y_no_se_contabiliza(db_cu36):
    proveedor = ProveedorFalso(
        falla=ErrorProveedorPagoDesconocido("timeout sintetico"),
    )
    ajuste = service.reembolsar_pago(
        db_cu36, 900, _devolucion("-20.20", servicio=1100),
        "ref-900-timeout", _admin(db_cu36), proveedor=proveedor,
    )
    assert ajuste.estado == "DESCONOCIDO"
    pago = _pago(db_cu36, 900)
    assert pago.estado == "APROBADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("20.20")
    assert saldos.pago_tiene_ajuste_abierto(db_cu36, pago.id) is True


def test_succeeded_tardio_sobre_pago_anulado_abre_conciliacion(db_cu36):
    service.anular_pago(
        db_cu36, 500,
        _anular(evidencia="Acta 46/2026: se creia duplicado"),
        "anu-500-tardio", _admin(db_cu36),
    )
    assert _pago(db_cu36, 500).estado == "ANULADO"

    evento = {
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": "ok", "amount": 2020, "currency": "bob", "status": "succeeded",
            "metadata": {},
        }},
    }
    scope = StripeWebhookScope(is_tenant=False, metadata={})
    primera = pagos_service.procesar_evento_webhook_stripe(db_cu36, evento, scope)
    segunda = pagos_service.procesar_evento_webhook_stripe(db_cu36, evento, scope)
    assert primera.procesado is True and segunda.procesado is True
    pago = _pago(db_cu36, 500)
    assert pago.estado == "APROBADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("20.20")
    reconciliaciones = db_cu36.scalars(select(PagoAjuste).where(
        PagoAjuste.pago_id == 500, PagoAjuste.tipo == "RECONCILIACION",
    )).all()
    assert len(reconciliaciones) == 1
    assert reconciliaciones[0].estado == "PENDIENTE_PROVEEDOR"


def _captura_stripe_tardia(db):
    """Anula el pago 500 y luego confirma la captura real en Stripe."""
    service.anular_pago(
        db, 500,
        _anular(evidencia="Acta 47/2026: anulacion previa al webhook"),
        "anu-500-e1", _admin(db),
    )
    evento = {
        "type": "payment_intent.succeeded",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": {"object": {
            "id": "ok", "amount": 2020, "currency": "bob",
            "status": "succeeded", "metadata": {},
        }},
    }
    pagos_service.procesar_evento_webhook_stripe(
        db, evento, StripeWebhookScope(is_tenant=False, metadata={}),
    )


def test_historial_tras_captura_stripe_tardia_acepta_creador_nulo(db_cu36):
    """E-1: el ajuste RECONCILIACION del webhook no tiene actor humano."""
    _captura_stripe_tardia(db_cu36)

    historial = service.historial_ajustes(db_cu36, 500, _admin(db_cu36))
    conciliaciones = [
        item for item in historial.ajustes if item.tipo == "RECONCILIACION"
    ]
    assert len(conciliaciones) == 1
    ajuste = conciliaciones[0]
    assert ajuste.creado_por is None
    payload = historial.model_dump()
    conciliacion_payload = next(
        item for item in payload["ajustes"] if item["tipo"] == "RECONCILIACION"
    )
    assert conciliacion_payload["creado_por"] is None

    detalle = service.consultar_ajuste(db_cu36, ajuste.id, _admin(db_cu36))
    assert detalle.creado_por is None
    assert detalle.model_dump()["creado_por"] is None


def test_api_historial_tras_captura_stripe_tardia_responde_200(
    cliente_cu31, db_cu36,
):
    """E-1: GET historial y detalle no deben fallar con 500."""
    _captura_stripe_tardia(db_cu36)
    cabeceras = token(13, 2)

    historial = cliente_cu31.get(
        "/pagos/administrativos/500/ajustes", headers=cabeceras,
    )
    assert historial.status_code == 200, historial.text
    cuerpo = historial.json()
    conciliacion = next(
        item for item in cuerpo["ajustes"] if item["tipo"] == "RECONCILIACION"
    )
    assert conciliacion["creado_por"] is None

    detalle = cliente_cu31.get(
        f"/pagos/administrativos/ajustes/{conciliacion['id']}",
        headers=cabeceras,
    )
    assert detalle.status_code == 200, detalle.text
    assert detalle.json()["creado_por"] is None


def _refund(
    refund_id,
    *,
    ajuste_id=None,
    pago_id="900",
    amount=1010,
    currency="bob",
    payment_intent="pi_cu36",
):
    metadata = {"pago_id": str(pago_id)} if pago_id is not None else {}
    if ajuste_id is not None:
        metadata["ajuste_id"] = str(ajuste_id)
    return ReembolsoProveedor(
        id=refund_id,
        payment_intent_id=payment_intent,
        amount=amount,
        currency=currency,
        status="succeeded",
        metadata=metadata,
    )


def _conciliacion_desconocida(db, clave):
    """Reembolso Stripe con timeout: queda DESCONOCIDO y sin referencia."""
    proveedor = ProveedorFalso(falla=ErrorProveedorPagoDesconocido("timeout"))
    ajuste = service.reembolsar_pago(
        db, 900, _devolucion("-10.10", servicio=1100), clave,
        _admin(db), proveedor=proveedor,
    )
    assert ajuste.estado == "DESCONOCIDO"
    assert ajuste.referencia_externa is None
    return ajuste, proveedor


def _estado_ajuste(db, ajuste_id):
    db.expire_all()
    return db.get(PagoAjuste, ajuste_id)


def test_conciliacion_elige_solo_el_refund_inequivoco(db_cu36):
    """E-2: con varios refunds no se toma el primero de la lista."""
    ajuste, proveedor = _conciliacion_desconocida(db_cu36, "ref-e2-multiple")
    proveedor.reembolsos = [
        _refund("re_ajeno", ajuste_id=ajuste.id + 1000, amount=1010),
        _refund("re_otro_importe", ajuste_id=ajuste.id, amount=5000),
        _refund("re_otra_moneda", ajuste_id=ajuste.id, amount=1010, currency="usd"),
        _refund("re_correcto", ajuste_id=ajuste.id, amount=1010),
    ]
    conciliado = service.conciliar_ajuste(
        db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
        _admin(db_cu36), proveedor=proveedor,
    )
    assert conciliado.estado == "CONFIRMADO"
    assert conciliado.referencia_externa == "re_correcto"


def test_conciliacion_rechaza_referencia_de_otro_ajuste(db_cu36):
    """E-2: metadata.ajuste_id de otro ajuste no confirma el nuestro."""
    ajuste, proveedor = _conciliacion_desconocida(db_cu36, "ref-e2-ajeno")
    proveedor.reembolsos = [
        _refund("re_de_otro", ajuste_id=ajuste.id + 77, amount=1010),
    ]
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu36), proveedor=proveedor,
        )
    assert error.value.status_code == 409
    actual = _estado_ajuste(db_cu36, ajuste.id)
    assert actual.estado == "DESCONOCIDO"
    assert actual.referencia_externa is None
    pago = _pago(db_cu36, 900)
    assert pago.estado == "APROBADO"
    assert saldos.monto_vigente_pago(db_cu36, pago) == Decimal("20.20")


def test_conciliacion_rechaza_importe_distinto(db_cu36):
    """E-2: metadata correcta pero importe distinto no confirma."""
    ajuste, proveedor = _conciliacion_desconocida(db_cu36, "ref-e2-importe")
    proveedor.reembolsos = [
        _refund("re_importe", ajuste_id=ajuste.id, amount=1011),
    ]
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu36), proveedor=proveedor,
        )
    assert error.value.status_code == 409
    actual = _estado_ajuste(db_cu36, ajuste.id)
    assert actual.estado == "DESCONOCIDO"
    assert actual.referencia_externa is None
    assert saldos.monto_vigente_pago(db_cu36, _pago(db_cu36, 900)) == (
        Decimal("20.20")
    )


def test_conciliacion_rechaza_moneda_distinta(db_cu36):
    """E-2: metadata e importe correctos con otra moneda no confirman."""
    ajuste, proveedor = _conciliacion_desconocida(db_cu36, "ref-e2-moneda")
    proveedor.reembolsos = [
        _refund("re_moneda", ajuste_id=ajuste.id, amount=1010, currency="usd"),
    ]
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu36), proveedor=proveedor,
        )
    assert error.value.status_code == 409
    actual = _estado_ajuste(db_cu36, ajuste.id)
    assert actual.estado == "DESCONOCIDO"
    assert actual.referencia_externa is None
    assert _pago(db_cu36, 900).estado == "APROBADO"


def test_conciliacion_rechaza_refunds_ambiguos_sin_atribucion(db_cu36):
    """E-2: dos refunds iguales sin metadata.ajuste_id son ambiguos."""
    ajuste, proveedor = _conciliacion_desconocida(db_cu36, "ref-e2-ambiguo")
    proveedor.reembolsos = [
        _refund("re_sin_metadata_1", ajuste_id=None, amount=1010),
        _refund("re_sin_metadata_2", ajuste_id=None, amount=1010),
    ]
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu36), proveedor=proveedor,
        )
    assert error.value.status_code == 409
    actual = _estado_ajuste(db_cu36, ajuste.id)
    assert actual.estado == "DESCONOCIDO"
    assert actual.referencia_externa is None


def test_conciliacion_rechaza_refund_vinculado_inconsistente(db_cu36):
    """E-2: un refund ya vinculado tambien se revalida antes de confirmar."""
    proveedor = ProveedorFalso(refund_estado="pending")
    ajuste = service.reembolsar_pago(
        db_cu36, 900, _devolucion("-20.20", servicio=1100),
        "ref-e2-vinculado", _admin(db_cu36), proveedor=proveedor,
    )
    assert ajuste.referencia_externa == "re_1"
    proveedor.reembolsos[0] = ReembolsoProveedor(
        id="re_1", payment_intent_id="pi_cu36", amount=999,
        currency="bob", status="succeeded",
    )
    with pytest.raises(HTTPException) as error:
        service.conciliar_ajuste(
            db_cu36, ajuste.id, ConciliarAjuste(resultado="CONFIRMADO"),
            _admin(db_cu36), proveedor=proveedor,
        )
    assert error.value.status_code == 409
    assert "no se confirma" in error.value.detail
    actual = _estado_ajuste(db_cu36, ajuste.id)
    assert actual.estado == "PENDIENTE_PROVEEDOR"
    assert _pago(db_cu36, 900).estado == "APROBADO"


# ------------------------------------------------------------- idempotencia
def test_idempotencia_mismo_payload_y_payload_distinto(db_cu36):
    primero = service.corregir_pago(
        db_cu36, 500, _correccion("-5.00"), "idem-cor", _admin(db_cu36),
    )
    segundo = service.corregir_pago(
        db_cu36, 500, _correccion("-5.00"), "idem-cor", _admin(db_cu36),
    )
    assert primero.id == segundo.id
    with pytest.raises(HTTPException) as error:
        service.corregir_pago(
            db_cu36, 500, _correccion("-4.00"), "idem-cor", _admin(db_cu36),
        )
    assert error.value.status_code == 409
    assert db_cu36.scalar(select(func.count()).select_from(PagoAjuste)) == 1


# ------------------------------------------------------------------- cuotas
def test_anular_pago_de_cuota_revierte_cuota_y_plan(db_cu36):
    from app.modules.gestion_pagos.schemas.planes_cuotas import (
        CuotaPlanCrear, PagoCuotaCrear, PlanPagoCrear,
    )
    from app.modules.gestion_pagos.services import planes_cuotas as planes

    datos = PlanPagoCrear(
        paciente_id=1, consulta_clinica_id=100,
        servicio_realizado_ids=[1102],
        cuotas=[
            CuotaPlanCrear(
                numero_cuota=1, monto_programado=Decimal("10.10"),
                fecha_vencimiento=datetime(2026, 11, 15).date(),
            ),
            CuotaPlanCrear(
                numero_cuota=2, monto_programado=Decimal("10.10"),
                fecha_vencimiento=datetime(2026, 12, 15).date(),
            ),
        ],
    )
    plan = planes.crear_plan(db_cu36, datos, "plan-cu36", _admin(db_cu36))
    pago = planes.registrar_pago_manual(
        db_cu36, plan.cuotas[0].id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-cu36-1", _admin(db_cu36),
    )
    assert pago.estado_pago == "APROBADO"
    assert pago.aplicado_a_cuota is True

    service.anular_pago(
        db_cu36, pago.pago_id,
        _anular(evidencia="Pago de cuota registrado por duplicado"),
        "anu-cuota-1", _admin(db_cu36),
    )
    refrescado = planes.consultar_plan(db_cu36, plan.id, _admin(db_cu36))
    assert refrescado.estado == "ACTIVO"
    assert [item.estado for item in refrescado.cuotas] == ["VENCIDA", "PENDIENTE"]
    assert _pago(db_cu36, pago.pago_id).estado == "ANULADO"


def test_reembolso_parcial_de_cuota_bloqueado(db_cu36):
    from app.modules.gestion_pagos.schemas.planes_cuotas import (
        CuotaPlanCrear, PagoCuotaCrear, PlanPagoCrear,
    )
    from app.modules.gestion_pagos.services import planes_cuotas as planes

    datos = PlanPagoCrear(
        paciente_id=1, consulta_clinica_id=100,
        servicio_realizado_ids=[1102],
        cuotas=[CuotaPlanCrear(
            numero_cuota=1, monto_programado=Decimal("20.20"),
            fecha_vencimiento=datetime(2026, 11, 15).date(),
        )],
    )
    plan = planes.crear_plan(db_cu36, datos, "plan-cu36-d4", _admin(db_cu36))
    pago = planes.registrar_pago_manual(
        db_cu36, plan.cuotas[0].id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-cu36-d4", _admin(db_cu36),
    )
    with pytest.raises(HTTPException) as error:
        service.reembolsar_pago(
            db_cu36, pago.pago_id, _devolucion("-5.00", servicio=1102),
            "dev-cuota-parcial", _admin(db_cu36),
        )
    assert error.value.status_code == 409
    assert "parcial" in error.value.detail


# ------------------------------------------------------------ compatibilidad
def test_confirmar_pago_bloqueado_con_ajuste_abierto(db_cu36):
    db_cu36.execute(text(
        "INSERT INTO pago_ajuste (id,pago_id,tipo,naturaleza,efecto_caja,estado,"
        "importe,moneda,motivo,clave_idempotencia,revertir_aplicacion_cuota,"
        "creado_por) VALUES (77,910,'RECONCILIACION','OTRO','SIN_EFECTO',"
        "'PENDIENTE_PROVEEDOR',0,'BOB','x','k-77',0,13)"
    ))
    db_cu36.commit()
    with pytest.raises(HTTPException) as error:
        administrativos.confirmar_pago(db_cu36, 910, _admin(db_cu36))
    assert error.value.status_code == 409


def test_historial_administrativo_expone_importes(db_cu36):
    service.reembolsar_pago(
        db_cu36, 500, _devolucion("-5.00"), "dev-hist", _admin(db_cu36),
    )
    respuesta = administrativos.consultar(db_cu36, 500, _admin(db_cu36))
    assert respuesta.importe_vigente == Decimal("15.20")
    assert respuesta.importe_reembolsado == Decimal("5.00")
    historial = service.historial_ajustes(db_cu36, 500, _admin(db_cu36))
    assert historial.importe_base == Decimal("20.20")
    assert historial.importe_vigente == Decimal("15.20")
    assert len(historial.ajustes) == 1


def test_historial_paciente_expone_importe_vigente(db_cu36):
    service.reembolsar_pago(
        db_cu36, 500, _devolucion("-5.00"), "dev-cu35", _admin(db_cu36),
    )
    paciente = db_cu36.get(Usuario, 11)
    historial = {
        item.pago_id: item for item in pagos_service.listar_mis_pagos(
            db_cu36, paciente,
        )
    }
    assert historial[500].importe_vigente == Decimal("15.20")
    assert historial[500].importe_reembolsado == Decimal("5.00")


def test_roles_no_administrativos_rechazados(db_cu36):
    otro = db_cu36.get(Usuario, 14)
    with pytest.raises(HTTPException) as error:
        service.historial_ajustes(db_cu36, 500, otro)
    assert error.value.status_code == 403
