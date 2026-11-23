from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.test_cu31_pagos_administrativos import cliente_cu31, db_cu31, _admin
from tests.test_base_flujo_pagos import cliente_pagos, db_pagos
from tests.test_clinical_request_routing import auth, routed_client
from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import crear_tenant_access_token
from app.modules.gestion_pagos.models.models import (
    Cuota, IntentoPagoCuota, NotificacionPago, Pago, PagoDetalle, PlanPago,
    PlanPagoServicio,
)
from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_pagos.services import estado_cuenta


@pytest.fixture
def db_cu34(db_cu31):
    db_cu31.execute(text(
        "INSERT INTO funcion VALUES "
        "(3,1,'Gestionar planes de pago',NULL,1),"
        "(4,1,'Gestionar cuotas',NULL,1)"
    ))
    db_cu31.execute(text(
        "INSERT INTO rol_funcion VALUES (5,2,3,3,NULL),(6,2,4,3,NULL)"
    ))
    db_cu31.execute(text(
        "INSERT INTO servicio_realizado "
        "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
        "fecha_realizacion,observaciones,estado,precio_aplicado) VALUES "
        "(3401,1,1,100,5,'2026-10-20',NULL,1,100.00),"
        "(3402,1,1,100,5,'2026-10-21',NULL,1,50.00)"
    ))
    db_cu31.commit()
    return db_cu31


def _estado(db):
    return estado_cuenta.consultar_estado_cuenta(db, 1, _admin(db))


def _limpiar_pagos(db):
    db.execute(text("DELETE FROM pago_detalle"))
    db.execute(text("DELETE FROM pago"))
    db.commit()


def _insertar_pago(db, pago_id, monto, estado="APROBADO", *, pasarela=None):
    db.execute(text(
        "INSERT INTO pago (id,monto,moneda,metodo_pago,estado,pasarela) "
        "VALUES (:id,:monto,'BOB','TARJETA',:estado,:pasarela)"
    ), {"id": pago_id, "monto": monto, "estado": estado, "pasarela": pasarela})


def _insertar_detalle(db, detalle_id, pago_id, servicio_id, monto):
    db.execute(text(
        "INSERT INTO pago_detalle VALUES (:id,:pago,:servicio,:monto)"
    ), {"id": detalle_id, "pago": pago_id, "servicio": servicio_id, "monto": monto})


def test_estado_cuenta_sin_pagos_y_sin_planes(db_cu34):
    _limpiar_pagos(db_cu34)
    resultado = _estado(db_cu34)
    assert resultado.resumen.importe_total_servicios == Decimal("180.30")
    assert resultado.resumen.total_pagos_aprobados_aplicados == Decimal("0.00")
    assert resultado.resumen.saldo_total_pendiente == Decimal("180.30")
    assert resultado.resumen.saldo_servicios_no_financiados == Decimal("180.30")
    assert all(item.servicio_realizado_id != 1007 for item in resultado.servicios)


def test_paciente_sin_servicios_ni_pagos(db_cu34):
    _limpiar_pagos(db_cu34)
    db_cu34.execute(text("DELETE FROM servicio_realizado WHERE paciente_id=1"))
    db_cu34.commit()
    resultado = _estado(db_cu34)
    assert resultado.servicios == []
    assert resultado.pagos_pendientes_confirmacion == []
    assert resultado.resumen.importe_total_servicios == Decimal("0.00")
    assert resultado.resumen.saldo_total_pendiente == Decimal("0.00")


