from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.core import config
from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_pacientes.repositories import repository as paciente_repo
from app.modules.gestion_pagos.repositories import repository as repo
from app.modules.gestion_pagos.providers import (
    ErrorFirmaWebhook,
    ErrorProveedorPago,
    ProveedorNoConfigurado,
    ProveedorPagoBase,
    es_intencion_reutilizable,
    es_intencion_terminal,
)
from app.modules.gestion_pagos.schemas.schemas import (
    ConsultaPagoResumen,
    EstadoPagoStripeRespuesta,
    IntencionPagoStripeRespuesta,
    OftalmologoPagoResumen,
    PagoHistorialItem,
    ServicioPagoHistorial,
    ServicioPagoRespuesta,
    WebhookStripeRespuesta,
)
from app.modules.gestion_pagos.services import comprobante_pdf
from app.modules.gestion_usuarios_seguridad.models.models import Usuario
from app.modules.gestion_usuarios_seguridad.repositories.repository import (
    registrar_bitacora,
)


@dataclass(frozen=True)
class ResultadoSeleccionServicios:
    consulta_id: int
    paciente_id: int
    servicios: list[ServicioRealizado]
    total: Decimal


def _decimal(valor) -> Decimal:
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def es_servicio_pagable(
    servicio: ServicioRealizado,
    *,
    cubierto_por_pago_aprobado: bool,
) -> bool:
    precio = servicio.precio_aplicado
    return bool(
        servicio.estado is True
        and servicio.consulta_clinica_id is not None
        and precio is not None
        and _decimal(precio) > Decimal("0")
        and not cubierto_por_pago_aprobado
    )


def _resolver_paciente_actual(db: Session, usuario: Usuario):
    if nombre_rol_actual(usuario) != "paciente":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo un paciente puede consultar sus pagos propios",
        )
    paciente = paciente_repo.obtener_paciente_por_usuario_id(db, usuario.id)
    if paciente is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El usuario no tiene un perfil de paciente",
        )
    if not paciente.estado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El perfil de paciente esta inactivo",
        )
    return paciente


def _servicios_visibles_del_paciente(db: Session, paciente_id: int):
    servicios = repo.listar_servicios_realizados_por_paciente(db, paciente_id)
    cubiertos = repo.obtener_ids_servicios_cubiertos_por_pago_aprobado(
        db,
        [servicio.id for servicio in servicios],
    )
    visibles = [
        servicio
        for servicio in servicios
        if servicio.id in cubiertos
        or es_servicio_pagable(servicio, cubierto_por_pago_aprobado=False)
    ]
    return visibles, cubiertos


def listar_mis_consultas_con_pagos(
    db: Session,
    usuario: Usuario,
) -> list[ConsultaPagoResumen]:
    paciente = _resolver_paciente_actual(db, usuario)
    servicios, cubiertos = _servicios_visibles_del_paciente(db, paciente.id)

    agrupados: dict[int, list[ServicioRealizado]] = {}
    for servicio in servicios:
        agrupados.setdefault(servicio.consulta_clinica_id, []).append(servicio)

    respuesta = []
    for items in agrupados.values():
        consulta = items[0].consulta_clinica
        pendientes = [item for item in items if item.id not in cubiertos]
        total = sum(
            (_decimal(item.precio_aplicado) for item in pendientes),
            start=Decimal("0.00"),
        )
        oftalmologo = None
        if consulta.oftalmologo is not None:
            oftalmologo = OftalmologoPagoResumen.model_validate(
                consulta.oftalmologo,
            )
        respuesta.append(
            ConsultaPagoResumen(
                consulta_id=consulta.id,
                fecha_consulta=consulta.fecha_consulta,
                oftalmologo=oftalmologo,
                cantidad_servicios=len(items),
                cantidad_pendientes=len(pendientes),
                total_pendiente=total,
            )
        )
    return respuesta


