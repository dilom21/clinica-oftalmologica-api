"""CU32/CU33: planes, cuotas e integracion financiera con CU31."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from tests.test_base_flujo_pagos import cliente_pagos, db_pagos, token
from tests.test_cu31_pagos_administrativos import cliente_cu31, db_cu31
from app.modules.gestion_pagos.models.models import (
    Cuota,
    IntentoPagoCuota,
    NotificacionPago,
    Pago,
    PagoDetalle,
    PlanPago,
    PlanPagoServicio,
)
from app.modules.gestion_pagos.repositories import repository as pago_repo
from app.modules.gestion_pagos.schemas.planes_cuotas import (
    CancelarPlanPago,
    CuotaPlanCrear,
    PagoCuotaCrear,
    PlanPagoCrear,
    RechazarPagoCuota,
)
from app.modules.gestion_pagos.services import planes_cuotas
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_pagos.services.webhook_tenancy import StripeWebhookScope
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario


class ProveedorStripeFalso:
    def __init__(self, *, estado="requires_payment_method"):
        self.estado = estado
        self.creadas = []
        self.intentos = {}

    def validar_configuracion(self, *, webhook=False):
        return None

    def crear_intencion(self, **kwargs):
        self.creadas.append(kwargs)
        intento = SimpleNamespace(
            id=f"pi_cuota_{len(self.creadas)}",
            client_secret="secret_test",
            amount=kwargs["amount"],
            currency=kwargs["currency"],
            status=self.estado,
        )
        self.intentos[intento.id] = intento
        return intento

    def recuperar_intencion(self, payment_intent_id):
        return self.intentos[payment_intent_id]

    def cancelar_intencion(self, payment_intent_id):
        self.intentos[payment_intent_id].status = "canceled"
        return self.intentos[payment_intent_id]

    def verificar_webhook(self, payload, firma):
        raise AssertionError("no corresponde")


@pytest.fixture
def db_cu32(db_cu31):
    db_cu31.execute(text(
        "INSERT INTO funcion VALUES "
        "(3,1,'Gestionar planes de pago',NULL,1),"
        "(4,1,'Gestionar cuotas',NULL,1)"
    ))
    db_cu31.execute(text(
        "INSERT INTO rol_funcion VALUES "
        "(5,2,3,3,NULL),(6,2,4,3,NULL),"
        "(7,3,3,3,NULL),(8,3,4,3,NULL)"
    ))
    db_cu31.execute(text(
        "INSERT INTO servicio_realizado "
        "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
        "fecha_realizacion,observaciones,estado,precio_aplicado) VALUES "
        "(2001,1,1,100,5,'2026-10-20',NULL,1,100.00),"
        "(2002,1,1,100,5,'2026-10-20',NULL,1,200.00),"
        "(2100,1,2,200,5,'2026-10-20',NULL,1,60.00)"
    ))
    db_cu31.commit()
    return db_cu31


@pytest.fixture
def cliente_cu32(cliente_cu31, db_cu32):
    return cliente_cu31


def _admin(db):
    return db.get(Usuario, 13)


def _paciente(db):
    return db.get(Usuario, 11)


def _datos_plan(servicios=(2001, 2002), cuotas=("100", "100", "100")):
    vencimientos = [date(2026, 11, 15), date(2026, 12, 15), date(2027, 1, 15)]
    return PlanPagoCrear(
        paciente_id=1,
        consulta_clinica_id=100,
        servicio_realizado_ids=list(servicios),
        cuotas=[
            CuotaPlanCrear(
                numero_cuota=indice,
                monto_programado=monto,
                fecha_vencimiento=vencimientos[indice - 1],
            )
            for indice, monto in enumerate(cuotas, 1)
        ],
        observaciones="Acuerdo de prueba",
    )


def _crear_plan(db, clave="plan-cu32-001"):
    return planes_cuotas.crear_plan(
        db, _datos_plan(), clave, _admin(db), "127.0.0.1",
    )


def test_modelos_mapean_cuatro_tablas_y_restricciones():
    assert PlanPago.__table__.name == "plan_pago"
    assert PlanPagoServicio.__table__.name == "plan_pago_servicio"
    assert Cuota.__table__.name == "cuota"
    assert IntentoPagoCuota.__table__.name == "intento_pago_cuota"
    assert {item.name for item in PlanPago.__table__.constraints} >= {
        "uq_plan_pago_clave_idempotencia", "ck_plan_pago_estado",
    }


def test_schema_exige_cronograma_consecutivo_y_total_no_controlado_por_cliente():
    with pytest.raises(ValueError):
        PlanPagoCrear(
            paciente_id=1, consulta_clinica_id=100,
            servicio_realizado_ids=[2001],
            cuotas=[CuotaPlanCrear(
                numero_cuota=2, monto_programado="100", fecha_vencimiento="2026-11-01",
            )],
            monto_total="0.01",
        )


def test_crear_plan_atomico_totales_reservas_e_idempotencia(db_cu32):
    primero = _crear_plan(db_cu32)
    segundo = _crear_plan(db_cu32)
    assert primero.id == segundo.id
    assert primero.monto_total == Decimal("300.00")
    assert primero.cantidad_cuotas == 3
    assert [item.monto_programado for item in primero.cuotas] == [
        Decimal("100.00"), Decimal("100.00"), Decimal("100.00"),
    ]
    assert db_cu32.scalar(select(func.count()).select_from(PlanPago)) == 1
    assert db_cu32.scalar(select(func.count()).select_from(PlanPagoServicio)) == 2


def test_plan_misma_clave_otro_payload_rechaza(db_cu32):
    _crear_plan(db_cu32, "plan-idem-conflict")
    datos = _datos_plan()
    datos.observaciones = "Otro acuerdo"
    with pytest.raises(HTTPException) as error:
        planes_cuotas.crear_plan(
            db_cu32, datos, "plan-idem-conflict", _admin(db_cu32),
        )
    assert error.value.status_code == 409


def test_fallo_bitacora_revierte_plan_servicios_y_cuotas(db_cu32, monkeypatch):
    def fallar(*args, **kwargs):
        raise RuntimeError("bitacora no disponible")

    monkeypatch.setattr(planes_cuotas, "registrar_bitacora", fallar)
    with pytest.raises(RuntimeError, match="bitacora"):
        _crear_plan(db_cu32, "plan-rollback")
    assert db_cu32.scalar(select(func.count()).select_from(PlanPago)) == 0
    assert db_cu32.scalar(select(func.count()).select_from(PlanPagoServicio)) == 0
    assert db_cu32.scalar(select(func.count()).select_from(Cuota)) == 0


def test_plan_rechaza_consulta_otro_paciente_y_total_inexacto(db_cu32):
    with pytest.raises(HTTPException) as error:
        planes_cuotas.crear_plan(
            db_cu32,
            _datos_plan(servicios=(2001, 2100), cuotas=("160",)),
            "plan-mixto", _admin(db_cu32),
        )
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error_total:
        planes_cuotas.crear_plan(
            db_cu32, _datos_plan(cuotas=("299.99",)),
            "plan-total-inexacto", _admin(db_cu32),
        )
    assert error_total.value.status_code == 422


def test_reserva_plan_bloquea_cobro_normal_cu31(db_cu32):
    _crear_plan(db_cu32)
    with pytest.raises(HTTPException) as error:
        pagos_service.validar_seleccion_servicios(db_cu32, [2001])
    assert error.value.status_code == 409
    assert "plan" in error.value.detail.lower()
    assert 2001 not in {
        item.id for item in pago_repo.listar_servicios_pendientes_pago(
            db_cu32, paciente_id=1, consulta_clinica_id=100,
        )
    }


def test_tres_cuotas_efectivo_asignacion_determinista_y_completado(db_cu32):
    plan = _crear_plan(db_cu32)
    for indice, cuota in enumerate(plan.cuotas, 1):
        respuesta = planes_cuotas.registrar_pago_manual(
            db_cu32, cuota.id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
            f"cuota-cash-{indice}", _admin(db_cu32),
        )
        assert respuesta.estado_pago == "APROBADO"
        assert respuesta.aplicado_a_cuota is True
    final = planes_cuotas.consultar_plan(db_cu32, plan.id, _admin(db_cu32))
    assert final.estado == "COMPLETADO"
    assert final.saldo_pendiente == Decimal("0.00")
    detalles = db_cu32.execute(
        select(PagoDetalle.servicio_realizado_id, func.sum(PagoDetalle.monto_aplicado))
        .join(Pago, Pago.id == PagoDetalle.pago_id)
        .where(Pago.estado == "APROBADO", PagoDetalle.servicio_realizado_id.in_([2001, 2002]))
        .group_by(PagoDetalle.servicio_realizado_id)
        .order_by(PagoDetalle.servicio_realizado_id)
    ).all()
    assert detalles == [(2001, Decimal("100.00")), (2002, Decimal("200.00"))]
    assert db_cu32.scalar(select(func.count()).select_from(NotificacionPago)) == 3


def test_servicio_no_se_considera_saldado_con_primera_cuota_parcial(db_cu32):
    plan = _crear_plan(db_cu32)
    planes_cuotas.registrar_pago_manual(
        db_cu32, plan.cuotas[0].id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-parcial-1", _admin(db_cu32),
    )
    assert pago_repo.servicio_cubierto_por_pago_aprobado(db_cu32, 2001) is True
    assert pago_repo.servicio_cubierto_por_pago_aprobado(db_cu32, 2002) is False


def test_qr_pendiente_no_liquida_confirmacion_unica_si(db_cu32):
    plan = _crear_plan(db_cu32)
    cuota = plan.cuotas[0]
    pendiente = planes_cuotas.registrar_pago_manual(
        db_cu32, cuota.id,
        PagoCuotaCrear(metodo_pago="QR", referencia_bancaria="QR-CUOTA-1"),
        "cuota-qr-001", _admin(db_cu32),
    )
    assert pendiente.estado_pago == "PENDIENTE"
    assert pendiente.estado_cuota in {"PENDIENTE", "VENCIDA"}
    assert db_cu32.scalar(select(func.count()).select_from(NotificacionPago)) == 0
    confirmado = planes_cuotas.confirmar_pago_manual(
        db_cu32, cuota.id, pendiente.pago_id, _admin(db_cu32),
    )
    repetido = planes_cuotas.confirmar_pago_manual(
        db_cu32, cuota.id, pendiente.pago_id, _admin(db_cu32),
    )
    assert confirmado.estado_cuota == repetido.estado_cuota == "PAGADA"
    assert db_cu32.scalar(select(func.count()).select_from(NotificacionPago)) == 1


def test_rechazar_transferencia_libera_cuota_para_nuevo_intento(db_cu32):
    plan = _crear_plan(db_cu32)
    cuota = plan.cuotas[0]
    pendiente = planes_cuotas.registrar_pago_manual(
        db_cu32, cuota.id,
        PagoCuotaCrear(metodo_pago="TRANSFERENCIA", referencia_bancaria="TR-CUOTA-1"),
        "cuota-tr-001", _admin(db_cu32),
    )
    rechazado = planes_cuotas.rechazar_pago_manual(
        db_cu32, cuota.id, pendiente.pago_id,
        RechazarPagoCuota(motivo="No se recibio el abono"), _admin(db_cu32),
    )
    assert rechazado.estado_pago == "RECHAZADO"
    efectivo = planes_cuotas.registrar_pago_manual(
        db_cu32, cuota.id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-cash-after-reject", _admin(db_cu32),
    )
    assert efectivo.estado_cuota == "PAGADA"


def test_cancelacion_libera_solo_plan_sin_cobros(db_cu32):
    plan = _crear_plan(db_cu32)
    cancelado = planes_cuotas.cancelar_plan(
        db_cu32, plan.id, CancelarPlanPago(motivo="Acuerdo revocado"), _admin(db_cu32),
    )
    assert cancelado.estado == "CANCELADO"
    assert all(item.estado == "CANCELADA" for item in cancelado.cuotas)
    assert all(item.activo is False for item in cancelado.servicios)
    pagos_service.validar_seleccion_servicios(db_cu32, [2001])


def test_cancelacion_bloqueada_despues_de_cobro(db_cu32):
    plan = _crear_plan(db_cu32)
    planes_cuotas.registrar_pago_manual(
        db_cu32, plan.cuotas[0].id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-before-cancel", _admin(db_cu32),
    )
    with pytest.raises(HTTPException) as error:
        planes_cuotas.cancelar_plan(
            db_cu32, plan.id, CancelarPlanPago(motivo="No permitido"), _admin(db_cu32),
        )
    assert error.value.status_code == 409


def test_cancelacion_bloqueada_con_transferencia_pendiente(db_cu32):
    plan = _crear_plan(db_cu32)
    planes_cuotas.registrar_pago_manual(
        db_cu32, plan.cuotas[0].id,
        PagoCuotaCrear(metodo_pago="TRANSFERENCIA", referencia_bancaria="TR-PENDIENTE"),
        "cuota-pendiente-cancel", _admin(db_cu32),
    )
    with pytest.raises(HTTPException) as error:
        planes_cuotas.cancelar_plan(
            db_cu32, plan.id, CancelarPlanPago(motivo="Intento de cancelar"), _admin(db_cu32),
        )
    assert error.value.status_code == 409


def test_paciente_solo_consulta_y_paga_sus_cuotas(db_cu32):
    plan = _crear_plan(db_cu32)
    propio = planes_cuotas.consultar_mi_plan(db_cu32, plan.id, _paciente(db_cu32))
    assert propio.paciente_id == 1
    with pytest.raises(HTTPException) as error:
        planes_cuotas.crear_plan(
            db_cu32, _datos_plan(), "paciente-no-crea", _paciente(db_cu32),
        )
    assert error.value.status_code == 403


def test_stripe_cuota_crea_intento_y_webhook_aplica_una_vez(db_cu32):
    plan = _crear_plan(db_cu32)
    cuota = plan.cuotas[0]
    proveedor = ProveedorStripeFalso()
    respuesta = planes_cuotas.crear_intencion_stripe(
        db_cu32, cuota.id, _paciente(db_cu32), proveedor,
    )
    assert respuesta.monto == Decimal("100.00")
    metadata = proveedor.creadas[0]["metadata"]
    assert metadata["cuota_id"] == str(cuota.id)
    assert metadata["plan_pago_id"] == str(plan.id)
    evento = {
        "type": "payment_intent.succeeded",
        "created": 1794000000,
        "data": {"object": {
            "id": respuesta.payment_intent_id,
            "amount": 10000,
            "currency": "bob",
            "metadata": metadata,
        }},
    }
    scope = StripeWebhookScope(is_tenant=False, metadata=metadata)
    primero = pagos_service.procesar_evento_webhook_stripe(db_cu32, evento, scope)
    segundo = pagos_service.procesar_evento_webhook_stripe(db_cu32, evento, scope)
    assert primero.estado_pago == segundo.estado_pago == "APROBADO"
    assert db_cu32.scalar(select(func.count()).select_from(IntentoPagoCuota).where(
        IntentoPagoCuota.aplicado_a_cuota.is_(True),
    )) == 1
    assert db_cu32.scalar(select(func.count()).select_from(NotificacionPago)) == 1


def test_webhook_cuota_rechaza_metadata_inconsistente(db_cu32):
    plan = _crear_plan(db_cu32)
    cuota = plan.cuotas[0]
    proveedor = ProveedorStripeFalso()
    respuesta = planes_cuotas.crear_intencion_stripe(
        db_cu32, cuota.id, _paciente(db_cu32), proveedor,
    )
    metadata = dict(proveedor.creadas[0]["metadata"])
    metadata["cuota_id"] = str(cuota.id + 999)
    evento = {
        "type": "payment_intent.succeeded",
        "data": {"object": {
            "id": respuesta.payment_intent_id,
            "amount": 10000, "currency": "bob", "metadata": metadata,
        }},
    }
    with pytest.raises(HTTPException) as error:
        pagos_service.procesar_evento_webhook_stripe(
            db_cu32, evento, StripeWebhookScope(is_tenant=False, metadata=metadata),
        )
    assert error.value.status_code == 409
    assert db_cu32.get(Pago, respuesta.pago_id).estado == "PENDIENTE"
    assert db_cu32.get(Cuota, cuota.id).estado == "PENDIENTE"


def test_segundo_cargo_stripe_real_se_preserva_y_marca_conciliacion(db_cu32):
    plan = _crear_plan(db_cu32)
    cuota = plan.cuotas[0]
    planes_cuotas.registrar_pago_manual(
        db_cu32, cuota.id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
        "cuota-primera-aplicada", _admin(db_cu32),
    )
    duplicado = Pago(
        monto=Decimal("100.00"), moneda="BOB", metodo_pago="TARJETA",
        estado="PENDIENTE", pasarela="STRIPE",
        referencia_transaccion="pi_duplicate_real",
        clave_idempotencia="duplicate-real-charge",
    )
    db_cu32.add(duplicado)
    db_cu32.flush()
    db_cu32.add(PagoDetalle(
        pago_id=duplicado.id, servicio_realizado_id=2001,
        monto_aplicado=Decimal("100.00"),
    ))
    db_cu32.add(IntentoPagoCuota(
        cuota_id=cuota.id, pago_id=duplicado.id, aplicado_a_cuota=False,
    ))
    db_cu32.commit()
    metadata = {
        "pago_id": str(duplicado.id), "consulta_clinica_id": "100",
        "plan_pago_id": str(plan.id), "cuota_id": str(cuota.id),
    }
    resultado = pagos_service.procesar_evento_webhook_stripe(
        db_cu32,
        {"type": "payment_intent.succeeded", "data": {"object": {
            "id": "pi_duplicate_real", "amount": 10000,
            "currency": "bob", "metadata": metadata,
        }}},
        StripeWebhookScope(is_tenant=False, metadata=metadata),
    )
    assert resultado.estado_pago == "APROBADO"
    intento = db_cu32.scalar(select(IntentoPagoCuota).where(
        IntentoPagoCuota.pago_id == duplicado.id,
    ))
    assert intento.aplicado_a_cuota is False
    assert db_cu32.get(Cuota, cuota.id).estado == "PAGADA"
    assert db_cu32.scalar(select(func.count()).select_from(Bitacora).where(
        Bitacora.accion == "CONFLICTO_DOBLE_COBRO_CUOTA_STRIPE",
    )) == 1


def test_endpoints_openapi_y_rbac(cliente_cu32, db_cu32):
    respuesta = cliente_cu32.post(
        "/pagos/planes",
        headers={**token(13, 2), "Idempotency-Key": "http-plan-001"},
        json=_datos_plan().model_dump(mode="json"),
    )
    assert respuesta.status_code == 201
    plan_id = respuesta.json()["id"]
    assert cliente_cu32.get(
        f"/pagos/mis-planes/{plan_id}", headers=token(11),
    ).status_code == 200
    assert cliente_cu32.post(
        f"/pagos/planes/{plan_id}/cancelar",
        headers=token(11), json={"motivo": "No autorizado"},
    ).status_code == 403

