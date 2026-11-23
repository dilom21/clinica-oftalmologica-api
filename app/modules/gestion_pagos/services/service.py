from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.dependencies import nombre_rol_actual
from app.core import config
from app.modules.gestion_historial_clinico.models.models import ServicioRealizado
from app.modules.gestion_pacientes.repositories import repository as paciente_repo
from app.modules.gestion_pagos.repositories import repository as repo
from app.modules.gestion_pagos.repositories import administrativos as admin_repo
from app.modules.gestion_pagos.repositories import planes_cuotas as planes_repo
from app.modules.gestion_pagos.repositories import saldos
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
from app.modules.gestion_pagos.services.webhook_tenancy import (
    StripeWebhookScope,
    StripeWebhookSessionResolver,
)
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


@dataclass(frozen=True, slots=True)
class StripePaymentScope:
    """Server-derived identity used for Stripe metadata and idempotency."""

    metadata: dict[str, str]
    idempotency_prefix: str


def _stripe_payment_scope(db: Session) -> StripePaymentScope:
    context = db.info.get("tenant_context")
    if context is None:
        return StripePaymentScope(metadata={}, idempotency_prefix="clinica")
    if (
        context.empresa_id <= 0
        or context.tenant_database_id <= 0
        or not context.empresa_codigo
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="El contexto tenant del pago no es valido",
        )
    return StripePaymentScope(
        metadata={
            "clinica_contexto": "tenant",
            "empresa_id": str(context.empresa_id),
            "empresa_codigo": context.empresa_codigo,
            "tenant_id": str(context.tenant_database_id),
        },
        idempotency_prefix=f"clinica-tenant-{context.tenant_database_id}",
    )


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
    ids = [servicio.id for servicio in servicios]
    cubiertos = repo.obtener_ids_servicios_cubiertos_por_pago_aprobado(
        db,
        ids,
    )
    montos_aprobados = planes_repo.montos_aprobados_por_servicio(db, ids)
    parciales = {
        servicio_id
        for servicio_id, monto in montos_aprobados.items()
        if _decimal(monto) > Decimal("0") and servicio_id not in cubiertos
    }
    visibles = [
        servicio
        for servicio in servicios
        if servicio.id in cubiertos
        or (
            servicio.id not in parciales
            and es_servicio_pagable(
                servicio,
                cubierto_por_pago_aprobado=False,
            )
        )
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
    montos_aprobados = planes_repo.montos_aprobados_por_servicio(
        db, servicio_realizado_ids,
    )
    parciales = sorted(
        servicio_id
        for servicio_id, monto in montos_aprobados.items()
        if _decimal(monto) > Decimal("0")
    )
    if parciales:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Servicios con cobros parciales aprobados: {parciales}",
        )
    reservados = planes_repo.reservas_activas_por_servicio(
        db, servicio_realizado_ids,
    )
    if reservados:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Servicios reservados por un plan de pago: "
                f"{sorted(reservados)}"
            ),
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
    payment_scope = _stripe_payment_scope(db)
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
        # La misma fila de servicio queda bloqueada para Stripe y para CU31.
        # Así un pago manual PENDIENTE no puede ser ignorado por un intento
        # electrónico concurrente.
        conflictos_manuales = [
            pago
            for pago in admin_repo.pagos_conflictivos_para_servicios(
                db, servicio_realizado_ids,
            )
            if pago.pasarela is None and pago.estado == "PENDIENTE"
        ]
        if conflictos_manuales:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Existe un pago manual pendiente para los servicios; "
                    "debe confirmarse o conciliarse antes de iniciar Stripe"
                ),
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
            # E-6: ninguna anulacion de intento ocurre fuera del libro CU36.
            from app.modules.gestion_pagos.services import ajustes as ajustes_service

            ajustes_service.anular_intento_no_capturado(
                db,
                cancelado,
                motivo="PaymentIntent cancelado confirmado antes del reintento",
                clave_idempotencia=f"cu31-reintento-cancelado-{cancelado.id}",
                usuario_id=usuario.id,
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
                    **payment_scope.metadata,
                    "pago_id": str(pago.id),
                    "consulta_clinica_id": str(seleccion.consulta_id),
                },
                idempotency_key=(
                    f"{payment_scope.idempotency_prefix}-pago-{pago.id}"
                ),
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
_EVENTOS_REEMBOLSO = frozenset(
    {"refund.created", "refund.updated", "refund.failed"}
)


