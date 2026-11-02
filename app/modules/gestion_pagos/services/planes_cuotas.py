from datetime import date, datetime, timezone
from decimal import Decimal
from math import ceil

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.core.time import fecha_local_aplicacion
from app.modules.gestion_pacientes.repositories import repository as paciente_repo
from app.modules.gestion_pagos.models.models import Pago, PagoDetalle
from app.modules.gestion_pagos.providers import (
    ErrorProveedorPago,
    ProveedorNoConfigurado,
    ProveedorPagoBase,
    es_intencion_reutilizable,
)
from app.modules.gestion_pagos.repositories import administrativos as admin_repo
from app.modules.gestion_pagos.repositories import planes_cuotas as repo
from app.modules.gestion_pagos.repositories import repository as pago_repo
from app.modules.gestion_pagos.schemas.planes_cuotas import (
    CancelarPlanPago,
    CuotaRespuesta,
    IntencionCuotaStripeRespuesta,
    IntentosCuotaRespuesta,
    PagoCuotaCrear,
    PagoCuotaRespuesta,
    PagoCuotaResumen,
    PlanPagoCrear,
    PlanPagoListadoItem,
    PlanPagoRespuesta,
    PlanesPagoPagina,
    RechazarPagoCuota,
    ServicioDisponiblePlan,
    ServicioPlanRespuesta,
    ServiciosDisponiblesPlanRespuesta,
)
from app.modules.gestion_pagos.services import notificaciones
from app.modules.gestion_pagos.services import service as pagos_service
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import registrar_bitacora


CENT = Decimal("0.01")


def _decimal(valor) -> Decimal:
    return Decimal(str(valor)).quantize(CENT)


def _actor_administrativo(usuario: Usuario) -> None:
    if nombre_rol_actual(usuario) not in {"administrador", "recepcionista"}:
        raise HTTPException(403, "Solo Administrador o Recepcionista puede gestionar planes")


def _estado_cuota(cuota, hoy: date | None = None) -> str:
    if cuota.estado != "PENDIENTE":
        return cuota.estado
    if cuota.fecha_vencimiento < (hoy or fecha_local_aplicacion()):
        return "VENCIDA"
    return "PENDIENTE"


def _pago_resumen(intento) -> PagoCuotaResumen:
    pago = intento.pago
    return PagoCuotaResumen(
        pago_id=pago.id,
        metodo_pago=pago.metodo_pago,
        estado=pago.estado,
        monto=_decimal(pago.monto),
        fecha_creacion=pago.fecha_creacion,
        aplicado_a_cuota=intento.aplicado_a_cuota,
    )


def _respuesta_plan(plan) -> PlanPagoRespuesta:
    cuotas = [
        CuotaRespuesta(
            id=cuota.id,
            numero_cuota=cuota.numero_cuota,
            monto_programado=_decimal(cuota.monto_programado),
            fecha_vencimiento=cuota.fecha_vencimiento,
            estado=_estado_cuota(cuota),
            pagos=[_pago_resumen(item) for item in cuota.intentos],
        )
        for cuota in plan.cuotas
    ]
    monto_pagado = sum(
        (_decimal(item.monto_programado) for item in plan.cuotas if item.estado == "PAGADA"),
        Decimal("0.00"),
    )
    return PlanPagoRespuesta(
        id=plan.id,
        paciente_id=plan.paciente_id,
        consulta_clinica_id=plan.consulta_clinica_id,
        monto_total=_decimal(plan.monto_total),
        moneda=plan.moneda,
        cantidad_cuotas=plan.cantidad_cuotas,
        fecha_creacion=plan.fecha_creacion,
        estado=plan.estado,
        observaciones=plan.observaciones,
        creado_por=plan.creado_por,
        saldo_pendiente=_decimal(plan.monto_total) - monto_pagado,
        monto_pagado=monto_pagado,
        servicios=[
            ServicioPlanRespuesta(
                servicio_realizado_id=item.servicio_realizado_id,
                nombre_servicio=item.servicio_realizado.servicio.nombre,
                monto_financiado=_decimal(item.monto_financiado),
                activo=item.activo,
            )
            for item in plan.servicios
        ],
        cuotas=cuotas,
    )