def test_pago_distribuido_y_pagos_multiples_en_un_servicio(db_cu34):
    _limpiar_pagos(db_cu34)
    _insertar_pago(db_cu34, 3501, "20.00")
    _insertar_detalle(db_cu34, 1, 3501, 1000, "5.00")
    _insertar_detalle(db_cu34, 2, 3501, 1001, "15.00")
    _insertar_pago(db_cu34, 3502, "5.10")
    _insertar_detalle(db_cu34, 3, 3502, 1000, "5.10")
    db_cu34.commit()
    resultado = _estado(db_cu34)
    por_id = {item.servicio_realizado_id: item for item in resultado.servicios}
    assert por_id[1000].monto_pagado_aplicado == Decimal("10.10")
    assert por_id[1000].saldo_pendiente == Decimal("0.00")
    assert por_id[1001].monto_pagado_aplicado == Decimal("15.00")
    assert resultado.resumen.total_pagos_aprobados_aplicados == Decimal("25.10")
    assert resultado.resumen.saldo_total_pendiente == Decimal("155.20")


def test_pago_completo_elimina_saldo_y_resumen_reconcilia_servicios(db_cu34):
    _limpiar_pagos(db_cu34)
    for ix, (sid, amount) in enumerate(((1000, "10.10"), (1001, "20.20"),
                                        (3401, "100.00"), (3402, "50.00")), 1):
        _insertar_pago(db_cu34, 3600 + ix, amount)
        _insertar_detalle(db_cu34, ix, 3600 + ix, sid, amount)
    db_cu34.commit()
    resultado = _estado(db_cu34)
    assert resultado.resumen.saldo_total_pendiente == Decimal("0.00")
    assert resultado.resumen.total_pagos_aprobados_aplicados == Decimal("180.30")
    assert sum((s.precio_aplicado for s in resultado.servicios), Decimal("0")) == resultado.resumen.importe_total_servicios
    assert sum((s.saldo_pendiente for s in resultado.servicios), Decimal("0")) == resultado.resumen.saldo_total_pendiente


def test_pagos_no_aprobados_no_reducen_saldo(db_cu34):
    for pago_id, estado_pago in ((3401, "PENDIENTE"), (3402, "RECHAZADO"),
                                 (3403, "ANULADO"), (3404, "REEMBOLSADO")):
        db_cu34.execute(text(
            "INSERT INTO pago (id,monto,moneda,metodo_pago,estado) "
            "VALUES (:id,10,'BOB','TARJETA',:estado)"
        ), {"id": pago_id, "estado": estado_pago})
        db_cu34.execute(text(
            "INSERT INTO pago_detalle VALUES (:id,:pago,3401,10)"
        ), {"id": pago_id, "pago": pago_id})
    db_cu34.commit()
    resultado = _estado(db_cu34)
    servicio = next(x for x in resultado.servicios if x.servicio_realizado_id == 3401)
    assert servicio.monto_pagado_aplicado == Decimal("0.00")
    assert servicio.saldo_pendiente == Decimal("100.00")
    assert {item.pago_id for item in resultado.pagos_pendientes_confirmacion} == {501, 3401}


def test_saldo_plan_no_se_suma_dos_veces_y_cuota_vencida(db_cu34, monkeypatch):
    db_cu34.execute(text(
        "INSERT INTO plan_pago VALUES (3401,1,100,100, 'BOB',2,"
        "'2026-10-01', 'ACTIVO',NULL,13,'plan-3401')"
    ))
    db_cu34.execute(text(
        "INSERT INTO plan_pago_servicio VALUES (3401,3401,100,1)"
    ))
    db_cu34.execute(text(
        "INSERT INTO cuota VALUES "
        "(3401,3401,1,40,'2026-10-15','PENDIENTE'),"
        "(3402,3401,2,60,'2026-12-15','PENDIENTE')"
    ))
    db_cu34.commit()
    monkeypatch.setattr("app.modules.gestion_pagos.services.estado_cuenta.fecha_local_aplicacion",
                        lambda: date(2026, 11, 20))
    resultado = _estado(db_cu34)
    assert resultado.resumen.saldo_planes_pago == Decimal("100.00")
    assert resultado.resumen.saldo_total_pendiente == Decimal("160.10")
    assert resultado.resumen.importe_cuotas_vencidas == Decimal("40.00")
    assert resultado.resumen.cantidad_cuotas_vencidas == 1
    assert resultado.resumen.cantidad_cuotas_pendientes == 1


