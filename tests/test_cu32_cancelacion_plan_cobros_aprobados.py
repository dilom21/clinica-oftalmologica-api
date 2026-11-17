"""CU32: un plan con cobros aprobados no puede cancelarse.

Prueba aislada: reutiliza las fixtures de CU32/CU33, que trabajan sobre SQLite
en memoria, y la fixture autouse de `tests/conftest.py` que bloquea cualquier
conexión a la base compartida. No se leen ni escriben datos de Supabase.
"""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text

from tests.test_base_flujo_pagos import cliente_pagos, db_pagos
from tests.test_cu31_pagos_administrativos import cliente_cu31, db_cu31
from tests.test_cu32_cu33_planes_cuotas import cliente_cu32, db_cu32, tenant_token

from app.modules.gestion_pagos.models.models import Cuota, IntentoPagoCuota, Pago
from app.modules.gestion_pagos.schemas.planes_cuotas import (
    CancelarPlanPago,
    CuotaPlanCrear,
    PagoCuotaCrear,
    PlanPagoCrear,
)
from app.modules.gestion_pagos.services import planes_cuotas
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario

# Servicio propio de esta prueba: Bs 60,00 financiados en tres cuotas de Bs 20,00.
SERVICIO_60 = 2200


@pytest.fixture
def db_cancelacion(db_cu32):
    db_cu32.execute(text(
        "INSERT INTO servicio_realizado "
        "(id,servicio_id,paciente_id,consulta_clinica_id,oftalmologo_id,"
        "fecha_realizacion,observaciones,estado,precio_aplicado) VALUES "
        "(:id,1,1,100,5,'2026-10-20',NULL,1,60.00)"
    ), {"id": SERVICIO_60})
    db_cu32.commit()
    return db_cu32


def _admin(db) -> Usuario:
    return db.get(Usuario, 13)


def _datos_plan_20() -> PlanPagoCrear:
    vencimientos = (date(2026, 11, 15), date(2026, 12, 15), date(2027, 1, 15))
    return PlanPagoCrear(
        paciente_id=1,
        consulta_clinica_id=100,
        servicio_realizado_ids=[SERVICIO_60],
        cuotas=[
            CuotaPlanCrear(
                numero_cuota=numero,
                monto_programado="20.00",
                fecha_vencimiento=vencimiento,
            )
            for numero, vencimiento in enumerate(vencimientos, 1)
        ],
        observaciones="Acuerdo de Bs 60,00 en tres cuotas",
    )


def _plan_con_dos_cuotas_cobradas(db, clave="plan-cancelacion-cobros"):
    """Plan de 3 x Bs 20,00 con dos cuotas cobradas: Bs 40,00 pagados y Bs 20,00 pendientes."""
    plan = planes_cuotas.crear_plan(db, _datos_plan_20(), clave, _admin(db), "127.0.0.1")
    for cuota in plan.cuotas[:2]:
        planes_cuotas.registrar_pago_manual(
            db, cuota.id, PagoCuotaCrear(metodo_pago="EFECTIVO"),
            f"{clave}-cuota-{cuota.numero_cuota}", _admin(db),
        )
    return plan


def test_cancelacion_rechazada_con_cobros_aprobados_sin_modificar_el_plan(db_cancelacion):
    db = db_cancelacion
    plan = _plan_con_dos_cuotas_cobradas(db)

    antes = planes_cuotas.consultar_plan(db, plan.id, _admin(db))
    assert antes.monto_pagado == Decimal("40.00")
    assert antes.saldo_pendiente == Decimal("20.00")
    assert [cuota.estado for cuota in antes.cuotas] == ["PAGADA", "PAGADA", "PENDIENTE"]

    with pytest.raises(HTTPException) as error:
        planes_cuotas.cancelar_plan(
            db, plan.id, CancelarPlanPago(motivo="Intento de anulación"),
            _admin(db), "127.0.0.1",
        )

    assert error.value.status_code == 409
    assert error.value.detail == "El plan tiene cobros aprobados y no puede cancelarse"

    despues = planes_cuotas.consultar_plan(db, plan.id, _admin(db))
    assert despues.estado == "ACTIVO"
    assert [cuota.estado for cuota in despues.cuotas] == ["PAGADA", "PAGADA", "PENDIENTE"]
    assert despues.monto_pagado == Decimal("40.00")
    assert despues.saldo_pendiente == Decimal("20.00")
    assert all(servicio.activo for servicio in despues.servicios)
    assert db.scalar(select(func.count()).select_from(Cuota).where(
        Cuota.plan_pago_id == plan.id, Cuota.estado == "CANCELADA",
    )) == 0
    aprobados = db.scalar(
        select(func.count())
        .select_from(IntentoPagoCuota)
        .join(Cuota, Cuota.id == IntentoPagoCuota.cuota_id)
        .join(Pago, Pago.id == IntentoPagoCuota.pago_id)
        .where(Cuota.plan_pago_id == plan.id, Pago.estado == "APROBADO")
    )
    assert aprobados == 2
    assert db.scalar(select(func.count()).select_from(Bitacora).where(
        Bitacora.accion == "CANCELAR_PLAN_PAGO",
    )) == 0


def test_endpoint_cancelar_devuelve_409_y_conserva_el_estado(cliente_cu32, db_cancelacion):
    db = db_cancelacion
    plan = _plan_con_dos_cuotas_cobradas(db, "plan-cancelacion-http")

    respuesta = cliente_cu32.post(
        f"/pagos/planes/{plan.id}/cancelar",
        headers=tenant_token(13, 2),
        json={"motivo": "Intento de anulación"},
    )

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == "El plan tiene cobros aprobados y no puede cancelarse"

    detalle = cliente_cu32.get(
        f"/pagos/planes/{plan.id}", headers=tenant_token(13, 2),
    )
    assert detalle.status_code == 200
    cuerpo = detalle.json()
    assert cuerpo["estado"] == "ACTIVO"
    assert Decimal(str(cuerpo["monto_pagado"])) == Decimal("40.00")
    assert Decimal(str(cuerpo["saldo_pendiente"])) == Decimal("20.00")