def _respuesta_listado(plan) -> PlanPagoListadoItem:
    pagadas = [item for item in plan.cuotas if item.estado == "PAGADA"]
    monto_pagado = sum(
        (_decimal(item.monto_programado) for item in pagadas), Decimal("0.00"),
    )
    return PlanPagoListadoItem(
        id=plan.id,
        paciente_id=plan.paciente_id,
        consulta_clinica_id=plan.consulta_clinica_id,
        monto_total=_decimal(plan.monto_total),
        monto_pagado=monto_pagado,
        saldo_pendiente=_decimal(plan.monto_total) - monto_pagado,
        moneda=plan.moneda,
        cantidad_cuotas=plan.cantidad_cuotas,
        cuotas_pagadas=len(pagadas),
        fecha_creacion=plan.fecha_creacion,
        estado=plan.estado,
        tiene_cuotas_vencidas=any(_estado_cuota(item) == "VENCIDA" for item in plan.cuotas),
    )


def _plan_mismo_payload(plan, datos: PlanPagoCrear) -> bool:
    servicios = sorted(item.servicio_realizado_id for item in plan.servicios)
    cuotas = [
        (item.numero_cuota, _decimal(item.monto_programado), item.fecha_vencimiento)
        for item in plan.cuotas
    ]
    esperadas = [
        (item.numero_cuota, _decimal(item.monto_programado), item.fecha_vencimiento)
        for item in sorted(datos.cuotas, key=lambda item: item.numero_cuota)
    ]
    return (
        plan.paciente_id == datos.paciente_id
        and plan.consulta_clinica_id == datos.consulta_clinica_id
        and servicios == sorted(datos.servicio_realizado_ids)
        and cuotas == esperadas
        and plan.observaciones == datos.observaciones
    )


def listar_servicios_disponibles(
    db: Session,
    usuario: Usuario,
    *,
    paciente_id: int,
    consulta_clinica_id: int,
) -> ServiciosDisponiblesPlanRespuesta:
    _actor_administrativo(usuario)
    paciente = paciente_repo.obtener_paciente_por_id(db, paciente_id)
    if paciente is None or not paciente.estado:
        raise HTTPException(404, "Paciente activo no encontrado")
    if not pago_repo.consulta_pertenece_a_paciente(db, consulta_clinica_id, paciente_id):
        raise HTTPException(404, "Consulta activa del paciente no encontrada")
    servicios = [
        item for item in pago_repo.listar_servicios_realizados_por_consulta(
            db, consulta_clinica_id,
        )
        if item.paciente_id == paciente_id
    ]
    ids = [item.id for item in servicios]
    cobrados = repo.montos_aprobados_por_servicio(db, ids)
    reservas = repo.reservas_activas_por_servicio(db, ids)
    conflictos = admin_repo.conflictos_por_servicio(db, ids)
    items = []
    for servicio in servicios:
        precio = _decimal(servicio.precio_aplicado or 0)
        cobrado = _decimal(cobrados.get(servicio.id, 0))
        conflicto = None
        if servicio.estado is not True or precio <= 0:
            conflicto = "SERVICIO_NO_FINANCIABLE"
        elif cobrado > 0:
            conflicto = "SERVICIO_CON_COBROS_PREVIOS"
        elif servicio.id in reservas:
            conflicto = "SERVICIO_RESERVADO_EN_PLAN"
        elif servicio.id in conflictos:
            conflicto = conflictos[servicio.id]
        items.append(ServicioDisponiblePlan(
            servicio_realizado_id=servicio.id,
            nombre_servicio=servicio.servicio.nombre,
            precio_aplicado=precio,
            saldo_cobrado=cobrado,
            saldo_disponible=max(precio - cobrado, Decimal("0.00")),
            disponible=conflicto is None,
            conflicto=conflicto,
        ))
    return ServiciosDisponiblesPlanRespuesta(
        paciente_id=paciente_id,
        consulta_clinica_id=consulta_clinica_id,
        servicios=items,
        total_disponible=sum(
            (item.saldo_disponible for item in items if item.disponible), Decimal("0.00"),
        ),
    )


