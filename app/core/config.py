import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

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
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "bob").strip().lower()

# =========================================================
# ZONA HORARIA DE LA APLICACIÓN
# `ZoneInfo` se expone aquí para app/core/time.py. Si la zona configurada
# es inválida se aplica un fallback seguro (America/La_Paz) en lugar de
# romper el arranque.
# =========================================================
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/La_Paz")
try:
    ZoneInfo(APP_TIMEZONE)
except Exception:
    APP_TIMEZONE = "America/La_Paz"

# =========================================================
# INTEGRACIÓN IA (DeepSeek) - OPCIONAL
# Sin DEEPSEEK_API_KEY la aplicación inicia normalmente; solo los endpoints
# que realmente necesitan IA responden 503 de forma controlada.
# =========================================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
try:
    DEEPSEEK_TIMEOUT_SECONDS = float(
        os.getenv("DEEPSEEK_TIMEOUT_SECONDS") or "30"
    )
except ValueError:
    DEEPSEEK_TIMEOUT_SECONDS = 30.0

# =========================================================
# SMTP PARA ENVÍO DE REPORTES - OPCIONAL
# Sin SMTP configurado la aplicación inicia normalmente; el envío de
# reportes por correo devuelve un error controlado.
# =========================================================
SMTP_HOST = os.getenv("SMTP_HOST")
try:
    SMTP_PORT = int(os.getenv("SMTP_PORT") or "587")
except ValueError:
    SMTP_PORT = 587
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Clínica Oftalmológica")
SMTP_USE_TLS = (os.getenv("SMTP_USE_TLS") or "true").strip().lower() in {
    "1", "true", "yes", "on",
}

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
