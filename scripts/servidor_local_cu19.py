"""Factory de Uvicorn para probar CU19 localmente sin cambiar .env.

Supabase en modo sesión puede agotar sus conexiones durante el desarrollo.
Este arranque usa el puerto de transacciones y devuelve cada conexión al
terminar la petición. La aplicación de producción sigue usando app.main.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool


_RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
_GMAIL_LOCAL = {
    "GMAIL_CLIENT_ID": "prueba-local",
    "GMAIL_CLIENT_SECRET": "prueba-local",
    "GMAIL_REFRESH_TOKEN": "prueba-local",
    "GMAIL_SENDER_EMAIL": "local@example.test",
}


def crear_app():
    load_dotenv(_RAIZ_PROYECTO / ".env", override=False)
    # Estas credenciales solo permiten arrancar; no envían correos.
    for nombre, valor in _GMAIL_LOCAL.items():
        if not os.environ.get(nombre, "").strip():
            os.environ[nombre] = valor

    from app.database import connection, session

    url = connection.engine.url
    if (
        url.drivername == "postgresql+psycopg"
        and (url.host or "").lower().endswith(".pooler.supabase.com")
        and (url.port or 5432) == 5432
    ):
        engine_local = create_engine(
            url.set(port=6543),
            poolclass=NullPool,
            pool_pre_ping=True,
            connect_args={"prepare_threshold": None},
        )
        connection.engine.dispose()
        connection.engine = engine_local
        session.SessionLocal.configure(bind=engine_local)

    from app.main import app

    return app