def test_varios_planes_activos_suman_saldos_sin_duplicar_servicios(db_cu34):
    _limpiar_pagos(db_cu34)
    for plan_id, servicio_id, monto in ((3701, 3401, "100"), (3702, 3402, "50")):
        db_cu34.execute(text(
            "INSERT INTO plan_pago VALUES (:id,1,100,:monto,'BOB',1,"
            "'2026-10-01','ACTIVO',NULL,13,:clave)"
        ), {"id": plan_id, "monto": monto, "clave": f"plan-{plan_id}"})
        db_cu34.execute(text(
            "INSERT INTO plan_pago_servicio VALUES (:plan,:servicio,:monto,1)"
        ), {"plan": plan_id, "servicio": servicio_id, "monto": monto})
        db_cu34.execute(text(
            "INSERT INTO cuota VALUES (:id,:plan,1,:monto,'2026-12-15','PENDIENTE')"
        ), {"id": plan_id, "plan": plan_id, "monto": monto})
    db_cu34.commit()
    resultado = _estado(db_cu34)
    assert resultado.resumen.cantidad_planes_activos == 2
    assert resultado.resumen.saldo_planes_pago == Decimal("150.00")
    assert resultado.resumen.saldo_servicios_no_financiados == Decimal("30.30")
    assert resultado.resumen.saldo_total_pendiente == Decimal("180.30")


def test_plan_cancelado_y_cuotas_canceladas_no_borran_deuda_servicio(db_cu34):
    _limpiar_pagos(db_cu34)
    db_cu34.execute(text(
        "INSERT INTO plan_pago VALUES (3801,1,100,100,'BOB',1,"
        "'2026-10-01','CANCELADO',NULL,13,'plan-cancelado')"
    ))
    db_cu34.execute(text(
        "INSERT INTO plan_pago_servicio VALUES (3801,3401,100,0)"
    ))
    db_cu34.execute(text(
        "INSERT INTO cuota VALUES (3801,3801,1,100,'2026-10-15','CANCELADA')"
    ))
    db_cu34.commit()
    resultado = _estado(db_cu34)
    assert resultado.resumen.cantidad_planes_activos == 0
    assert resultado.resumen.saldo_planes_pago == Decimal("0.00")
    assert resultado.resumen.saldo_servicios_no_financiados == Decimal("180.30")
    assert resultado.resumen.saldo_total_pendiente == Decimal("180.30")
    assert resultado.planes_pago[0].cuotas[0].estado == "CANCELADA"


def test_plan_parcialmente_pagado_cuota_pagada_y_saldo_coherente(db_cu34):
    _limpiar_pagos(db_cu34)
    db_cu34.execute(text(
        "INSERT INTO plan_pago VALUES (3901,1,100,100,'BOB',2,"
        "'2026-10-01','ACTIVO',NULL,13,'plan-parcial')"
    ))
    db_cu34.execute(text("INSERT INTO plan_pago_servicio VALUES (3901,3401,100,1)"))
    db_cu34.execute(text(
        "INSERT INTO cuota VALUES (3901,3901,1,40,'2026-10-15','PAGADA'),"
        "(3902,3901,2,60,'2026-12-15','PENDIENTE')"
    ))
    _insertar_pago(db_cu34, 3901, "40.00")
    _insertar_detalle(db_cu34, 3901, 3901, 3401, "40.00")
    db_cu34.execute(text(
        "INSERT INTO intento_pago_cuota (id,cuota_id,pago_id,aplicado_a_cuota) "
        "VALUES (3901,3901,3901,1)"
    ))
    db_cu34.commit()
    resultado = _estado(db_cu34)
    assert resultado.resumen.total_pagos_aprobados_aplicados == Decimal("40.00")
    assert resultado.resumen.saldo_planes_pago == Decimal("60.00")
    assert resultado.resumen.saldo_total_pendiente == Decimal("140.30")
    assert resultado.resumen.cantidad_cuotas_pagadas == 1
    assert resultado.resumen.cantidad_cuotas_pendientes == 1