def listar_mis_servicios_de_consulta(
    db: Session,
    consulta_id: int,
    usuario: Usuario,
) -> list[ServicioPagoRespuesta]:
    paciente = _resolver_paciente_actual(db, usuario)
    servicios, cubiertos = _servicios_visibles_del_paciente(db, paciente.id)
    seleccionados = [
        servicio
        for servicio in servicios
        if servicio.consulta_clinica_id == consulta_id
    ]
    if not seleccionados:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Consulta no encontrada o sin servicios relacionados con pagos",
        )
    return [
        ServicioPagoRespuesta(
            servicio_realizado_id=servicio.id,
            servicio_id=servicio.servicio_id,
            nombre_servicio=servicio.servicio.nombre,
            fecha_realizacion=servicio.fecha_realizacion,
            precio_aplicado=(
                _decimal(servicio.precio_aplicado)
                if servicio.precio_aplicado is not None
                else None
            ),
            estado_pago=(
                "PAGADO" if servicio.id in cubiertos else "PENDIENTE"
            ),
        )
        for servicio in seleccionados
    ]


def validar_seleccion_servicios(
    db: Session,
    servicio_realizado_ids: list[int],
    *,
    paciente_id_esperado: int | None = None,
    servicios_precargados: list[ServicioRealizado] | None = None,
) -> ResultadoSeleccionServicios:
    if not servicio_realizado_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Debe seleccionar al menos un servicio",
        )
    if any(servicio_id <= 0 for servicio_id in servicio_realizado_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Todos los identificadores deben ser positivos",
        )
    if len(servicio_realizado_ids) != len(set(servicio_realizado_ids)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No se permiten servicios duplicados",
        )

    encontrados = servicios_precargados
    if encontrados is None:
        encontrados = repo.obtener_servicios_realizados_por_ids(
            db,
            servicio_realizado_ids,
        )
    por_id = {servicio.id: servicio for servicio in encontrados}
    faltantes = [
        servicio_id
        for servicio_id in servicio_realizado_ids
        if servicio_id not in por_id
    ]
    if faltantes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Servicios realizados no encontrados: {faltantes}",
        )
    servicios = [por_id[servicio_id] for servicio_id in servicio_realizado_ids]

    pacientes = {servicio.paciente_id for servicio in servicios}
    consultas = {servicio.consulta_clinica_id for servicio in servicios}
    if len(pacientes) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Todos los servicios deben pertenecer al mismo paciente",
        )
    if None in consultas or len(consultas) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Todos los servicios deben pertenecer a la misma consulta",
        )
    paciente_id = next(iter(pacientes))
    consulta_id = next(iter(consultas))
    if paciente_id_esperado is not None and paciente_id != paciente_id_esperado:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Los servicios no pertenecen al paciente autenticado",
        )
    if not repo.consulta_pertenece_a_paciente(db, consulta_id, paciente_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La consulta no pertenece al paciente de los servicios",
        )

    for servicio in servicios:
        if servicio.estado is not True:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"El servicio realizado {servicio.id} esta inactivo",
            )
        if (
            servicio.precio_aplicado is None
            or _decimal(servicio.precio_aplicado) <= Decimal("0")
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"El servicio realizado {servicio.id} no tiene un precio valido",
            )

    cubiertos = repo.obtener_ids_servicios_cubiertos_por_pago_aprobado(
        db,
        servicio_realizado_ids,
    )
    if cubiertos:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Servicios ya cubiertos por un pago aprobado: {sorted(cubiertos)}",
        )
    total = sum(
        (_decimal(servicio.precio_aplicado) for servicio in servicios),
        start=Decimal("0.00"),
    )
    return ResultadoSeleccionServicios(
        consulta_id=consulta_id,
        paciente_id=paciente_id,
        servicios=servicios,
        total=total,
    )


def monto_a_unidad_minima(monto: Decimal) -> int:
    monto_decimal = _decimal(monto)
    if not monto_decimal.is_finite() or monto_decimal <= Decimal("0"):
        raise ValueError("El monto debe ser un Decimal positivo y finito")
    normalizado = monto_decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(normalizado * 100)


