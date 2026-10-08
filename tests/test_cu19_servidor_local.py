"""Arranque local CU19: configuración aislada, sin conexión de red."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.database import connection, session
from app.main import app
from scripts import servidor_local_cu19


@pytest.fixture
def arranque_local(monkeypatch):
    engine_original = MagicMock()
    engine_original.url = URL.create(
        "postgresql+psycopg",
        username="postgres.proyecto-local",
        password="clave-de-prueba@:/%",
        host="aws-0-sa-east-1.pooler.supabase.com",
        port=5432,
        database="postgres",
        query={"sslmode": "require", "application_name": "cu19-local"},
    )
    crear_engine = MagicMock()
    cargar_env = MagicMock()
    configurar_sesion = MagicMock()
    monkeypatch.setattr(connection, "engine", engine_original)
    monkeypatch.setattr(servidor_local_cu19, "create_engine", crear_engine)
    monkeypatch.setattr(servidor_local_cu19, "load_dotenv", cargar_env)
    monkeypatch.setattr(session.SessionLocal, "configure", configurar_sesion)
    return engine_original, crear_engine, cargar_env, configurar_sesion


@pytest.mark.parametrize("puerto", [5432, None])
def test_supabase_local_conserva_base_y_credenciales_y_libera_conexiones(arranque_local, puerto):
    original, crear_engine, cargar_env, configurar_sesion = arranque_local
    original.url = URL.create(
        original.url.drivername,
        username=original.url.username,
        password=original.url.password,
        host=original.url.host,
        port=puerto,
        database=original.url.database,
        query=original.url.query,
    )
    url_original = original.url

    assert servidor_local_cu19.crear_app() is app

    url_local = crear_engine.call_args.args[0]
    assert url_local.port == 6543
    for atributo in ("drivername", "username", "password", "host", "database", "query"):
        assert getattr(url_local, atributo) == getattr(url_original, atributo)
    assert original.url == url_original
    assert crear_engine.call_args.kwargs == {
        "poolclass": NullPool,
        "pool_pre_ping": True,
        "connect_args": {"prepare_threshold": None},
    }
    original.dispose.assert_called_once_with()
    original.connect.assert_not_called()
    crear_engine.return_value.connect.assert_not_called()
    assert connection.engine is crear_engine.return_value
    configurar_sesion.assert_called_once_with(bind=crear_engine.return_value)
    cargar_env.assert_called_once_with(servidor_local_cu19._RAIZ_PROYECTO / ".env", override=False)


@pytest.mark.parametrize("url", [
    "sqlite+pysqlite:///:memory:",
    "postgresql+psycopg://prueba:clave@localhost:5432/clinica",
    "postgresql+psycopg://prueba:clave@db.proyecto.supabase.co:5432/postgres",
    "postgresql+psycopg://prueba:clave@aws-0.pooler.supabase.com:6543/postgres",
    "postgresql+psycopg://prueba:clave@aws-0.pooler.supabase.com:5433/postgres",
    "postgresql+psycopg2://prueba:clave@aws-0.pooler.supabase.com:5432/postgres",
    "postgresql+psycopg://prueba:clave@falsopooler.supabase.com:5432/postgres",
])
def test_otras_conexiones_conservan_su_configuracion(arranque_local, url):
    from sqlalchemy.engine import make_url

    original, crear_engine, _, configurar_sesion = arranque_local
    original.url = make_url(url)

    assert servidor_local_cu19.crear_app() is app

    assert connection.engine is original
    crear_engine.assert_not_called()
    original.dispose.assert_not_called()
    configurar_sesion.assert_not_called()


def test_arranque_local_preserva_gmail_configurado_y_database_url(arranque_local, monkeypatch):
    valores = {
        "GMAIL_CLIENT_ID": "cliente-configurado",
        "GMAIL_CLIENT_SECRET": "secreto-configurado",
        "GMAIL_REFRESH_TOKEN": "token-configurado",
        "GMAIL_SENDER_EMAIL": "configurado@example.test",
        "DATABASE_URL": "postgresql+psycopg://prueba:clave@localhost:5432/clinica",
    }
    for nombre, valor in valores.items():
        monkeypatch.setenv(nombre, valor)

    servidor_local_cu19.crear_app()

    for nombre, valor in valores.items():
        assert servidor_local_cu19.os.environ[nombre] == valor


def test_arranque_local_completa_solo_gmail_ausente_o_vacio(arranque_local, monkeypatch):
    monkeypatch.delenv("GMAIL_CLIENT_ID", raising=False)
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "")
    monkeypatch.setenv("GMAIL_REFRESH_TOKEN", "   ")
    monkeypatch.setenv("GMAIL_SENDER_EMAIL", "configurado@example.test")

    servidor_local_cu19.crear_app()

    assert servidor_local_cu19.os.environ["GMAIL_CLIENT_ID"] == "prueba-local"
    assert servidor_local_cu19.os.environ["GMAIL_CLIENT_SECRET"] == "prueba-local"
    assert servidor_local_cu19.os.environ["GMAIL_REFRESH_TOKEN"] == "prueba-local"
    assert servidor_local_cu19.os.environ["GMAIL_SENDER_EMAIL"] == "configurado@example.test"