def test_plan_completado_totalmente_pagado_no_conserva_saldo(db_cu34):
    _limpiar_pagos(db_cu34)
    db_cu34.execute(text(
        "INSERT INTO plan_pago VALUES (3951,1,100,100,'BOB',1,"
        "'2026-10-01','COMPLETADO',NULL,13,'plan-completo')"
    ))
    db_cu34.execute(text("INSERT INTO plan_pago_servicio VALUES (3951,3401,100,1)"))
    db_cu34.execute(text(
        "INSERT INTO cuota VALUES (3951,3951,1,100,'2026-10-15','PAGADA')"
    ))
    _insertar_pago(db_cu34, 3951, "100.00")
    _insertar_detalle(db_cu34, 3951, 3951, 3401, "100.00")
    db_cu34.execute(text(
        "INSERT INTO intento_pago_cuota (id,cuota_id,pago_id,aplicado_a_cuota) "
        "VALUES (3951,3951,3951,1)"
    ))
    db_cu34.commit()
    resultado = _estado(db_cu34)
    assert resultado.resumen.cantidad_planes_activos == 0
    assert resultado.resumen.saldo_planes_pago == Decimal("0.00")
    assert resultado.resumen.saldo_total_pendiente == Decimal("80.30")
    assert resultado.planes_pago[0].estado == "COMPLETADO"


def test_distribucion_inconsistente_del_pago_retorna_409(db_cu34):
    _limpiar_pagos(db_cu34)
    _insertar_pago(db_cu34, 4151, "9.99")
    _insertar_detalle(db_cu34, 4151, 4151, 1000, "10.00")
    db_cu34.commit()
    with pytest.raises(HTTPException) as exc:
        _estado(db_cu34)
    assert exc.value.status_code == 409


def test_stripe_aprobado_no_aplicado_se_expone_para_reconciliacion(db_cu34):
    _limpiar_pagos(db_cu34)
    db_cu34.execute(text(
        "INSERT INTO plan_pago VALUES (4001,1,100,100,'BOB',1,"
        "'2026-10-01','ACTIVO',NULL,13,'plan-stripe-conflicto')"
    ))
    db_cu34.execute(text("INSERT INTO plan_pago_servicio VALUES (4001,3401,100,1)"))
    db_cu34.execute(text(
        "INSERT INTO cuota VALUES (4001,4001,1,100,'2026-12-15','PENDIENTE')"
    ))
    _insertar_pago(db_cu34, 4001, "40.00", pasarela="STRIPE")
    _insertar_detalle(db_cu34, 4001, 4001, 3401, "40.00")
    db_cu34.execute(text(
        "INSERT INTO intento_pago_cuota (id,cuota_id,pago_id,aplicado_a_cuota) "
        "VALUES (4001,4001,4001,0)"
    ))
    db_cu34.execute(text("UPDATE pago SET estado='APROBADO' WHERE id=4001"))
    db_cu34.commit()
    resultado = _estado(db_cu34)
    item = next(x for x in resultado.pagos_pendientes_confirmacion if x.pago_id == 4001)
    servicio = next(x for x in resultado.servicios if x.servicio_realizado_id == 3401)
    assert item.requiere_reconciliacion is True
    assert item.aplicado_a_obligacion is False
    assert servicio.monto_pagado_aplicado == Decimal("0.00")
    assert resultado.resumen.saldo_total_pendiente == Decimal("180.30")