def _moneda_stripe_configurada() -> str:
    moneda = config.STRIPE_CURRENCY.strip().lower()
    if len(moneda) != 3 or not moneda.isalpha():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="STRIPE_CURRENCY no tiene una configuracion valida",
        )
    return moneda


def _traducir_error_proveedor(error: Exception):
    if isinstance(error, ProveedorNoConfigurado):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        ) from error
    if isinstance(error, ErrorProveedorPago):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(error),
        ) from error
    raise error


def _validar_datos_intencion(
    intento,
    *,
    monto: Decimal,
    moneda: str,
) -> None:
    esperado = monto_a_unidad_minima(_decimal(monto))
    if intento.amount != esperado or intento.currency.lower() != moneda.lower():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La intencion Stripe no coincide con el monto o moneda del pago",
        )


def _validar_intencion_reutilizable(intento, pago) -> None:
    _validar_datos_intencion(
        intento,
        monto=pago.monto,
        moneda=pago.moneda,
    )
    if not es_intencion_reutilizable(intento.status) or not intento.client_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La intencion Stripe existente no es reutilizable",
        )


def crear_intencion_stripe(
    db: Session,
    servicio_realizado_ids: list[int],
    usuario: Usuario,
    proveedor: ProveedorPagoBase,
) -> IntencionPagoStripeRespuesta:
    paciente = _resolver_paciente_actual(db, usuario)
    try:
        proveedor.validar_configuracion()
    except (ProveedorNoConfigurado, ErrorProveedorPago) as error:
        _traducir_error_proveedor(error)

    ids_solicitados = frozenset(servicio_realizado_ids)

    # Primera fase: se obtiene una fotografia consistente de posibles intentos
    # previos. El commit libera todos los locks antes de consultar Stripe.
    try:
        bloqueados = repo.obtener_servicios_realizados_por_ids_para_actualizacion(
            db,
            servicio_realizado_ids,
        )
        seleccion = validar_seleccion_servicios(
            db,
            servicio_realizado_ids,
            paciente_id_esperado=paciente.id,
            servicios_precargados=bloqueados,
        )
        candidatos = repo.listar_pagos_stripe_candidatos_reintento(
            db,
            servicio_realizado_ids,
        )
        referencias = {
            pago.referencia_transaccion: (
                _decimal(pago.monto),
                pago.moneda,
            )
            for pago in candidatos
            if pago.referencia_transaccion
        }
        db.commit()
    except Exception:
        db.rollback()
        raise

    # Stripe se consulta sin mantener locks de PostgreSQL.
    intentos_por_referencia = {}
    try:
        for referencia, (monto, moneda) in referencias.items():
            intento_previo = proveedor.recuperar_intencion(referencia)
            _validar_datos_intencion(
                intento_previo,
                monto=monto,
                moneda=moneda,
            )
            if not (
                es_intencion_reutilizable(intento_previo.status)
                or es_intencion_terminal(intento_previo.status)
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Stripe devolvio un estado de PaymentIntent no soportado",
                )
            intentos_por_referencia[referencia] = intento_previo
    except (ProveedorNoConfigurado, ErrorProveedorPago) as error:
        _traducir_error_proveedor(error)

    # Segunda fase: se vuelven a adquirir los locks y se revalida todo. Esto
    # cierra la ventana entre la consulta externa y la decision de persistencia.
    intento = None
    try:
        bloqueados = repo.obtener_servicios_realizados_por_ids_para_actualizacion(
            db,
            servicio_realizado_ids,
        )
        seleccion = validar_seleccion_servicios(
            db,
            servicio_realizado_ids,
            paciente_id_esperado=paciente.id,
            servicios_precargados=bloqueados,
        )
        candidatos = repo.listar_pagos_stripe_candidatos_reintento(
            db,
            servicio_realizado_ids,
        )

        activos = []
        cancelados = []
        succeeded = []
        for candidato in candidatos:
            ids_candidato = frozenset(
                detalle.servicio_realizado_id
                for detalle in candidato.detalles
            )
            referencia = candidato.referencia_transaccion
            if referencia is None:
                if candidato.estado == "PENDIENTE":
                    activos.append((candidato, ids_candidato, None))
                continue
            intento_previo = intentos_por_referencia.get(referencia)
            if intento_previo is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="El estado del intento cambio; vuelva a intentar",
                )
            _validar_datos_intencion(
                intento_previo,
                monto=candidato.monto,
                moneda=candidato.moneda,
            )
            if es_intencion_reutilizable(intento_previo.status):
                activos.append((candidato, ids_candidato, intento_previo))
            elif intento_previo.status == "canceled":
                cancelados.append(candidato)
            elif intento_previo.status == "succeeded":
                succeeded.append(candidato)

        if succeeded:
            for candidato in succeeded:
                registrar_bitacora(
                    db=db,
                    usuario_id=usuario.id,
                    accion="PAGO_STRIPE_SUCCEEDED_PENDIENTE_WEBHOOK",
                    entidad_afectada="pago",
                    id_registro_afectado=candidato.id,
                    descripcion=(
                        "Stripe reporta succeeded; se bloquea un nuevo cobro "
                        "hasta procesar el webhook"
                    ),
                )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Existe un PaymentIntent exitoso pendiente de conciliacion",
            )

        parciales = [item for item in activos if item[1] != ids_solicitados]
        if parciales:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Existen servicios asociados a otro intento Stripe activo",
            )
        exactos = [item for item in activos if item[1] == ids_solicitados]
        if len(exactos) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Existen multiples intentos Stripe activos incompatibles",
            )

        for cancelado in cancelados:
            repo.actualizar_estado_pago(db, cancelado, estado="ANULADO")
            registrar_bitacora(
                db=db,
                usuario_id=usuario.id,
                accion="RECONCILIAR_PAGO_STRIPE_ANULADO",
                entidad_afectada="pago",
                id_registro_afectado=cancelado.id,
                descripcion="PaymentIntent cancelado confirmado antes del reintento",
            )

        if exactos:
            pago, _, intento = exactos[0]
            if _decimal(pago.monto) != seleccion.total:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="El pago existente no coincide con el total actual",
                )
            if pago.estado == "RECHAZADO":
                repo.actualizar_estado_pago(db, pago, estado="PENDIENTE")
                registrar_bitacora(
                    db=db,
                    usuario_id=usuario.id,
                    accion="REINTENTAR_PAGO_STRIPE",
                    entidad_afectada="pago",
                    id_registro_afectado=pago.id,
                    descripcion="Pago Stripe rechazado reabierto con el mismo intento",
                )
        else:
            pago = repo.crear_pago_stripe_pendiente(
                db,
                monto=seleccion.total,
                moneda=_moneda_stripe_configurada().upper(),
            )
            repo.crear_detalles_pago(
                db,
                pago_id=pago.id,
                servicios=seleccion.servicios,
            )
            registrar_bitacora(
                db=db,
                usuario_id=usuario.id,
                accion="INICIAR_PAGO_STRIPE",
                entidad_afectada="pago",
                id_registro_afectado=pago.id,
                descripcion="Pago Stripe pendiente creado",
            )
        db.commit()
        db.refresh(pago)
    except Exception:
        db.rollback()
        raise

    amount = monto_a_unidad_minima(_decimal(pago.monto))
    if intento is None:
        try:
            intento = proveedor.crear_intencion(
                amount=amount,
                currency=pago.moneda.lower(),
                metadata={
                    "pago_id": str(pago.id),
                    "consulta_clinica_id": str(seleccion.consulta_id),
                },
                idempotency_key=f"clinica-pago-{pago.id}",
            )
            _validar_intencion_reutilizable(intento, pago)
        except (ProveedorNoConfigurado, ErrorProveedorPago) as error:
            _traducir_error_proveedor(error)
    else:
        _validar_intencion_reutilizable(intento, pago)

    if pago.referencia_transaccion is None:
        try:
            repo.asignar_referencia_transaccion(db, pago, intento.id)
            db.commit()
            db.refresh(pago)
        except Exception:
            db.rollback()
            raise
    elif pago.referencia_transaccion != intento.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La referencia Stripe del pago no coincide",
        )

    return IntencionPagoStripeRespuesta(
        pago_id=pago.id,
        consulta_clinica_id=seleccion.consulta_id,
        payment_intent_id=intento.id,
        client_secret=intento.client_secret,
        monto=_decimal(pago.monto),
        moneda=pago.moneda,
        estado_pago=pago.estado,
    )