def crear_plan(
    db: Session,
    datos: PlanPagoCrear,
    clave_idempotencia: str,
    usuario: Usuario,
    ip: str | None = None,
) -> PlanPagoRespuesta:
    _actor_administrativo(usuario)
    try:
        existente = repo.obtener_plan_por_clave(db, clave_idempotencia, bloquear=True)
        if existente is not None:
            if not _plan_mismo_payload(existente, datos):
                raise HTTPException(409, "La clave idempotente ya fue usada con otros datos")
            plan_id = existente.id
            db.rollback()
            return _respuesta_plan(repo.obtener_plan(db, plan_id))

        servicios = pago_repo.obtener_servicios_realizados_por_ids_para_actualizacion(
            db, datos.servicio_realizado_ids,
        )
        seleccion = pagos_service.validar_seleccion_servicios(
            db,
            datos.servicio_realizado_ids,
            paciente_id_esperado=datos.paciente_id,
            servicios_precargados=servicios,
        )
        if seleccion.consulta_id != datos.consulta_clinica_id:
            raise HTTPException(422, "Los servicios no pertenecen a la consulta indicada")
        cobrados = repo.montos_aprobados_por_servicio(db, datos.servicio_realizado_ids)
        if any(_decimal(valor) > 0 for valor in cobrados.values()):
            raise HTTPException(409, "Un servicio ya tiene cobros aprobados")
        if repo.reservas_activas_por_servicio(db, datos.servicio_realizado_ids):
            raise HTTPException(409, "Un servicio ya esta reservado por otro plan")
        conflictos = admin_repo.pagos_conflictivos_para_servicios(
            db, datos.servicio_realizado_ids,
        )
        if conflictos:
            raise HTTPException(409, "Existen pagos o intentos incompatibles con el plan")

        total_cuotas = sum(
            (_decimal(item.monto_programado) for item in datos.cuotas), Decimal("0.00"),
        )
        if total_cuotas != _decimal(seleccion.total):
            raise HTTPException(422, "La suma de cuotas debe coincidir exactamente con los servicios")
        plan = repo.crear_plan(
            db,
            paciente_id=datos.paciente_id,
            consulta_clinica_id=datos.consulta_clinica_id,
            monto_total=seleccion.total,
            cantidad_cuotas=len(datos.cuotas),
            observaciones=datos.observaciones,
            creado_por=usuario.id,
            clave_idempotencia=clave_idempotencia,
        )
        repo.crear_servicios_plan(db, plan.id, seleccion.servicios)
        repo.crear_cuotas(db, plan.id, datos.cuotas)
        registrar_bitacora(
            db=db, usuario_id=usuario.id, ip=ip,
            accion="CREAR_PLAN_PAGO", entidad_afectada="plan_pago",
            id_registro_afectado=plan.id,
            descripcion=f"Plan creado con {len(datos.cuotas)} cuotas",
        )
        db.commit()
        return _respuesta_plan(repo.obtener_plan(db, plan.id))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Clave idempotente o servicio reservado duplicado") from exc
    except Exception:
        db.rollback()
        raise


def listar_planes(db: Session, usuario: Usuario, **filtros) -> PlanesPagoPagina:
    _actor_administrativo(usuario)
    planes, total = repo.listar_planes(db, **filtros)
    page, page_size = filtros.get("page", 1), filtros.get("page_size", 20)
    return PlanesPagoPagina(
        items=[_respuesta_listado(item) for item in planes],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=ceil(total / page_size),
    )


def consultar_plan(db: Session, plan_id: int, usuario: Usuario) -> PlanPagoRespuesta:
    _actor_administrativo(usuario)
    plan = repo.obtener_plan(db, plan_id)
    if plan is None:
        raise HTTPException(404, "Plan de pago no encontrado")
    return _respuesta_plan(plan)