def test_pago_aprobado_excesivo_y_moneda_incompatible_retorna_409(db_cu34):
    _limpiar_pagos(db_cu34)
    _insertar_pago(db_cu34, 4101, "10.11")
    _insertar_detalle(db_cu34, 4101, 4101, 1000, "10.11")
    db_cu34.commit()
    with pytest.raises(HTTPException) as exc:
        _estado(db_cu34)
    assert exc.value.status_code == 409
    assert "10.11" not in exc.value.detail

    _limpiar_pagos(db_cu34)
    db_cu34.execute(text(
        "INSERT INTO pago (id,monto,moneda,metodo_pago,estado) "
        "VALUES (4102,1,'USD','TARJETA','PENDIENTE')"
    ))
    _insertar_detalle(db_cu34, 4102, 4102, 1000, "1.00")
    db_cu34.commit()
    with pytest.raises(HTTPException) as exc:
        _estado(db_cu34)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("function_id", [1, 3, 4])
def test_endpoint_exige_permiso_lectura_de_cada_dominio(
    cliente_cu31, db_cu34, function_id,
):
    tenant_token = crear_tenant_access_token(
        usuario_id=13, rol_id=2, tenant_id=1, empresa_id=1,
        empresa_codigo="TEST-CU34",
    )
    db_cu34.execute(text(
        "DELETE FROM rol_funcion WHERE rol_id=2 AND funcion_id=:id"
    ), {"id": function_id})
    db_cu34.commit()
    response = cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1",
        headers={"Authorization": f"Bearer {tenant_token}"},
    )
    assert response.status_code == 403


def test_endpoint_paciente_inexistente_e_id_invalido(cliente_cu31, db_cu34):
    tenant_token = crear_tenant_access_token(
        usuario_id=13, rol_id=2, tenant_id=1, empresa_id=1,
        empresa_codigo="TEST-CU34",
    )
    headers = {"Authorization": f"Bearer {tenant_token}"}
    assert cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/99999", headers=headers,
    ).status_code == 404
    assert cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/0", headers=headers,
    ).status_code == 422


