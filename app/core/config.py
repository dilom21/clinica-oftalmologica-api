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
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL")

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

if not RESEND_API_KEY:
    raise RuntimeError(
        "RESEND_API_KEY no está configurada en el archivo .env"
    )

if not RESEND_FROM_EMAIL:
    raise RuntimeError(
        "RESEND_FROM_EMAIL no está configurada en el archivo .env"
    )