_EVENTOS_STRIPE = {
    "payment_intent.succeeded": "APROBADO",
    "payment_intent.payment_failed": "RECHAZADO",
    "payment_intent.processing": "PENDIENTE",
    "payment_intent.canceled": "ANULADO",
}


def _fecha_evento_stripe(evento: dict[str, Any]) -> datetime:
    timestamp = evento.get("created")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return datetime.now(timezone.utc)


def procesar_webhook_stripe(
    db: Session,
    payload: bytes,
    firma: str | None,
    proveedor: ProveedorPagoBase,
) -> WebhookStripeRespuesta:
    try:
        proveedor.validar_configuracion(webhook=True)
        evento = proveedor.verificar_webhook(payload, firma)
    except ProveedorNoConfigurado as error:
        _traducir_error_proveedor(error)
    except ErrorFirmaWebhook as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ErrorProveedorPago as error:
        _traducir_error_proveedor(error)

    tipo = evento.get("type")
    estado_destino = _EVENTOS_STRIPE.get(tipo)
    if estado_destino is None:
        return WebhookStripeRespuesta(procesado=False)

    objeto = evento.get("data", {}).get("object", {})
    payment_intent_id = objeto.get("id")
    if not payment_intent_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El evento no contiene un PaymentIntent valido",
        )

    try:
        pago_previo = repo.obtener_pago_por_referencia_transaccion(
            db,
            payment_intent_id,
        )
        if pago_previo is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pago asociado al PaymentIntent no encontrado",
            )
        detalles = repo.listar_detalles_pago(db, pago_previo.id)
        if not detalles:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El pago Stripe no tiene servicios asociados",
            )
        repo.obtener_servicios_realizados_por_ids_para_actualizacion(
            db,
            sorted(detalle.servicio_realizado_id for detalle in detalles),
        )
        pago = repo.obtener_pago_por_referencia_para_actualizacion(
            db,
            payment_intent_id,
        )
        amount = objeto.get("amount")
        currency = str(objeto.get("currency", "")).lower()
        if (
            amount != monto_a_unidad_minima(_decimal(pago.monto))
            or currency != pago.moneda.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Monto o moneda Stripe inconsistente con el pago",
            )

        if estado_destino == "APROBADO" and pago.estado != "APROBADO":
            otros_aprobados = (
                repo.obtener_pagos_aprobados_para_servicios_excluyendo(
                    db,
                    [detalle.servicio_realizado_id for detalle in detalles],
                    pago_id_excluido=pago.id,
                )
            )
            if otros_aprobados:
                registrar_bitacora(
                    db=db,
                    usuario_id=None,
                    accion="CONFLICTO_DOBLE_COBRO_STRIPE",
                    entidad_afectada="pago",
                    id_registro_afectado=pago.id,
                    descripcion=(
                        "PaymentIntent succeeded sobre servicios ya cubiertos "
                        f"por pagos aprobados: {sorted(otros_aprobados)}"
                    ),
                )
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Conflicto financiero: los servicios ya estan cubiertos "
                        "por otro pago aprobado"
                    ),
                )

        estado_anterior = pago.estado
        if pago.estado != "APROBADO":
            puede_cambiar = (
                pago.estado == "PENDIENTE"
                or (
                    pago.estado == "RECHAZADO"
                    and estado_destino == "APROBADO"
                )
            )
            if puede_cambiar:
                fecha_pago = (
                    _fecha_evento_stripe(evento)
                    if estado_destino == "APROBADO"
                    else None
                )
                repo.actualizar_estado_pago(
                    db,
                    pago,
                    estado=estado_destino,
                    fecha_hora_pago=fecha_pago,
                )

        if pago.estado != estado_anterior:
            acciones = {
                "APROBADO": "PAGO_STRIPE_APROBADO",
                "RECHAZADO": "PAGO_STRIPE_RECHAZADO",
                "ANULADO": "PAGO_STRIPE_ANULADO",
            }
            accion = acciones.get(pago.estado)
            if accion:
                registrar_bitacora(
                    db=db,
                    usuario_id=None,
                    accion=accion,
                    entidad_afectada="pago",
                    id_registro_afectado=pago.id,
                    descripcion=f"Webhook Stripe actualizo pago a {pago.estado}",
                )
        db.commit()
        db.refresh(pago)
        return WebhookStripeRespuesta(
            procesado=True,
            pago_id=pago.id,
            estado_pago=pago.estado,
        )
    except Exception:
        db.rollback()
        raise


