import base64
from dataclasses import dataclass

import httpx

from app.core import config


class ErrorResend(RuntimeError):
    pass


class ResendNoConfigurado(ErrorResend):
    pass


@dataclass(frozen=True, slots=True)
class RespuestaResend:
    id: str


class ResendProvider:
    endpoint = "https://api.resend.com/emails"

    def __init__(self, client=None):
        self._client = client

    def validar_configuracion(self):
        if not config.RESEND_API_KEY or not config.RESEND_FROM_EMAIL:
            raise ResendNoConfigurado("Resend no esta configurado")
        nombre = (config.RESEND_FROM_NAME or "Clinica Oftalmologica").strip()
        return config.RESEND_API_KEY, f"{nombre} <{config.RESEND_FROM_EMAIL}>"

    def enviar_comprobante(
        self,
        *,
        destinatario: str,
        pago_id: int,
        pdf: bytes,
        nombre_archivo: str,
        clave_idempotencia: str,
        tags: dict[str, str],
    ) -> RespuestaResend:
        api_key, remitente = self.validar_configuracion()
        cliente = self._client or httpx.Client(timeout=config.RESEND_TIMEOUT_SECONDS)
        cerrar = self._client is None
        try:
            respuesta = cliente.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": clave_idempotencia,
                },
                json={
                    "from": remitente,
                    "to": [destinatario],
                    "subject": f"Comprobante de pago CP-{pago_id:06d}",
                    "html": (
                        "<p>Tu pago fue aprobado.</p>"
                        "<p>Adjuntamos el comprobante correspondiente.</p>"
                    ),
                    "attachments": [{
                        "filename": nombre_archivo,
                        "content": base64.b64encode(pdf).decode("ascii"),
                    }],
                    "tags": [
                        {"name": clave, "value": valor}
                        for clave, valor in tags.items()
                    ],
                },
            )
            respuesta.raise_for_status()
            data = respuesta.json()
            mensaje_id = data.get("id")
            if not isinstance(mensaje_id, str) or not mensaje_id:
                raise ErrorResend("Resend devolvio una respuesta invalida")
            return RespuestaResend(id=mensaje_id)
        except ResendNoConfigurado:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ErrorResend("Resend no pudo aceptar el comprobante") from exc
        finally:
            if cerrar:
                cliente.close()


def obtener_proveedor_resend() -> ResendProvider:
    return ResendProvider()