def test_estado_cuenta_lee_tenant_autenticado_con_ids_coincidentes(routed_client):
    client, engines, _, registry = routed_client
    for code, price in (("ALPHA", "10.00"), ("BETA", "90.00")):
        with engines[code].begin() as conn:
            ddls = [
                "CREATE TABLE historial_clinico (id INTEGER PRIMARY KEY, paciente_id INTEGER, fecha_apertura TIMESTAMP, observaciones_generales TEXT, estado BOOLEAN)",
                "CREATE TABLE oftalmologo (id INTEGER PRIMARY KEY, usuario_id INTEGER, matricula TEXT, nombres TEXT, apellidos TEXT, especialidad TEXT, estado BOOLEAN, fecha_registro TIMESTAMP)",
                "CREATE TABLE consulta_clinica (id INTEGER PRIMARY KEY, historial_clinico_id INTEGER, cita_id INTEGER, oftalmologo_id INTEGER, fecha_consulta TIMESTAMP, motivo_consulta TEXT, anamnesis TEXT, observaciones TEXT, estado BOOLEAN)",
                "CREATE TABLE servicio_oftalmologico (id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, precio NUMERIC, duracion INTEGER, estado BOOLEAN)",
                "CREATE TABLE servicio_realizado (id INTEGER PRIMARY KEY, servicio_id INTEGER, paciente_id INTEGER, consulta_clinica_id INTEGER, oftalmologo_id INTEGER, fecha_realizacion TIMESTAMP, observaciones TEXT, estado BOOLEAN, precio_aplicado NUMERIC)",
                "CREATE TABLE pago (id INTEGER PRIMARY KEY, fecha_creacion TIMESTAMP, fecha_hora_pago TIMESTAMP, monto NUMERIC, moneda TEXT, metodo_pago TEXT, estado TEXT, pasarela TEXT, referencia_transaccion TEXT, observaciones TEXT, clave_idempotencia TEXT)",
                "CREATE TABLE pago_detalle (id INTEGER PRIMARY KEY, pago_id INTEGER, servicio_realizado_id INTEGER, monto_aplicado NUMERIC)",
                "CREATE TABLE plan_pago (id INTEGER PRIMARY KEY, paciente_id INTEGER, consulta_clinica_id INTEGER, monto_total NUMERIC, moneda TEXT, cantidad_cuotas INTEGER, fecha_creacion TIMESTAMP, estado TEXT, observaciones TEXT, creado_por INTEGER, clave_idempotencia TEXT)",
                "CREATE TABLE plan_pago_servicio (plan_pago_id INTEGER, servicio_realizado_id INTEGER, monto_financiado NUMERIC, activo BOOLEAN)",
                "CREATE TABLE cuota (id INTEGER PRIMARY KEY, plan_pago_id INTEGER, numero_cuota INTEGER, monto_programado NUMERIC, fecha_vencimiento DATE, estado TEXT)",
                "CREATE TABLE intento_pago_cuota (id INTEGER PRIMARY KEY, cuota_id INTEGER, pago_id INTEGER, fecha_creacion TIMESTAMP, aplicado_a_cuota BOOLEAN)",
            ]
            for ddl in ddls:
                conn.exec_driver_sql(ddl)
            conn.exec_driver_sql(
                "INSERT INTO modulo VALUES (2,'Pagos',NULL,1)"
            )
            conn.exec_driver_sql(
                "INSERT INTO funcion (id,modulo_id,nombre,estado) VALUES "
                "(2,2,'Gestionar pagos',1),(3,2,'Gestionar planes de pago',1),(4,2,'Gestionar cuotas',1)"
            )
            conn.exec_driver_sql(
                "INSERT INTO rol_funcion VALUES (2,1,2,1),(3,1,3,1),(4,1,4,1)"
            )
            conn.exec_driver_sql(
                "INSERT INTO historial_clinico VALUES (10,1,'2026-01-01',NULL,1)"
            )
            conn.exec_driver_sql(
                "INSERT INTO oftalmologo VALUES (1,7,'M-1','Oftal','Test','General',1,'2026-01-01')"
            )
            conn.exec_driver_sql(
                "INSERT INTO consulta_clinica VALUES (100,10,NULL,1,'2026-01-01',NULL,NULL,NULL,1)"
            )
            conn.exec_driver_sql(
                "INSERT INTO servicio_oftalmologico VALUES (1,'Examen',NULL,1,10,1)"
            )
            conn.exec_driver_sql(
                "INSERT INTO servicio_realizado VALUES (1000,1,1,100,1,'2026-01-01',NULL,1,?)",
                (price,),
            )
    for code, expected in (("ALPHA", "10.00"), ("BETA", "90.00")):
        tenant_id = 1 if code == "ALPHA" else 2
        token = crear_tenant_access_token(7, 1, tenant_id, tenant_id, code)
        response = client.get(
            "/pagos/administrativos/estado-cuenta/1", headers=auth(token),
        )
        assert response.status_code == 200, response.text
        assert response.json()["paciente"]["nombres"] == code
        assert response.json()["resumen"]["importe_total_servicios"] == expected
    assert registry.calls == ["ALPHA", "BETA"]


def test_consulta_no_modifica_registros_financieros(db_cu34, monkeypatch):
    models = (ServicioRealizado, Pago, PagoDetalle, PlanPago, PlanPagoServicio,
              Cuota, IntentoPagoCuota, NotificacionPago)

    def snapshot():
        return tuple(
            tuple(tuple(row) for row in db_cu34.execute(model.__table__.select()).all())
            for model in models
        )

    import app.modules.gestion_pagos.services.notificaciones as notificaciones
    from app.modules.gestion_pagos.providers.stripe_provider import StripeProveedor

    def prohibir_notificacion(*_args, **_kwargs):
        pytest.fail("La consulta no debe programar notificaciones")

    monkeypatch.setattr(notificaciones, "programar_comprobante", prohibir_notificacion)
    monkeypatch.setattr(StripeProveedor, "crear_intencion", prohibir_notificacion)
    antes = snapshot()
    _estado(db_cu34)
    _estado(db_cu34)
    despues = snapshot()
    assert despues == antes


