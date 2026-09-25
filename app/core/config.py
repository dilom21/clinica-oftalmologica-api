import os
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