def cancelar_plan(
    db: Session,
    plan_id: int,
    datos: CancelarPlanPago,
    usuario: Usuario,
    ip: str | None = None,
) -> PlanPagoRespuesta:
    _actor_administrativo(usuario)
    try:
        plan = repo.obtener_plan(db, plan_id, bloquear=True)
        if plan is None:
            raise HTTPException(404, "Plan de pago no encontrado")
        if plan.estado == "CANCELADO":
            db.rollback()
            return _respuesta_plan(repo.obtener_plan(db, plan_id))
        if plan.estado != "ACTIVO":
            raise HTTPException(409, "Solo un plan activo puede cancelarse")
        repo.bloquear_cuotas_plan(db, plan.id)
        plan = repo.obtener_plan(db, plan.id)
        intentos = [item for cuota in plan.cuotas for item in cuota.intentos]
        if any(item.aplicado_a_cuota or item.pago.estado == "APROBADO" for item in intentos):
            raise HTTPException(409, "El plan tiene cobros aprobados y no puede cancelarse")
        if any(
            item.pago.estado == "PENDIENTE"
            or (item.pago.pasarela == "STRIPE" and item.pago.estado == "RECHAZADO")
            for item in intentos
        ):
            raise HTTPException(409, "El plan tiene intentos potencialmente cobrables; concilielos primero")
        repo.cancelar_plan(db, plan)
        registrar_bitacora(
            db=db, usuario_id=usuario.id, ip=ip,
            accion="CANCELAR_PLAN_PAGO", entidad_afectada="plan_pago",
            id_registro_afectado=plan.id, descripcion=datos.motivo,
        )
        db.commit()
        return _respuesta_plan(repo.obtener_plan(db, plan.id))
    except Exception:
        db.rollback()
        raise


def listar_mis_planes(db: Session, usuario: Usuario, **filtros) -> PlanesPagoPagina:
    paciente = pagos_service._resolver_paciente_actual(db, usuario)
    return _listar_planes_paciente(db, paciente.id, **filtros)


def _listar_planes_paciente(db: Session, paciente_id: int, **filtros):
    filtros["paciente_id"] = paciente_id
    planes, total = repo.listar_planes(db, **filtros)
    page, page_size = filtros.get("page", 1), filtros.get("page_size", 20)
    return PlanesPagoPagina(
        items=[_respuesta_listado(item) for item in planes], total=total,
        page=page, page_size=page_size, total_pages=ceil(total / page_size),
    )


def consultar_mi_plan(db: Session, plan_id: int, usuario: Usuario) -> PlanPagoRespuesta:
    paciente = pagos_service._resolver_paciente_actual(db, usuario)
    plan = repo.obtener_plan(db, plan_id)
    if plan is None or plan.paciente_id != paciente.id:
        raise HTTPException(404, "Plan de pago no encontrado")
    return _respuesta_plan(plan)


def _validar_cuota_cobrable(cuota) -> None:
    if cuota.plan.estado != "ACTIVO" or cuota.estado != "PENDIENTE":
        raise HTTPException(409, "La cuota no esta disponible para cobro")


def _asignacion_cuota(db: Session, cuota) -> list[tuple[object, Decimal]]:
    servicios = sorted(cuota.plan.servicios, key=lambda item: item.servicio_realizado_id)
    cobrados = repo.montos_aprobados_por_servicio(
        db, [item.servicio_realizado_id for item in servicios],
    )
    restante = _decimal(cuota.monto_programado)
    asignacion = []
    for item in servicios:
        disponible = _decimal(item.monto_financiado) - _decimal(
            cobrados.get(item.servicio_realizado_id, 0),
        )
        if disponible <= 0:
            continue
        aplicado = min(disponible, restante)
        if aplicado > 0:
            asignacion.append((item, aplicado))
            restante -= aplicado
        if restante == 0:
            break
    if restante != 0:
        raise HTTPException(409, "La cuota excede el saldo financiado disponible")
    return asignacion


def _crear_detalles_asignados(db: Session, pago_id: int, asignacion):
    detalles = [
        PagoDetalle(
            pago_id=pago_id,
            servicio_realizado_id=item.servicio_realizado_id,
            monto_aplicado=monto,
        )
        for item, monto in asignacion
    ]
    db.add_all(detalles)
    db.flush()
    return detalles