def consultar_estado_pago_stripe(
    db: Session,
    pago_id: int,
    usuario: Usuario,
) -> EstadoPagoStripeRespuesta:
    paciente = _resolver_paciente_actual(db, usuario)
    pago = repo.obtener_pago_por_id_con_servicios(db, pago_id)
    if pago is None or pago.pasarela != "STRIPE":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pago Stripe no encontrado",
        )
    pacientes = {
        detalle.servicio_realizado.paciente_id
        for detalle in pago.detalles
        if detalle.servicio_realizado is not None
    }
    if pacientes != {paciente.id}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El pago no pertenece al paciente autenticado",
        )
    consultas = {
        detalle.servicio_realizado.consulta_clinica_id
        for detalle in pago.detalles
        if detalle.servicio_realizado is not None
    }
    if None in consultas or len(consultas) != 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El pago no tiene una consulta clinica consistente",
        )
    return EstadoPagoStripeRespuesta(
        pago_id=pago.id,
        consulta_clinica_id=next(iter(consultas)),
        estado_pago=pago.estado,
        monto=_decimal(pago.monto),
        moneda=pago.moneda,
        payment_intent_id=pago.referencia_transaccion,
    )


# =========================================================
# ETAPA 8.1 - HISTORIAL Y COMPROBANTES (solo lectura)
# =========================================================