def _procesar_evento_reembolso_stripe(
    db: Session,
    evento: dict[str, Any],
    scope: StripeWebhookScope,
) -> WebhookStripeRespuesta:
    """CU36: aplica refund.* de forma idempotente y sin simular reembolsos."""
    from app.modules.gestion_pagos.providers import ReembolsoProveedor
    from app.modules.gestion_pagos.repositories import ajustes as ajustes_repo
    from app.modules.gestion_pagos.services import ajustes as ajustes_service

    objeto = evento.get("data", {}).get("object", {})
    refund_id = objeto.get("id")
    if not refund_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El evento no contiene un refund valido",
        )
    ajuste_id = scope.ajuste_id
    if ajuste_id is None:
        # E-3/I-2: un refund sin metadata de ajuste/tenant no se descarta con
        # 409. Se registra durablemente para conciliacion manual y solo
        # entonces se responde 2xx a Stripe. Un evento cuyo PaymentIntent no
        # prueba ningun tenant se guarda en la cuarentena central del Control
        # Plane: nunca se envia automaticamente a la base legacy.
        from app.modules.gestion_pagos.services import huerfanos

        if scope.is_orphan:
            huerfanos.registrar_evento_huerfano_central(db, evento)
        else:
            huerfanos.registrar_evento_huerfano(db, evento)
        return WebhookStripeRespuesta(procesado=False)
    try:
        ajuste = ajustes_repo.obtener_ajuste(db, ajuste_id, bloquear=True)
        if ajuste is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ajuste de reembolso no encontrado",
            )
        if scope.pago_id is not None and ajuste.pago_id != scope.pago_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La metadata Stripe no corresponde al ajuste",
            )
        _, _, pago = ajustes_service._bloquear_pago(db, ajuste.pago_id)
        amount = objeto.get("amount")
        currency = str(objeto.get("currency", "")).lower()
        if (
            int(amount or 0) != monto_a_unidad_minima(abs(_decimal(ajuste.importe)))
            or currency != pago.moneda.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Monto o moneda del reembolso inconsistente con el ajuste",
            )
        reembolso = ReembolsoProveedor(
            id=str(refund_id),
            payment_intent_id=objeto.get("payment_intent"),
            amount=int(amount or 0),
            currency=currency,
            status=str(objeto.get("status", "")),
            failure_reason=objeto.get("failure_reason"),
            metadata={
                str(clave): str(valor)
                for clave, valor in dict(objeto.get("metadata") or {}).items()
                if valor is not None
            },
        )
        if ajuste.estado == "CONFIRMADO" and reembolso.confirmed:
            db.rollback()
            return WebhookStripeRespuesta(
                procesado=True, pago_id=pago.id, estado_pago=pago.estado,
            )
        estado_anterior = ajuste.estado
        ajuste = ajustes_service.aplicar_reembolso_proveedor(
            db, ajuste, reembolso, usuario_id=None,
        )
        if ajuste.estado != estado_anterior:
            registrar_bitacora(
                db=db,
                usuario_id=None,
                accion=f"REEMBOLSO_STRIPE_{ajuste.estado}",
                entidad_afectada="pago_ajuste",
                id_registro_afectado=ajuste.id,
                descripcion=(
                    f"Webhook {evento.get('type')} actualizo el ajuste a "
                    f"{ajuste.estado}"
                ),
            )
        db.commit()
        db.refresh(pago)
        return WebhookStripeRespuesta(
            procesado=True, pago_id=pago.id, estado_pago=pago.estado,
        )
    except Exception:
        db.rollback()
        raise