def _respuesta_pago(cuota, intento) -> PagoCuotaRespuesta:
    pago = intento.pago
    return PagoCuotaRespuesta(
        pago_id=pago.id, cuota_id=cuota.id, plan_pago_id=cuota.plan_pago_id,
        monto=_decimal(pago.monto), moneda=pago.moneda,
        metodo_pago=pago.metodo_pago, estado_pago=pago.estado,
        estado_cuota=_estado_cuota(cuota),
        aplicado_a_cuota=intento.aplicado_a_cuota,
        referencia_bancaria=(
            pago.referencia_transaccion if pago.pasarela is None else None
        ),
    )


def _aplicar_pago(db: Session, intento, *, fecha_pago: datetime, usuario_id=None):
    cuota = intento.cuota
    pago = intento.pago
    if intento.aplicado_a_cuota:
        return
    if cuota.estado != "PENDIENTE":
        raise HTTPException(409, "La cuota ya fue aplicada o cancelada")
    otros = [
        item for item in cuota.intentos
        if item.id != intento.id and item.aplicado_a_cuota
    ]
    if otros:
        raise HTTPException(409, "La cuota ya tiene otro pago aplicado")
    if _decimal(pago.monto) != _decimal(cuota.monto_programado):
        raise HTTPException(409, "El monto del pago no coincide con la cuota")
    if sum((_decimal(item.monto_aplicado) for item in pago.detalles), Decimal("0.00")) != _decimal(pago.monto):
        raise HTTPException(409, "La distribucion del pago no coincide con su monto")
    pago.estado = "APROBADO"
    pago.fecha_hora_pago = fecha_pago
    intento.aplicado_a_cuota = True
    cuota.estado = "PAGADA"
    repo.completar_plan_si_corresponde(db, cuota.plan)
    notificaciones.programar_comprobante(db, pago.id)
    registrar_bitacora(
        db=db, usuario_id=usuario_id,
        accion="APLICAR_PAGO_CUOTA", entidad_afectada="cuota",
        id_registro_afectado=cuota.id,
        descripcion=f"Pago {pago.id} aplicado integramente a cuota {cuota.numero_cuota}",
    )
    db.flush()


def registrar_pago_manual(
    db: Session,
    cuota_id: int,
    datos: PagoCuotaCrear,
    clave_idempotencia: str,
    usuario: Usuario,
    ip: str | None = None,
) -> PagoCuotaRespuesta:
    _actor_administrativo(usuario)
    try:
        existente = repo.obtener_intento_por_clave(db, clave_idempotencia)
        if existente is not None:
            cuota_bloqueada, existente, pago = repo.bloquear_operacion_por_pago(
                db,
                existente.pago_id,
            )
            if (
                cuota_bloqueada is None
                or existente is None
                or pago is None
                or existente.cuota_id != cuota_id
                or pago.metodo_pago != datos.metodo_pago
                or pago.observaciones != datos.observaciones
                or (pago.referencia_transaccion or None) != datos.referencia_bancaria
            ):
                raise HTTPException(409, "La clave idempotente ya fue usada con otros datos")
            db.rollback()
            cuota = repo.obtener_cuota(db, cuota_id)
            intento = repo.obtener_intento_por_pago(db, pago.id)
            return _respuesta_pago(cuota, intento)
        cuota = repo.bloquear_plan_y_cuota(db, cuota_id)
        if cuota is None:
            raise HTTPException(404, "Cuota no encontrada")
        _validar_cuota_cobrable(cuota)
        if repo.intentos_potencialmente_cobrables(cuota):
            raise HTTPException(409, "La cuota ya tiene un intento activo o cobrable")
        asignacion = _asignacion_cuota(db, cuota)
        aprobado = datos.metodo_pago == "EFECTIVO"
        pago = admin_repo.crear_pago_manual(
            db, monto=cuota.monto_programado, metodo_pago=datos.metodo_pago,
            estado="APROBADO" if aprobado else "PENDIENTE",
            referencia=datos.referencia_bancaria,
            observaciones=datos.observaciones,
            clave_idempotencia=clave_idempotencia,
            fecha_hora_pago=datetime.now(timezone.utc) if aprobado else None,
        )
        detalles = _crear_detalles_asignados(db, pago.id, asignacion)
        intento = repo.crear_intento(db, cuota.id, pago.id)
        intento.pago = pago
        pago.detalles = detalles
        intento.cuota = cuota
        if aprobado:
            _aplicar_pago(db, intento, fecha_pago=pago.fecha_hora_pago, usuario_id=usuario.id)
        registrar_bitacora(
            db=db, usuario_id=usuario.id, ip=ip,
            accion="REGISTRAR_PAGO_CUOTA", entidad_afectada="pago",
            id_registro_afectado=pago.id,
            descripcion=f"Pago {datos.metodo_pago} para cuota {cuota.numero_cuota}",
        )
        db.commit()
        cuota = repo.obtener_cuota(db, cuota.id)
        intento = repo.obtener_intento_por_pago(db, pago.id)
        return _respuesta_pago(cuota, intento)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Clave idempotente o referencia bancaria duplicada") from exc
    except Exception:
        db.rollback()
        raise


