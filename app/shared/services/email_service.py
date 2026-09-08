import base64
import html
import os
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import (
    GMAIL_CLIENT_ID,
    GMAIL_CLIENT_SECRET,
    GMAIL_REFRESH_TOKEN,
    GMAIL_SENDER_EMAIL,
)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
EMAIL_LOGO_URL = os.getenv("EMAIL_LOGO_URL", "")
EMAIL_LOGO_PATH = os.getenv(
    "EMAIL_LOGO_PATH",
    str(
        Path(__file__).resolve().parents[4]
        / "clinica-oftalmologica-web"
        / "public"
        / "images"
        / "logo"
        / "logo-clinica.png"
    ),
)


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
    logo_src: str = "",
) -> str:
    enlace_seguro = html.escape(enlace, quote=True)
    logo = ""
    if logo_src or EMAIL_LOGO_URL:
        logo_src_seguro = html.escape(logo_src or EMAIL_LOGO_URL, quote=True)
        logo = f'<img src="{logo_src_seguro}" alt="Clinica Oftalmologica" style="display:block;max-width:220px;height:auto;margin:0 auto 24px;">'
    else:
        logo = '<div style="font-size:20px;font-weight:700;color:#0f766e;text-align:center;margin-bottom:24px;">Clinica Oftalmologica</div>'

    return f"""
    <html>
        <body style=\"margin:0;background:#f3f4f6;font-family:Arial,sans-serif;color:#1f2937;\">
            <div style=\"max-width:560px;margin:32px auto;padding:32px;background:#ffffff;border-radius:8px;\">
                {logo}
                <h2 style=\"margin:0 0 16px;text-align:center;\">Recuperacion de contrasena</h2>
                <p>Recibimos una solicitud para cambiar tu contrasena.</p>
        <p>
            <a href=\"{enlace_seguro}\"
            style=\"display:inline-block;padding:10px 18px;background:#0f766e;color:#ffffff;text-decoration:none;border-radius:6px;\"
            >
            Restablecer contrasena
            </a>
        </p>
                <p>Este enlace expira en {minutos_expira} minutos.</p>
                <p>Si no solicitaste este cambio, ignora este mensaje.</p>
            </div>
        </body>
    </html>
    """.strip()


def _get_gmail_service():
    creds = Credentials(
        token=None,
        refresh_token=GMAIL_REFRESH_TOKEN,
        client_id=GMAIL_CLIENT_ID,
        client_secret=GMAIL_CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds)


def enviar_correo_recuperacion_password(
    destinatario: str,
    enlace: str,
    minutos_expira: int,
):
    logo_path = Path(EMAIL_LOGO_PATH)
    logo_cid = "clinica-logo"
    logo_src = f"cid:{logo_cid}" if logo_path.is_file() else EMAIL_LOGO_URL
    html_body = construir_html_recuperacion_password(
        enlace,
        minutos_expira,
        logo_src,
    )

    message = MIMEMultipart("related")
    message.attach(MIMEText(html_body, "html", "utf-8"))
    if logo_path.is_file():
        with logo_path.open("rb") as logo_file:
            logo_image = MIMEImage(logo_file.read(), _subtype="png")
        logo_image.add_header("Content-ID", f"<{logo_cid}>")
        logo_image.add_header("Content-Disposition", "inline", filename=logo_path.name)
        message.attach(logo_image)
    message["to"] = destinatario
    message["from"] = GMAIL_SENDER_EMAIL
    message["subject"] = "Recuperacion de contrasena"

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service = _get_gmail_service()
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()