def test_consulta_no_altera_respuesta_de_servicios_pendientes(cliente_cu31, db_cu34):
    tenant_token = crear_tenant_access_token(
        usuario_id=13, rol_id=2, tenant_id=1, empresa_id=1,
        empresa_codigo="TEST-CU34",
    )
    headers = {"Authorization": f"Bearer {tenant_token}"}
    path = "/pagos/administrativos/servicios-pendientes?paciente_id=1"
    antes = cliente_cu31.get(path, headers=headers)
    assert antes.status_code == 200
    estado = cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1", headers=headers,
    )
    assert estado.status_code == 200
    despues = cliente_cu31.get(path, headers=headers)
    assert despues.status_code == 200
    assert despues.json() == antes.json()


def test_endpoint_requiere_permisos_financieros(cliente_cu31, db_cu34):
    db_cu34.commit()
    tenant_token = crear_tenant_access_token(
        usuario_id=13, rol_id=2, tenant_id=1, empresa_id=1,
        empresa_codigo="TEST-CU34",
    )
    response = cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1",
        headers={"Authorization": f"Bearer {tenant_token}"},
    )
    assert response.status_code == 200
    assert response.json()["resumen"]["saldo_total_pendiente"] == "160.10"


def test_contrato_json_estado_cuenta_coherente(cliente_cu31, db_cu34):
    _limpiar_pagos(db_cu34)
    db_cu34.execute(text("DELETE FROM servicio_realizado WHERE paciente_id=1"))
    db_cu34.execute(text(
        "INSERT INTO servicio_realizado "
        "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
        "fecha_realizacion,observaciones,estado,precio_aplicado) VALUES "
        "(6001,1,1,100,5,'2026-10-20',NULL,1,100.00),"
        "(6002,1,1,100,5,'2026-10-21',NULL,1,50.00),"
        "(6003,1,1,100,5,'2026-10-22',NULL,1,200.00)"
    ))
    _insertar_pago(db_cu34, 6001, "30.00")
    _insertar_detalle(db_cu34, 6001, 6001, 6001, "30.00")
    _insertar_pago(db_cu34, 6002, "20.00")
    _insertar_detalle(db_cu34, 6002, 6002, 6001, "20.00")
    _insertar_pago(db_cu34, 6003, "100.00")
    _insertar_detalle(db_cu34, 6003, 6003, 6003, "100.00")
    _insertar_pago(db_cu34, 6004, "50.00", "PENDIENTE")
    _insertar_detalle(db_cu34, 6004, 6004, 6002, "50.00")
    db_cu34.execute(text(
        "INSERT INTO plan_pago VALUES (6001,1,100,200,'BOB',2,"
        "'2026-10-01','ACTIVO',NULL,13,'plan-contrato')"
    ))
    db_cu34.execute(text("INSERT INTO plan_pago_servicio VALUES (6001,6003,200,1)"))
    db_cu34.execute(text(
        "INSERT INTO cuota VALUES (6001,6001,1,100,'2026-10-30','PAGADA'),"
        "(6002,6001,2,100,'2026-12-15','PENDIENTE')"
    ))
    db_cu34.execute(text(
        "INSERT INTO intento_pago_cuota (id,cuota_id,pago_id,aplicado_a_cuota) "
        "VALUES (6001,6001,6003,1)"
    ))
    db_cu34.commit()
    tenant_token = crear_tenant_access_token(
        usuario_id=13, rol_id=2, tenant_id=1, empresa_id=1,
        empresa_codigo="TEST-CU34",
    )
    response = cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1",
        headers={"Authorization": f"Bearer {tenant_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "paciente": {
            "id": 1, "nombres": "Ana", "apellidos": "Uno",
            "ci": None, "telefono": None,
        },
        "resumen": {
            "moneda": "BOB", "importe_total_servicios": "350.00",
            "total_pagos_aprobados_aplicados": "150.00",
            "saldo_total_pendiente": "200.00",
            "saldo_servicios_no_financiados": "100.00",
            "saldo_planes_pago": "100.00", "importe_cuotas_vencidas": "0.00",
            "cantidad_planes_activos": 1, "cantidad_cuotas_pendientes": 1,
            "cantidad_cuotas_pagadas": 1, "cantidad_cuotas_vencidas": 0,
        },
        "servicios": [
            {
                "servicio_realizado_id": 6003, "consulta_clinica_id": 100,
                "servicio": "Tonometria", "fecha_realizacion": "2026-10-22T00:00:00",
                "precio_aplicado": "200.00", "monto_pagado_aplicado": "100.00",
                "saldo_pendiente": "100.00", "financiado": True, "plan_pago_id": 6001,
            },
            {
                "servicio_realizado_id": 6002, "consulta_clinica_id": 100,
                "servicio": "Tonometria", "fecha_realizacion": "2026-10-21T00:00:00",
                "precio_aplicado": "50.00", "monto_pagado_aplicado": "0.00",
                "saldo_pendiente": "50.00", "financiado": False, "plan_pago_id": None,
            },
            {
                "servicio_realizado_id": 6001, "consulta_clinica_id": 100,
                "servicio": "Tonometria", "fecha_realizacion": "2026-10-20T00:00:00",
                "precio_aplicado": "100.00", "monto_pagado_aplicado": "50.00",
                "saldo_pendiente": "50.00", "financiado": False, "plan_pago_id": None,
            },
        ],
        "planes_pago": [
            {
                "id": 6001, "monto_total": "200.00", "moneda": "BOB",
                "estado": "ACTIVO", "saldo_pendiente": "100.00",
                "cuotas": [
                    {
                        "id": 6001, "numero_cuota": 1,
                        "monto_programado": "100.00", "fecha_vencimiento": "2026-10-30",
                        "estado": "PAGADA",
                    },
                    {
                        "id": 6002, "numero_cuota": 2,
                        "monto_programado": "100.00", "fecha_vencimiento": "2026-12-15",
                        "estado": "PENDIENTE",
                    },
                ],
            },
        ],
        "pagos_pendientes_confirmacion": [
            {
                "pago_id": 6004, "monto": "50.00", "moneda": "BOB",
                "estado": "PENDIENTE", "pasarela": None,
                "aplicado_a_obligacion": None, "requiere_reconciliacion": False,
            },
        ],
    }
    assert sum(Decimal(item["saldo_pendiente"]) for item in body["servicios"]) == Decimal("200.00")


def test_endpoint_rechaza_sin_token_o_token_invalido(cliente_cu31):
    assert cliente_cu31.get("/pagos/administrativos/estado-cuenta/1").status_code == 401
    assert cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1",
        headers={"Authorization": "Bearer invalido"},
    ).status_code == 401


def test_endpoint_rechaza_token_tenant_expirado(cliente_cu31):
    expired = jwt.encode({
        "sub": "13", "rol_id": 2, "tenant_id": 1, "empresa_id": 1,
        "empresa_codigo": "TEST-CU34", "token_type": "tenant",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    response = cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1",
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 401


def test_endpoint_no_admite_paciente_movil(cliente_cu31):
    tenant_token = crear_tenant_access_token(
        usuario_id=11, rol_id=1, tenant_id=1, empresa_id=1,
        empresa_codigo="TEST-CU34",
    )
    response = cliente_cu31.get(
        "/pagos/administrativos/estado-cuenta/1",
        headers={"Authorization": f"Bearer {tenant_token}"},
    )
    assert response.status_code == 403