def confirmar_pago_manual(
    db: Session, cuota_id: int, pago_id: int, usuario: Usuario, ip: str | None = None,
) -> PagoCuotaRespuesta:
    _actor_administrativo(usuario)
    try:
        cuota, intento, pago = repo.bloquear_operacion_por_pago(db, pago_id)
        if cuota is None or intento is None or intento.cuota_id != cuota_id:
            raise HTTPException(404, "Pago de cuota no encontrado")
        if intento.aplicado_a_cuota:
            db.rollback()
            return _respuesta_pago(repo.obtener_cuota(db, cuota_id), repo.obtener_intento_por_pago(db, pago_id))
        intento.pago = pago
        if pago.pasarela is not None or pago.metodo_pago not in {"QR", "TRANSFERENCIA"}:
            raise HTTPException(409, "Solo QR o transferencia administrativa requieren confirmacion")
        if pago.estado != "PENDIENTE":
            raise HTTPException(409, f"No se puede confirmar un pago {pago.estado}")
        _validar_cuota_cobrable(cuota)
        _aplicar_pago(db, intento, fecha_pago=datetime.now(timezone.utc), usuario_id=usuario.id)
        registrar_bitacora(
            db=db, usuario_id=usuario.id, ip=ip,
            accion="CONFIRMAR_PAGO_CUOTA", entidad_afectada="pago",
            id_registro_afectado=pago.id, descripcion=f"Cobro verificado de cuota {cuota.numero_cuota}",
        )
        db.commit()
        return _respuesta_pago(repo.obtener_cuota(db, cuota_id), repo.obtener_intento_por_pago(db, pago_id))
    except Exception:
        db.rollback()
        raise


def rechazar_pago_manual(
    db: Session,
    cuota_id: int,
    pago_id: int,
    datos: RechazarPagoCuota,
    usuario: Usuario,
    ip: str | None = None,
) -> PagoCuotaRespuesta:
    _actor_administrativo(usuario)
    try:
        cuota, intento, pago = repo.bloquear_operacion_por_pago(db, pago_id)
        if cuota is None or intento is None or intento.cuota_id != cuota_id:
            raise HTTPException(404, "Pago de cuota no encontrado")
        intento.pago = pago
        if pago.pasarela is not None or pago.metodo_pago not in {"QR", "TRANSFERENCIA"}:
            raise HTTPException(409, "Solo un pendiente manual puede rechazarse")
        if intento.aplicado_a_cuota or pago.estado == "APROBADO":
            raise HTTPException(409, "Un pago aplicado no puede rechazarse")
        if pago.estado == "RECHAZADO":
            db.rollback()
            return _respuesta_pago(repo.obtener_cuota(db, cuota_id), repo.obtener_intento_por_pago(db, pago_id))
        if pago.estado != "PENDIENTE":
            raise HTTPException(409, f"No se puede rechazar un pago {pago.estado}")
        pago.estado = "RECHAZADO"
        registrar_bitacora(
            db=db, usuario_id=usuario.id, ip=ip,
            accion="RECHAZAR_PAGO_CUOTA", entidad_afectada="pago",
            id_registro_afectado=pago.id, descripcion=datos.motivo,
        )
        db.commit()
        return _respuesta_pago(repo.obtener_cuota(db, cuota_id), repo.obtener_intento_por_pago(db, pago_id))
    except Exception:
        db.rollback()
        raise


