import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from email_validator import EmailNotValidError, validate_email
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core import config
from app.modules.gestion_pagos.providers.resend_provider import (
    ErrorResend,
    ResendNoConfigurado,
    ResendProvider,
    ResultadoResendDesconocido,
)
from app.modules.gestion_pagos.repositories import administrativos as repo
from app.modules.gestion_pagos.schemas.administrativos import ResultadoWebhookResend
from app.modules.gestion_pagos.services import service as pago_service


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DestinatarioComprobante:
    correo: str | None


def _ahora():
    return datetime.now(timezone.utc)


def _resolver_destinatario(db: Session, pago_id: int) -> DestinatarioComprobante:
    filas = repo.obtener_destinatario_pago(db, pago_id)
    pacientes = {fila.id for fila in filas}
    if len(pacientes) != 1 or len(filas) != 1:
        return DestinatarioComprobante(None)
    fila = filas[0]

    correo_pruebas = config.obtener_configuracion_resend_pruebas()
    if correo_pruebas is not None:
        contexto = db.info.get("tenant_context")
        logger.warning(
            "CU31: comprobante redirigido a EMAIL_TEST; "
            "no representa entrega al paciente",
            extra={
                "evento": "CU31_EMAIL_TEST_REDIRECT",
                "redireccion_email_test": True,
                "pago_id": pago_id,
                "paciente_id": fila.id,
                "empresa_codigo": (
                    contexto.empresa_codigo if contexto is not None else "legacy"
                ),
            },
        )
        return DestinatarioComprobante(correo_pruebas)

    if not fila.estado or not fila.correo:
        return DestinatarioComprobante(None)
    try:
        correo = validate_email(
            fila.correo, check_deliverability=False,
        ).normalized
        return DestinatarioComprobante(correo)
    except EmailNotValidError:
        return DestinatarioComprobante(None)


def _destinatario_valido(db: Session, pago_id: int) -> str | None:
    """Compatibilidad interna para consumidores que solo necesitan el correo."""
    return _resolver_destinatario(db, pago_id).correo


def _prefijo_tenant(db: Session) -> str:
    contexto = db.info.get("tenant_context")
    return f"tenant-{contexto.tenant_database_id}" if contexto else "legacy"


def programar_comprobante(db: Session, pago_id: int):
    existente = repo.obtener_notificacion_por_pago(db, pago_id, bloquear=True)
    if existente is not None:
        return existente
    destinatario = _resolver_destinatario(db, pago_id)
    return repo.crear_notificacion(
        db,
        pago_id=pago_id,
        destinatario=destinatario.correo,
        clave=f"comprobante-pago/{_prefijo_tenant(db)}/{pago_id}",
        estado="PENDIENTE" if destinatario.correo else "FALLIDO",
        error=None if destinatario.correo else "DESTINATARIO_NO_DISPONIBLE",
    )


def _tags(db: Session, pago_id: int) -> dict[str, str]:
    pago = repo.obtener_pago_administrativo(db, pago_id)
    consultas = {
        detalle.servicio_realizado.consulta_clinica_id
        for detalle in pago.detalles
        if detalle.servicio_realizado is not None
    }
    contexto = db.info.get("tenant_context")
    tags = {
        "tipo": "comprobante_pago",
        "pago_id": str(pago_id),
        "consulta_clinica_id": str(next(iter(consultas))),
    }
    if contexto is not None:
        tags.update({
            "clinica_contexto": "tenant",
            "empresa_id": str(contexto.empresa_id),
            "empresa_codigo": contexto.empresa_codigo,
            "tenant_id": str(contexto.tenant_database_id),
        })
    return tags