def _reconciliar_succeeded_tardio(
    db: Session,
    pago,
    intento_cuota,
    evento: dict[str, Any],
) -> None:
    """CU36: Stripe capturo dinero despues de una anulacion local.

    No se ignora el webhook: se restaura la representacion financiera
    (dinero capturado) y se abre una conciliacion explicita para reembolsar.
    """
    from decimal import Decimal

    from app.modules.gestion_pagos.repositories import ajustes as ajustes_repo
    from app.modules.gestion_pagos.services import notificaciones

    fecha_pago = _fecha_evento_stripe(evento)
    clave = f"cu36-reconciliacion-{pago.id}"
    pago.estado = "APROBADO"
    pago.fecha_hora_pago = pago.fecha_hora_pago or fecha_pago
    if intento_cuota is not None and not intento_cuota.aplicado_a_cuota:
        from app.modules.gestion_pagos.services import planes_cuotas

        planes_cuotas.aplicar_pago_desde_webhook(
            db, intento_cuota, fecha_pago=fecha_pago,
        )
    if ajustes_repo.obtener_ajuste_por_clave(db, clave) is None:
        ajustes_repo.crear_ajuste(
            db,
            pago_id=pago.id,
            tipo="RECONCILIACION",
            naturaleza="OTRO",
            efecto_caja="SIN_EFECTO",
            estado="PENDIENTE_PROVEEDOR",
            importe=Decimal("0.00"),
            moneda=pago.moneda,
            motivo=(
                "Stripe confirmo la captura despues de una anulacion local; "
                "requiere conciliacion y reembolso si corresponde"
            ),
            evidencia=f"payment_intent {pago.referencia_transaccion}",
            clave_idempotencia=clave,
            creado_por=None,
            observaciones="Ajuste abierto automaticamente por webhook",
        )
    notificaciones.programar_comprobante(db, pago.id)
    registrar_bitacora(
        db=db,
        usuario_id=None,
        accion="RECONCILIAR_STRIPE_SUCCEEDED_TARDIO",
        entidad_afectada="pago",
        id_registro_afectado=pago.id,
        descripcion=(
            "Stripe reporto succeeded sobre un pago ANULADO; se restauro el "
            "dinero capturado y se abrio conciliacion"
        ),
    )
    db.flush()


def _fecha_evento_stripe(evento: dict[str, Any]) -> datetime:
    timestamp = evento.get("created")
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _registrar_discrepancia_canceled(
    db: Session,
    pago,
    evento: dict[str, Any],
) -> None:
    """CU36 (I-3): Stripe cancelo un intento que localmente figura APROBADO.

    Un pago APROBADO nunca se anula automaticamente: puede representar dinero
    realmente capturado. La discrepancia se registra de forma durable e
    idempotente para que Stripe reciba 2xx (sin reintentos infinitos) y para
    que un administrador la concilie antes de cualquier otra operacion.
    """
    from decimal import Decimal

    from app.modules.gestion_pagos.repositories import ajustes as ajustes_repo

    clave = f"cu36-stripe-canceled-discrepancia-{pago.id}"
    if ajustes_repo.obtener_ajuste_por_clave(db, clave) is not None:
        return
    ajuste = ajustes_repo.crear_ajuste(
        db,
        pago_id=pago.id,
        tipo="RECONCILIACION",
        naturaleza="OTRO",
        efecto_caja="SIN_EFECTO",
        estado="PENDIENTE_PROVEEDOR",
        importe=Decimal("0.00"),
        moneda=pago.moneda,
        motivo=(
            "Stripe reporto payment_intent.canceled sobre un pago APROBADO; "
            "requiere conciliacion manual (no se anula automaticamente)"
        ),
        evidencia=f"Webhook {evento.get('id')} payment_intent.canceled",
        clave_idempotencia=clave,
        creado_por=None,
        observaciones="Discrepancia abierta automaticamente por webhook",
    )
    registrar_bitacora(
        db=db,
        usuario_id=None,
        accion="RECONCILIAR_STRIPE_CANCELED_SOBRE_APROBADO",
        entidad_afectada="pago_ajuste",
        id_registro_afectado=ajuste.id,
        descripcion=(
            "Stripe cancelo un PaymentIntent que localmente figura APROBADO; "
            "se abrio conciliacion y no se anulo el pago"
        ),
    )
    db.flush()


def verificar_evento_webhook_stripe(
    payload: bytes,
    firma: str | None,
    proveedor: ProveedorPagoBase,
) -> dict[str, Any]:
    try:
        proveedor.validar_configuracion(webhook=True)
        return proveedor.verificar_webhook(payload, firma)
    except ProveedorNoConfigurado as error:
        _traducir_error_proveedor(error)
    except ErrorFirmaWebhook as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ErrorProveedorPago as error:
        _traducir_error_proveedor(error)


