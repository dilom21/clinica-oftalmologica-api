from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import resend

from app.core.config import RESEND_API_KEY, RESEND_FROM_EMAIL

resend.api_key = RESEND_API_KEY


def construir_enlace_recuperacion_password(
    base_url: str,
    token: str,
) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = token
    return urlunparse(parsed._replace(query=urlencode(query)))


def construir_html_recuperacion_password(
    enlace: str,
    minutos_expira: int,
) -> str:
    return f"""
    <html>
      <body style=\"font-family: Arial, sans-serif; color: #1f2937;\">
        <h2>Recuperacion de contrasena</h2>
        <p>Recibimos una solicitud para recuperar tu contrasena.</p>
        <p>
          <a
            href=\"{enlace}\"
            style=\"display:inline-block;padding:10px 18px;background:#0f766e;color:#ffffff;text-decoration:none;border-radius:6px;\"
          >
            Restablecer contrasena
          </a>
        </p>
        <p>Este enlace expira en {minutos_expira} minutos.</p>
        <p>Si no solicitaste este cambio, ignora este mensaje.</p>
      </body>
    </html>
    """.strip()


def enviar_correo_recuperacion_password(
    destinatario: str,
    enlace: str,
    minutos_expira: int,
):
    html = construir_html_recuperacion_password(enlace, minutos_expira)

    params = {
        "from": RESEND_FROM_EMAIL,
        "to": [destinatario],
        "subject": "Recuperacion de contrasena",
        "html": html,
    }

    return resend.Emails.send(params)