_ESTADOS_CON_COMPROBANTE = frozenset({"APROBADO"})


def _detalles_pertenecen_al_paciente(pago, paciente_id: int) -> bool:
    """True solo si *todos* los detalles pertenecen al paciente indicado.

    Se exige que el pago tenga al menos un detalle y que ninguna de sus
    relaciones apunte a otro paciente, evitando filtrar informacion ajena.
    """
    if not pago.detalles:
        return False
    for detalle in pago.detalles:
        servicio = detalle.servicio_realizado
        if servicio is None or servicio.paciente_id != paciente_id:
            return False
    return True


def _nombre_servicio(servicio) -> str:
    catalogo = servicio.servicio
    return catalogo.nombre if catalogo is not None else "Servicio"


def listar_mis_pagos(
    db: Session,
    usuario: Usuario,
) -> list[PagoHistorialItem]:
    """Historial de pagos del paciente autenticado, del mas reciente al mas antiguo."""
    paciente = _resolver_paciente_actual(db, usuario)
    pago_ids = repo.listar_ids_pagos_de_paciente(db, paciente.id)
    pagos = repo.obtener_pagos_con_detalles(db, pago_ids)

    historial: list[PagoHistorialItem] = []
    for pago in pagos:
        if not _detalles_pertenecen_al_paciente(pago, paciente.id):
            # Relaciones inconsistentes (incluye pagos compartidos con otros
            # pacientes): se omite por completo para no exponer datos ajenos.
            continue
        servicios = []
        consultas: set[int | None] = set()
        for detalle in pago.detalles:
            servicio = detalle.servicio_realizado
            consultas.add(servicio.consulta_clinica_id)
            servicios.append(
                ServicioPagoHistorial(
                    servicio_realizado_id=servicio.id,
                    nombre_servicio=_nombre_servicio(servicio),
                    monto_aplicado=_decimal(detalle.monto_aplicado),
                )
            )
        consulta_id = next(iter(consultas)) if len(consultas) == 1 else None
        historial.append(
            PagoHistorialItem(
                pago_id=pago.id,
                consulta_clinica_id=consulta_id,
                fecha_creacion=pago.fecha_creacion,
                fecha_hora_pago=pago.fecha_hora_pago,
                monto=_decimal(pago.monto),
                moneda=pago.moneda,
                metodo_pago=pago.metodo_pago,
                estado_pago=pago.estado,
                pasarela=pago.pasarela,
                servicios=servicios,
            )
        )
    return historial


