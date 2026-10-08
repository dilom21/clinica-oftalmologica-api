import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()

DEFAULT_APP_TIMEZONE = "America/La_Paz"


def _resolver_app_timezone(valor: str | None) -> str:
    """Valida la zona configurada y evita una caída global por un valor inválido."""
    zona = (valor or DEFAULT_APP_TIMEZONE).strip()
    try:
        ZoneInfo(zona)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_APP_TIMEZONE
    return zona


APP_TIMEZONE = _resolver_app_timezone(os.getenv("APP_TIMEZONE"))

DATABASE_URL = os.getenv("DATABASE_URL")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("PASSWORD_RESET_TOKEN_EXPIRE_MINUTES", "30")
)
PASSWORD_RESET_URL_BASE = os.getenv("PASSWORD_RESET_URL_BASE")
GMAIL_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
GMAIL_REFRESH_TOKEN = os.getenv("GMAIL_REFRESH_TOKEN")
GMAIL_SENDER_EMAIL = os.getenv("GMAIL_SENDER_EMAIL")

# SMTP is optional: password recovery continues to use the Gmail API above.
SMTP_HOST = os.getenv("SMTP_HOST") or None
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
except ValueError:
    SMTP_PORT = 587
SMTP_USERNAME = os.getenv("SMTP_USERNAME") or None
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or None
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or None
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME") or "Clinica Oftalmologica"
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada en el archivo .env")

if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY no está configurada en el archivo .env")

if JWT_ALGORITHM != "HS256":
    raise RuntimeError("JWT_ALGORITHM debe ser HS256")

if PASSWORD_RESET_TOKEN_EXPIRE_MINUTES <= 0:
    raise RuntimeError(
        "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES debe ser mayor a 0"
    )

if not PASSWORD_RESET_URL_BASE:
    raise RuntimeError(
        "PASSWORD_RESET_URL_BASE no está configurada en el archivo .env"
    )

if not GMAIL_CLIENT_ID:
    raise RuntimeError(
        "GMAIL_CLIENT_ID no está configurada en el archivo .env"
    )

if not GMAIL_CLIENT_SECRET:
    raise RuntimeError(
        "GMAIL_CLIENT_SECRET no está configurada en el archivo .env"
    )

if not GMAIL_REFRESH_TOKEN:
    raise RuntimeError(
        "GMAIL_REFRESH_TOKEN no está configurada en el archivo .env"
    )

if not GMAIL_SENDER_EMAIL:
    raise RuntimeError(
        "GMAIL_SENDER_EMAIL no está configurada en el archivo .env"
    )


# =========================================================
# INTEGRACIÓN IA - DEEPSEEK
# DEEPSEEK_API_KEY es opcional al importar: la aplicación debe
# iniciar sin IA configurada y responder 503 solo al usar los
# endpoints de IA. Nunca se registra la clave en logs.
# =========================================================

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or None
DEEPSEEK_BASE_URL = (
    os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
)
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL") or "deepseek-flash"

try:
    DEEPSEEK_TIMEOUT_SECONDS = int(
        os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")
    )
except ValueError:
    DEEPSEEK_TIMEOUT_SECONDS = 30

if DEEPSEEK_TIMEOUT_SECONDS <= 0:
    DEEPSEEK_TIMEOUT_SECONDS = 30