def listar_intentos_cuota(db: Session, cuota_id: int, usuario: Usuario):
    cuota = repo.obtener_cuota(db, cuota_id)
    if cuota is None:
        raise HTTPException(404, "Cuota no encontrada")
    rol = nombre_rol_actual(usuario)
    if rol == "paciente":
        paciente = pagos_service._resolver_paciente_actual(db, usuario)
        if cuota.plan.paciente_id != paciente.id:
            raise HTTPException(404, "Cuota no encontrada")
    else:
        _actor_administrativo(usuario)
    return IntentosCuotaRespuesta(
        cuota_id=cuota.id,
        estado_cuota=_estado_cuota(cuota),
        intentos=[_pago_resumen(item) for item in cuota.intentos],
    )


def crear_intencion_stripe(
    db: Session,
    cuota_id: int,
    usuario: Usuario,
    proveedor: ProveedorPagoBase,
) -> IntencionCuotaStripeRespuesta:
    paciente = pagos_service._resolver_paciente_actual(db, usuario)
    scope = pagos_service._stripe_payment_scope(db)
    try:
        proveedor.validar_configuracion()
    except (ProveedorNoConfigurado, ErrorProveedorPago) as error:
        pagos_service._traducir_error_proveedor(error)

    try:
        cuota = repo.bloquear_plan_y_cuota(db, cuota_id)
        if cuota is None or cuota.plan.paciente_id != paciente.id:
            raise HTTPException(404, "Cuota no encontrada")
        _validar_cuota_cobrable(cuota)
        conflictos = repo.intentos_potencialmente_cobrables(cuota)
        if len(conflictos) > 1:
            raise HTTPException(409, "La cuota tiene multiples intentos incompatibles")
        candidato = conflictos[0] if conflictos else None
        if candidato is not None and candidato.pago.pasarela != "STRIPE":
            raise HTTPException(409, "La cuota tiene un pago manual pendiente")
        db.commit()
    except Exception:
        db.rollback()
        raise

    intento_proveedor = None
    if candidato is not None and candidato.pago.referencia_transaccion:
        try:
            intento_proveedor = proveedor.recuperar_intencion(
                candidato.pago.referencia_transaccion,
            )
            pagos_service._validar_datos_intencion(
                intento_proveedor,
                monto=candidato.pago.monto,
                moneda=candidato.pago.moneda,
            )
        except (ProveedorNoConfigurado, ErrorProveedorPago) as error:
            pagos_service._traducir_error_proveedor(error)
        if intento_proveedor.status == "succeeded":
            raise HTTPException(409, "Existe un cobro exitoso pendiente de webhook")
        if intento_proveedor.status == "canceled":
            try:
                _, anterior, pago_anterior = repo.bloquear_operacion_por_pago(
                    db,
                    candidato.pago_id,
                )
                anterior.pago = pago_anterior
                pago_anterior.estado = "ANULADO"
                db.commit()
                candidato = None
                intento_proveedor = None
            except Exception:
                db.rollback()
                raise
        elif not es_intencion_reutilizable(intento_proveedor.status):
            raise HTTPException(409, "El intento Stripe no es reutilizable")

    try:
        cuota = repo.bloquear_plan_y_cuota(db, cuota_id)
        _validar_cuota_cobrable(cuota)
        conflictos = repo.intentos_potencialmente_cobrables(cuota)
        if candidato is not None:
            actual = next((item for item in conflictos if item.pago_id == candidato.pago_id), None)
            if actual is None or len(conflictos) != 1:
                raise HTTPException(409, "El estado de la cuota cambio; vuelva a intentar")
            intento = actual
            pago = actual.pago
        else:
            if conflictos:
                raise HTTPException(409, "La cuota tiene otro intento activo")
            asignacion = _asignacion_cuota(db, cuota)
            pago = pago_repo.crear_pago_stripe_pendiente(
                db, monto=cuota.monto_programado,
                moneda=pagos_service._moneda_stripe_configurada().upper(),
            )
            pago.clave_idempotencia = f"cuota-stripe-{cuota.id}-{pago.id}"
            detalles = _crear_detalles_asignados(db, pago.id, asignacion)
            intento = repo.crear_intento(db, cuota.id, pago.id)
            intento.pago = pago
            intento.cuota = cuota
            pago.detalles = detalles
            registrar_bitacora(
                db=db, usuario_id=usuario.id,
                accion="INICIAR_PAGO_STRIPE_CUOTA", entidad_afectada="pago",
                id_registro_afectado=pago.id,
                descripcion=f"Intento Stripe para cuota {cuota.numero_cuota}",
            )
        db.commit()
        pago_id = pago.id
        plan_id = cuota.plan_pago_id
        consulta_id = cuota.plan.consulta_clinica_id
    except Exception:
        db.rollback()
        raise

    if intento_proveedor is None:
        try:
            intento_proveedor = proveedor.crear_intencion(
                amount=pagos_service.monto_a_unidad_minima(_decimal(pago.monto)),
                currency=pago.moneda.lower(),
                metadata={
                    **scope.metadata,
                    "pago_id": str(pago.id),
                    "consulta_clinica_id": str(consulta_id),
                    "plan_pago_id": str(plan_id),
                    "cuota_id": str(cuota_id),
                },
                idempotency_key=f"{scope.idempotency_prefix}-cuota-pago-{pago.id}",
            )
            pagos_service._validar_intencion_reutilizable(intento_proveedor, pago)
        except (ProveedorNoConfigurado, ErrorProveedorPago) as error:
            pagos_service._traducir_error_proveedor(error)

    try:
        actual = pago_repo.obtener_pago_por_id(db, pago_id)
        if actual.referencia_transaccion is None:
            pago_repo.asignar_referencia_transaccion(db, actual, intento_proveedor.id)
            db.commit()
        elif actual.referencia_transaccion != intento_proveedor.id:
            raise HTTPException(409, "La referencia Stripe del pago no coincide")
    except Exception:
        db.rollback()
        raise
    return IntencionCuotaStripeRespuesta(
        pago_id=pago_id, cuota_id=cuota_id, plan_pago_id=plan_id,
        payment_intent_id=intento_proveedor.id,
        client_secret=intento_proveedor.client_secret,
        monto=_decimal(pago.monto), moneda=pago.moneda, estado_pago=pago.estado,
    )