def procesar_evento_webhook_stripe(
    db: Session,
    evento: dict[str, Any],
    scope: StripeWebhookScope,
) -> WebhookStripeRespuesta:
    tipo = evento.get("type")
    if tipo in _EVENTOS_REEMBOLSO:
        return _procesar_evento_reembolso_stripe(db, evento, scope)
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
        metadata_pago_id = scope.pago_id
        if metadata_pago_id is not None and metadata_pago_id != pago_previo.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La metadata Stripe no corresponde al pago encontrado",
            )
        detalles = repo.listar_detalles_pago(db, pago_previo.id)
        if not detalles:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El pago Stripe no tiene servicios asociados",
            )
        consultas = {
            detalle.servicio_realizado.consulta_clinica_id
            for detalle in detalles
            if detalle.servicio_realizado is not None
        }
        metadata_consulta_id = scope.consulta_clinica_id
        if (
            metadata_consulta_id is not None
            and consultas != {metadata_consulta_id}
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="La metadata Stripe no corresponde a la consulta del pago",
            )
        intento_previo = planes_repo.obtener_intento_por_pago(
            db,
            pago_previo.id,
        )
        cuota = None
        if intento_previo is not None:
            cuota, intento_cuota, pago = planes_repo.bloquear_operacion_por_pago(
                db,
                pago_previo.id,
            )
            if cuota is None or intento_cuota is None or pago is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="El intento de cuota cambio durante el procesamiento",
                )
        else:
            repo.obtener_servicios_realizados_por_ids_para_actualizacion(
                db,
                sorted(detalle.servicio_realizado_id for detalle in detalles),
            )
            pago = repo.obtener_pago_por_referencia_para_actualizacion(
                db,
                payment_intent_id,
            )
            intento_cuota = None
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

        if intento_cuota is not None:
            if (
                scope.cuota_id != cuota.id
                or scope.plan_pago_id != cuota.plan_pago_id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="La metadata Stripe no corresponde a la cuota del pago",
                )
            # E-5: una captura tardia sobre un pago de cuota previamente
            # ANULADO tambien debe reconciliarse. Antes la rama de cuotas
            # retornaba antes de esta comprobacion y revertia la anulacion sin
            # ajuste RECONCILIACION ni bitacora CU36.
            if estado_destino == "APROBADO" and pago.estado == "ANULADO":
                _reconciliar_succeeded_tardio(db, pago, intento_cuota, evento)
                db.commit()
                db.refresh(pago)
                return WebhookStripeRespuesta(
                    procesado=True,
                    pago_id=pago.id,
                    estado_pago=pago.estado,
                )
            if estado_destino == "APROBADO":
                from app.modules.gestion_pagos.services import planes_cuotas

                if not intento_cuota.aplicado_a_cuota:
                    planes_cuotas.aplicar_pago_desde_webhook(
                        db,
                        intento_cuota,
                        fecha_pago=_fecha_evento_stripe(evento),
                    )
                db.commit()
                db.refresh(pago)
                return WebhookStripeRespuesta(
                    procesado=True,
                    pago_id=pago.id,
                    estado_pago=pago.estado,
                )

        if (
            intento_cuota is None
            and estado_destino == "APROBADO"
            and pago.estado != "APROBADO"
        ):
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
        if estado_destino == "APROBADO" and pago.estado == "ANULADO":
            _reconciliar_succeeded_tardio(db, pago, intento_cuota, evento)
            db.commit()
            db.refresh(pago)
            return WebhookStripeRespuesta(
                procesado=True,
                pago_id=pago.id,
                estado_pago=pago.estado,
            )
        # I-3: toda anulacion de un intento no capturado pasa por el libro
        # CU36 (ajuste ANULACION + bitacora). Un pago APROBADO nunca se anula
        # automaticamente: se registra una discrepancia durable y se responde
        # 2xx para no provocar reintentos infinitos de Stripe.
        if estado_destino == "ANULADO":
            if pago.estado in {"PENDIENTE", "RECHAZADO"}:
                from app.modules.gestion_pagos.services import ajustes as ajustes_service

                ajustes_service.anular_intento_no_capturado(
                    db,
                    pago,
                    motivo="PaymentIntent cancelado confirmado por Stripe",
                    clave_idempotencia=f"cu36-stripe-canceled-{pago.id}",
                    usuario_id=None,
                    evidencia=(
                        f"Webhook {evento.get('id')} payment_intent.canceled"
                    ),
                )
            elif pago.estado == "APROBADO":
                _registrar_discrepancia_canceled(db, pago, evento)
            # ANULADO/REEMBOLSADO: estado final ya durable -> no-op idempotente.
            db.commit()
            db.refresh(pago)
            return WebhookStripeRespuesta(
                procesado=True,
                pago_id=pago.id,
                estado_pago=pago.estado,
            )

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
            if pago.estado == "APROBADO":
                # Outbox en el mismo commit financiero. El correo y el PDF se
                # procesan fuera del webhook, sin llamadas de red bajo locks.
                from app.modules.gestion_pagos.services import notificaciones

                notificaciones.programar_comprobante(db, pago.id)
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