def generar_comprobante_pago(
    db: Session,
    pago_id: int,
    usuario: Usuario,
) -> tuple[bytes, str]:
    """Genera el comprobante PDF de un pago aprobado del paciente autenticado.

    Devuelve una tupla ``(contenido_pdf, nombre_archivo)``. Es una operacion
    de solo lectura: no modifica el estado del pago ni escribe en la base.
    """
    paciente = _resolver_paciente_actual(db, usuario)
    pago = repo.obtener_pago_para_comprobante(db, pago_id)
    if pago is None or not _detalles_pertenecen_al_paciente(pago, paciente.id):
        # 404 uniforme: no revela la existencia de pagos de terceros.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comprobante no disponible",
        )
    if pago.estado not in _ESTADOS_CON_COMPROBANTE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El comprobante solo esta disponible para pagos APROBADOS "
                f"(estado actual: {pago.estado})"
            ),
        )

    servicios = []
    consultas: set[int] = set()
    consulta = None
    for detalle in pago.detalles:
        servicio = detalle.servicio_realizado
        if servicio.consulta_clinica_id is not None:
            consultas.add(servicio.consulta_clinica_id)
        if consulta is None and servicio.consulta_clinica is not None:
            consulta = servicio.consulta_clinica
        servicios.append(
            comprobante_pdf.ServicioComprobante(
                nombre=_nombre_servicio(servicio),
                monto=_decimal(detalle.monto_aplicado),
            )
        )

    total = _decimal(pago.monto)
    suma_detalles = sum(
        (servicio.monto for servicio in servicios),
        start=Decimal("0.00"),
    )
    if suma_detalles != total:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El pago presenta una inconsistencia entre el monto total "
                "y el detalle de servicios"
            ),
        )

    consulta_id = next(iter(consultas)) if len(consultas) == 1 else None
    datos = comprobante_pdf.DatosComprobante(
        pago_id=pago.id,
        consulta_clinica_id=consulta_id,
        consulta_fecha=consulta.fecha_consulta if consulta is not None else None,
        fecha_pago=pago.fecha_hora_pago,
        fecha_emision=datetime.now(timezone.utc),
        estado=pago.estado,
        metodo_pago=pago.metodo_pago,
        pasarela=pago.pasarela,
        moneda=pago.moneda,
        referencia_transaccion=pago.referencia_transaccion,
        paciente_nombres=paciente.nombres,
        paciente_apellidos=paciente.apellidos,
        monto_total=total,
        servicios=servicios,
    )
    contenido = comprobante_pdf.generar_comprobante_pdf(datos)
    return contenido, f"comprobante_pago_{pago.id}.pdf"
