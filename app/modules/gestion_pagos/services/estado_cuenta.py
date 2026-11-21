from collections import defaultdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.time import fecha_local_aplicacion
from app.modules.gestion_pagos.repositories import estado_cuenta as repo
from app.modules.gestion_pagos.schemas.estado_cuenta import (
    CuotaEstadoCuenta,
    EstadoCuentaPaciente,
    PacienteEstadoCuenta,
    PagoPendienteEstadoCuenta,
    PlanEstadoCuenta,
    ResumenEstadoCuenta,
    ServicioEstadoCuenta,
)
from app.modules.gestion_pagos.services.administrativos import _actor_administrativo


CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def _decimal(value) -> Decimal:
    return (value if isinstance(value, Decimal) else Decimal(str(value))).quantize(CENT)


def consultar_estado_cuenta(db: Session, paciente_id: int, usuario) -> EstadoCuentaPaciente:
    _actor_administrativo(usuario)
    paciente = repo.obtener_paciente(db, paciente_id)
    if paciente is None or not paciente.estado:
        raise HTTPException(404, "Paciente activo no encontrado")

    servicios = repo.listar_servicios(db, paciente_id)
    ids = [item.id for item in servicios]
    planes = repo.listar_planes(db, paciente_id)
    filas = repo.pagos_y_detalles(db, ids)
    intentos = repo.listar_intentos_paciente(db, paciente_id)

    aprobados_por_servicio: dict[int, Decimal] = defaultdict(lambda: ZERO)
    detalle_por_pago: dict[int, Decimal] = defaultdict(lambda: ZERO)
    monto_pago: dict[int, Decimal] = {}
    pagos_pendientes: dict[int, PagoPendienteEstadoCuenta] = {}
    monedas = {p.moneda for _, p, _ in filas}
    for detalle, pago, intento in filas:
        detalle_por_pago[pago.id] += _decimal(detalle.monto_aplicado)
        monto_pago[pago.id] = _decimal(pago.monto)
        if pago.estado == "APROBADO" and (intento is None or intento.aplicado_a_cuota):
            aprobados_por_servicio[detalle.servicio_realizado_id] += _decimal(detalle.monto_aplicado)
        if pago.estado == "PENDIENTE":
            pagos_pendientes[pago.id] = PagoPendienteEstadoCuenta(
                pago_id=pago.id, monto=_decimal(pago.monto), moneda=pago.moneda,
                estado=pago.estado, pasarela=pago.pasarela,
                aplicado_a_obligacion=intento.aplicado_a_cuota if intento else None,
                requiere_reconciliacion=False,
            )
        elif pago.estado == "APROBADO" and intento is not None and not intento.aplicado_a_cuota:
            pagos_pendientes[pago.id] = PagoPendienteEstadoCuenta(
                pago_id=pago.id, monto=_decimal(pago.monto), moneda=pago.moneda,
                estado=pago.estado, pasarela=pago.pasarela,
                aplicado_a_obligacion=False, requiere_reconciliacion=True,
            )
    if any(detalle_por_pago[pago_id] != monto for pago_id, monto in monto_pago.items()):
        raise HTTPException(
            409,
            "La cuenta contiene un pago cuya distribución no coincide con su importe",
        )

    plan_servicio: dict[int, tuple[int, Decimal, bool]] = {}
    for plan in planes:
        for vinculacion in plan.servicios:
            plan_servicio[vinculacion.servicio_realizado_id] = (
                plan.id, _decimal(vinculacion.monto_financiado), plan.estado != "CANCELADO"
            )

    total_servicios = ZERO
    total_aplicado = ZERO
    saldo_no_financiado = ZERO
    servicios_salida = []
    for servicio in servicios:
        precio = _decimal(servicio.precio_aplicado or ZERO)
        cobrable = (
            servicio.estado is True and servicio.consulta_clinica_id is not None
            and servicio.precio_aplicado is not None and precio > ZERO
        )
        if not cobrable:
            continue
        total_servicios += precio
        vinculo = plan_servicio.get(servicio.id)
        es_financiado = vinculo is not None and vinculo[2]
        plan_id = vinculo[0] if vinculo else None
        aplicado = aprobados_por_servicio[servicio.id]
        if aplicado > precio:
            raise HTTPException(
                409,
                "La cuenta contiene importes aplicados superiores al precio de un servicio",
            )
        total_aplicado += aplicado
        saldo = max(precio - aplicado, ZERO)
        if not es_financiado:
            saldo_no_financiado += saldo
        servicios_salida.append(ServicioEstadoCuenta(
            servicio_realizado_id=servicio.id,
            consulta_clinica_id=servicio.consulta_clinica_id,
            servicio=servicio.servicio.nombre,
            fecha_realizacion=servicio.fecha_realizacion,
            precio_aplicado=precio,
            monto_pagado_aplicado=aplicado,
            saldo_pendiente=saldo,
            financiado=es_financiado,
            plan_pago_id=plan_id if es_financiado else None,
        ))

    planes_salida = []
    saldo_planes = ZERO
    vencido_total = ZERO
    cantidad_pendientes = cantidad_pagadas = cantidad_vencidas = activos = 0
    hoy = fecha_local_aplicacion()
    for plan in planes:
        cuotas_out = []
        saldo_plan = ZERO
        total_financiado = sum(
            (_decimal(item.monto_financiado) for item in plan.servicios), ZERO,
        )
        total_cuotas_pendientes_plan = ZERO
        saldo_financiado_servicios = ZERO
        for vinculacion in plan.servicios:
            aplicado_servicio = aprobados_por_servicio[vinculacion.servicio_realizado_id]
            financiado = _decimal(vinculacion.monto_financiado)
            if aplicado_servicio > financiado:
                raise HTTPException(
                    409,
                    "La cuenta contiene importes aplicados superiores al monto financiado",
                )
            saldo_financiado_servicios += financiado - aplicado_servicio
        for cuota in plan.cuotas:
            estado = cuota.estado
            if estado == "PENDIENTE" and cuota.fecha_vencimiento < hoy:
                estado = "VENCIDA"
            if estado in {"PENDIENTE", "VENCIDA"} and plan.estado != "CANCELADO":
                saldo_plan += _decimal(cuota.monto_programado)
                total_cuotas_pendientes_plan += _decimal(cuota.monto_programado)
            if estado == "PENDIENTE":
                cantidad_pendientes += 1
            elif estado == "VENCIDA":
                cantidad_vencidas += 1
                vencido_total += _decimal(cuota.monto_programado)
            elif estado == "PAGADA":
                cantidad_pagadas += 1
            cuotas_out.append(CuotaEstadoCuenta(
                id=cuota.id, numero_cuota=cuota.numero_cuota,
                monto_programado=_decimal(cuota.monto_programado),
                fecha_vencimiento=cuota.fecha_vencimiento, estado=estado,
            ))
        if plan.estado != "CANCELADO" and (
            total_financiado != _decimal(plan.monto_total)
            or sum((_decimal(item.monto_programado) for item in plan.cuotas), ZERO)
            != _decimal(plan.monto_total)
            or total_cuotas_pendientes_plan != saldo_financiado_servicios
        ):
            raise HTTPException(
                409,
                "La cuenta contiene un plan con importes inconsistentes entre servicios y cuotas",
            )
        if plan.estado == "ACTIVO":
            activos += 1
            saldo_planes += saldo_plan
        planes_salida.append(PlanEstadoCuenta(
            id=plan.id, monto_total=_decimal(plan.monto_total), moneda=plan.moneda,
            estado=plan.estado, saldo_pendiente=saldo_plan, cuotas=cuotas_out,
        ))

    # Reconcile obligation balance from service-level amounts. Plan balance is
    # informational and must not be added to service debt a second time.
    # A service is one obligation: use its applied details exactly once. Active
    # financing is a presentation split, never an additional debt component.
    saldo_total = saldo_no_financiado + saldo_planes
    monedas |= {p.moneda for p in planes}
    if monedas - {"BOB"} or len(monedas) > 1:
        raise HTTPException(409, "La cuenta contiene monedas incompatibles")
    return EstadoCuentaPaciente(
        paciente=PacienteEstadoCuenta(
            id=paciente.id, nombres=paciente.nombres, apellidos=paciente.apellidos,
            ci=paciente.ci, telefono=paciente.telefono,
        ),
        resumen=ResumenEstadoCuenta(
            moneda=next(iter(monedas), "BOB"),
            importe_total_servicios=total_servicios,
            total_pagos_aprobados_aplicados=total_aplicado,
            saldo_total_pendiente=saldo_total,
            saldo_servicios_no_financiados=saldo_no_financiado,
            saldo_planes_pago=saldo_planes,
            importe_cuotas_vencidas=vencido_total,
            cantidad_planes_activos=activos,
            cantidad_cuotas_pendientes=cantidad_pendientes,
            cantidad_cuotas_pagadas=cantidad_pagadas,
            cantidad_cuotas_vencidas=cantidad_vencidas,
        ),
        servicios=servicios_salida,
        planes_pago=planes_salida,
        pagos_pendientes_confirmacion=list(pagos_pendientes.values()),
    )