def procesar_webhook_stripe(
    db: Session,
    payload: bytes,
    firma: str | None,
    proveedor: ProveedorPagoBase,
) -> WebhookStripeRespuesta:
    """Legacy-compatible service entry point used by direct unit callers."""
    evento = verificar_evento_webhook_stripe(payload, firma, proveedor)
    objeto = evento.get("data", {}).get("object", {})
    metadata = objeto.get("metadata") or {}
    tenant_markers = {
        "clinica_contexto", "empresa_id", "empresa_codigo", "tenant_id"
    }.intersection(metadata)
    if tenant_markers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un evento tenant requiere resolucion segura de su base clinica",
        )
    return procesar_evento_webhook_stripe(
        db,
        evento,
        StripeWebhookScope(is_tenant=False, metadata=metadata),
    )


def procesar_webhook_stripe_en_base_correcta(
    payload: bytes,
    firma: str | None,
    proveedor: ProveedorPagoBase,
    session_resolver: StripeWebhookSessionResolver,
) -> WebhookStripeRespuesta:
    """Verify the Stripe signature, then and only then select a database."""
    evento = verificar_evento_webhook_stripe(payload, firma, proveedor)
    if (
        _EVENTOS_STRIPE.get(evento.get("type")) is None
        and evento.get("type") not in _EVENTOS_REEMBOLSO
    ):
        return WebhookStripeRespuesta(procesado=False)
    with session_resolver.open_for_verified_event(evento, proveedor) as (db, scope):
        return procesar_evento_webhook_stripe(db, evento, scope)


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
    resumenes = saldos.resumenes_por_pago(db, [pago.id for pago in pagos])

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
        resumen = resumenes[pago.id]
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
                importe_vigente=resumen.vigente,
                importe_reembolsado=resumen.reembolsado,
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
    return _generar_comprobante_validado(
        pago,
        paciente,
        _nombre_clinica_comprobante(db),
    )


def generar_comprobante_interno(
    db: Session,
    pago_id: int,
) -> tuple[bytes, str]:
    """Genera el mismo PDF para procesos internos ya autorizados (admin/outbox)."""
    pago = repo.obtener_pago_para_comprobante(db, pago_id)
    if pago is None or not pago.detalles:
        raise HTTPException(status_code=404, detail="Comprobante no disponible")
    pacientes = {
        detalle.servicio_realizado.paciente_id
        for detalle in pago.detalles
        if detalle.servicio_realizado is not None
    }
    if len(pacientes) != 1:
        raise HTTPException(
            status_code=409,
            detail="El pago no tiene un paciente consistente",
        )
    paciente = paciente_repo.obtener_paciente_por_id(db, next(iter(pacientes)))
    if paciente is None or not _detalles_pertenecen_al_paciente(pago, paciente.id):
        raise HTTPException(
            status_code=409,
            detail="El pago no tiene un paciente consistente",
        )
    return _generar_comprobante_validado(
        pago,
        paciente,
        _nombre_clinica_comprobante(db),
    )


def _nombre_clinica_comprobante(db: Session) -> str:
    contexto = db.info.get("tenant_context")
    if contexto is None:
        return "CLÍNICA OFTALMOLÓGICA"

    nombre = str(getattr(contexto, "empresa_nombre", "") or "").strip()
    codigo = str(getattr(contexto, "empresa_codigo", "") or "").strip()
    empresa_id = getattr(contexto, "empresa_id", None)
    tenant_id = getattr(contexto, "tenant_database_id", None)
    if not nombre or not codigo or not empresa_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo resolver con seguridad la clinica del comprobante",
        )

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        database_name = str(
            getattr(contexto, "database_name", "") or ""
        ).strip()
        actual = db.scalar(text("select current_database()"))
        if not database_name or actual != database_name:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="La clinica no coincide con la base del comprobante",
            )
    return nombre


def _generar_comprobante_validado(
    pago,
    paciente,
    clinica_nombre: str,
) -> tuple[bytes, str]:
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
        clinica_nombre=clinica_nombre,
        pago_id=pago.id,
        consulta_clinica_id=consulta_id,
        consulta_fecha=consulta.fecha_consulta if consulta is not None else None,
        fecha_pago=pago.fecha_hora_pago,
        # La emision forma parte del contenido idempotente del comprobante. Se
        # deriva del pago persistido para que una descarga o reintento posterior
        # no cambie el PDF.
        fecha_emision=pago.fecha_hora_pago or pago.fecha_creacion,
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