def procesar_siguiente(db: Session, proveedor: ResendProvider) -> bool:
    ahora = _ahora()
    try:
        item = repo.reclamar_notificacion(
            db,
            ahora,
            ahora + timedelta(minutes=10),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    if item is None:
        return False

    correo_pruebas = config.obtener_configuracion_resend_pruebas()
    redirigido_pruebas = bool(
        correo_pruebas is not None and item.destinatario == correo_pruebas
    )
    # El destinatario queda congelado al crear la entrada outbox. Cambiarlo en
    # el worker conservaria la clave idempotente pero alteraria el payload.
    destinatario_efectivo = item.destinatario
    if redirigido_pruebas:
        logger.warning(
            "CU31: worker enviara un comprobante al destinatario de pruebas; "
            "no representa entrega al paciente",
            extra={
                "evento": "CU31_EMAIL_TEST_REDIRECT",
                "redireccion_email_test": True,
                "pago_id": item.pago_id,
                "notificacion_id": item.id,
            },
        )

    try:
        contenido, nombre = pago_service.generar_comprobante_interno(db, item.pago_id)
        respuesta = proveedor.enviar_comprobante(
            destinatario=destinatario_efectivo,
            pago_id=item.pago_id,
            pdf=contenido,
            nombre_archivo=nombre,
            clave_idempotencia=item.clave_idempotencia,
            tags=_tags(db, item.pago_id),
        )
        actual = db.get(type(item), item.id)
        actual.estado = "ACEPTADO_POR_PROVEEDOR"
        actual.proveedor_mensaje_id = respuesta.id
        actual.fecha_aceptado = _ahora()
        actual.fecha_actualizacion = actual.fecha_aceptado
        actual.bloqueado_hasta = None
        actual.proximo_intento = None
        actual.ultimo_error = None
        db.commit()
        return True
    except ResultadoResendDesconocido:
        db.rollback()
        actual = db.get(type(item), item.id)
        actual.estado = "FALLIDO"
        actual.bloqueado_hasta = None
        actual.fecha_actualizacion = _ahora()
        actual.proximo_intento = actual.fecha_actualizacion + timedelta(
            minutes=min(2 ** max(actual.intentos - 1, 0), 60),
        )
        actual.ultimo_error = "RESULTADO_ENVIO_DESCONOCIDO"
        db.commit()
        return True
    except (ErrorResend, HTTPException) as exc:
        db.rollback()
        actual = db.get(type(item), item.id)
        actual.estado = "FALLIDO"
        actual.bloqueado_hasta = None
        actual.fecha_actualizacion = _ahora()
        actual.proximo_intento = actual.fecha_actualizacion + timedelta(
            minutes=min(2 ** max(actual.intentos - 1, 0), 60),
        )
        actual.ultimo_error = (
            "RESEND_NO_CONFIGURADO"
            if isinstance(exc, ResendNoConfigurado)
            else "ENVIO_NO_ACEPTADO"
        )
        db.commit()
        return True


def solicitar_reintento(db: Session, pago_id: int):
    item = repo.obtener_notificacion_por_pago(db, pago_id, bloquear=True)
    if item is None:
        raise HTTPException(404, "Notificacion de comprobante no encontrada")
    if item.estado in {"ACEPTADO_POR_PROVEEDOR", "ENTREGADO"}:
        return item
    if item.estado == "PROCESANDO":
        raise HTTPException(
            409,
            "La notificacion esta siendo procesada; espere al worker",
        )
    if item.ultimo_error == "RESULTADO_ENVIO_DESCONOCIDO":
        raise HTTPException(
            409,
            "No se puede reintentar manualmente: la aceptacion del proveedor es incierta",
        )
    if item.estado == "PENDIENTE":
        return item
    if item.ultimo_error in {"DESTINATARIO_NO_DISPONIBLE", "RESEND_NO_CONFIGURADO"}:
        destinatario = _resolver_destinatario(db, pago_id)
        if destinatario.correo is None:
            raise HTTPException(409, "El paciente no tiene un correo valido asociado")
        item.destinatario = destinatario.correo
    elif item.destinatario is None:
        raise HTTPException(409, "El paciente no tiene un correo valido asociado")
    item.estado = "PENDIENTE"
    item.proximo_intento = None
    item.bloqueado_hasta = None
    item.ultimo_error = None
    item.fecha_actualizacion = _ahora()
    db.flush()
    return item


def verificar_webhook(payload: bytes, svix_id: str, timestamp: str, firma: str) -> dict:
    secreto = config.RESEND_WEBHOOK_SECRET
    if not secreto:
        raise HTTPException(500, "El webhook de Resend no esta configurado")
    try:
        marca = int(timestamp)
        if abs(int(time.time()) - marca) > 300:
            raise ValueError
        clave = secreto.removeprefix("whsec_")
        key = base64.b64decode(clave)
        firmado = f"{svix_id}.{timestamp}.".encode() + payload
        esperado = base64.b64encode(hmac.new(key, firmado, hashlib.sha256).digest()).decode()
        candidatos = [parte.split(",", 1)[-1] for parte in firma.split() if parte.startswith("v1,")]
        if not any(hmac.compare_digest(esperado, candidato) for candidato in candidatos):
            raise ValueError
        evento = json.loads(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(400, "Firma webhook Resend invalida") from None
    return evento


def procesar_evento_webhook(db: Session, evento: dict) -> ResultadoWebhookResend:
    tipo = evento.get("type")
    if tipo not in {"email.delivered", "email.failed", "email.bounced"}:
        return ResultadoWebhookResend(procesado=False)
    proveedor_id = (evento.get("data") or {}).get("email_id")
    if not proveedor_id:
        raise HTTPException(400, "Evento Resend sin email_id")
    try:
        item = repo.obtener_notificacion_por_proveedor_id(db, proveedor_id, bloquear=True)
        if item is None:
            raise HTTPException(404, "Notificacion Resend no encontrada")
        if tipo == "email.delivered":
            item.estado = "ENTREGADO"
            item.fecha_entregado = _ahora()
            item.ultimo_error = None
        elif item.estado != "ENTREGADO":
            item.estado = "FALLIDO"
            item.ultimo_error = "ENTREGA_FALLIDA"
        item.fecha_actualizacion = _ahora()
        db.commit()
        return ResultadoWebhookResend(
            procesado=True,
            notificacion_id=item.id,
            estado=item.estado,
        )
    except Exception:
        db.rollback()
        raise


def metadata_enrutamiento(evento: dict) -> dict[str, str]:
    tags = (evento.get("data") or {}).get("tags") or {}
    if isinstance(tags, list):
        try:
            tags = {item["name"]: item["value"] for item in tags}
        except (KeyError, TypeError):
            raise HTTPException(409, "Metadata Resend inconsistente") from None
    if not isinstance(tags, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in tags.items()
    ):
        raise HTTPException(409, "Metadata Resend inconsistente")
    return tags