def aplicar_pago_desde_webhook(
    db: Session,
    intento,
    *,
    fecha_pago: datetime,
) -> None:
    """Aplica un Stripe succeeded ya autenticado por el webhook compartido."""
    if intento.aplicado_a_cuota:
        return
    cuota = intento.cuota
    pago = intento.pago
    otro_aplicado = any(
        item.id != intento.id and item.aplicado_a_cuota
        for item in cuota.intentos
    )
    if cuota.estado != "PENDIENTE" or otro_aplicado:
        # Stripe ya confirma dinero real: se preserva como APROBADO aunque no
        # pueda aplicarse otra vez a la cuota. La bitacora deja el caso listo
        # para conciliacion/reembolso manual, sin ocultar el segundo cargo.
        pago.estado = "APROBADO"
        pago.fecha_hora_pago = fecha_pago
        notificaciones.programar_comprobante(db, pago.id)
        registrar_bitacora(
            db=db, usuario_id=None,
            accion="CONFLICTO_DOBLE_COBRO_CUOTA_STRIPE",
            entidad_afectada="pago", id_registro_afectado=pago.id,
            descripcion=(
                f"Stripe confirmo pago para cuota {cuota.id}, pero la cuota "
                "ya estaba aplicada o cancelada; requiere conciliacion"
            ),
        )
        db.flush()
        return
    _aplicar_pago(db, intento, fecha_pago=fecha_pago, usuario_id=None)
