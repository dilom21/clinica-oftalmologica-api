import importlib
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core import dependencies
from app.core import config
from app.core import time as app_time
from app.core.security import crear_access_token
from app.database import session
from app.main import app
from app.modules.gestion_reportes.registry.datasets import DATASETS
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora
from app.modules.integracion_ia.providers.deepseek_provider import (
    IAProveedorNoDisponibleError,
)


@pytest.fixture(autouse=True)
def restaurar_configuracion_timezone():
    yield
    importlib.reload(config)


def _respuesta(**overrides):
    respuesta = {
        "dataset": "consultas_clinicas",
        "columnas": ["fecha_consulta", "paciente"],
        "filtros": [],
        "orden": [],
        "limit": 50,
        "requiere_aclaracion": False,
        "pregunta_aclaracion": None,
        "accion_sugerida": "previsualizar",
        "formato_sugerido": None,
    }
    respuesta.update(overrides)
    return respuesta


@pytest.fixture
def voz_client(db_historial):
    db_historial.execute(text("INSERT INTO funcion VALUES (30, 'Generar reportes', 1)"))
    db_historial.execute(text("INSERT INTO rol_funcion VALUES (4, 1, 30, 1)"))
    db_historial.commit()

    def local_db():
        yield db_historial

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = local_db
    app.dependency_overrides[dependencies.get_db] = local_db
    try:
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def test_interpreta_comando_columnas_fecha_y_orden_desc(voz_client, monkeypatch):
    from app.modules.integracion_ia.services import service

    esperado = _respuesta(
        filtros=[{
            "campo": "fecha_consulta",
            "operador": "between",
            "valor": ["2026-10-01", "2026-10-31"],
        }],
        orden=[{"campo": "fecha_consulta", "direccion": "desc"}],
    )
    capturado = {}
    monkeypatch.setattr(
        service.deepseek_provider, "generar_json",
        lambda system, user: capturado.update(system=system, user=user) or esperado,
    )

    response = voz_client.post(
        "/ia/reportes/interpretar",
        json={"texto": "Muéstrame las consultas de octubre más recientes"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == esperado
    assert "No generes SQL" in capturado["system"]
    assert "2026-" in capturado["user"]
    assert "CLINICAL_SECRET" not in capturado["user"]


def test_app_timezone_default(monkeypatch):
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    importlib.reload(config)
    assert config.APP_TIMEZONE == "America/La_Paz"


def test_app_timezone_es_configurable(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    importlib.reload(config)
    assert config.APP_TIMEZONE == "UTC"

    class RelojCongelado(datetime):
        @classmethod
        def now(cls, tz=None):
            instante_utc = cls(2026, 10, 5, 2, 0, tzinfo=timezone.utc)
            return instante_utc.astimezone(tz) if tz else instante_utc.replace(tzinfo=None)

    monkeypatch.setattr(app_time, "datetime", RelojCongelado)
    assert app_time.fecha_local_aplicacion().isoformat() == "2026-10-05"

    monkeypatch.setenv("APP_TIMEZONE", "America/La_Paz")
    importlib.reload(config)
    assert app_time.fecha_local_aplicacion().isoformat() == "2026-10-04"


def test_app_timezone_invalida_hace_fallback_seguro(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "Zona/Invalida")
    importlib.reload(config)
    assert config.APP_TIMEZONE == "America/La_Paz"


def test_prompt_usa_fecha_local_en_frontera_utc(voz_client, monkeypatch):
    capturado = {}

    class RelojCongelado(datetime):
        @classmethod
        def now(cls, tz=None):
            instante_utc = cls(2026, 10, 5, 2, 0, tzinfo=timezone.utc)
            return instante_utc.astimezone(tz) if tz else instante_utc.replace(tzinfo=None)

    monkeypatch.setattr(app_time, "datetime", RelojCongelado)
    from app.modules.integracion_ia.services import service

    monkeypatch.setattr(
        service.deepseek_provider,
        "generar_json",
        lambda system, user: capturado.update(user=user) or _respuesta(),
    )

    response = voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "consultas de hoy"}
    )

    assert response.status_code == 200, response.text
    assert "Fecha actual: 2026-10-04" in capturado["user"]
    assert "Fecha actual: 2026-10-05" not in capturado["user"]


def test_exportar_pdf_solo_se_sugiere(voz_client, monkeypatch):
    from app.modules.integracion_ia.services import service

    monkeypatch.setattr(
        service.deepseek_provider, "generar_json",
        lambda *args: _respuesta(accion_sugerida="exportar", formato_sugerido="pdf"),
    )
    response = voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "Exporta el reporte en PDF"}
    )
    assert response.status_code == 200
    assert response.json()["formato_sugerido"] == "pdf"


def test_pacientes_activos_normaliza_listar_tabla_con_keys_reales(voz_client, monkeypatch):
    from app.modules.integracion_ia.services import service

    columnas = list(DATASETS["pacientes"]["fields"])
    respuesta = _respuesta(
        dataset="pacientes",
        columnas=columnas,
        filtros=[{"campo": "estado", "operador": "eq", "valor": True}],
        limit=200,
        accion_sugerida="listar",
        formato_sugerido="tabla",
    )
    monkeypatch.setattr(service.deepseek_provider, "generar_json", lambda *args: respuesta)

    response = voz_client.post(
        "/ia/reportes/interpretar",
        json={"texto": "Muéstrame los pacientes activos."},
    )

    assert response.status_code == 200, response.text
    contenido = response.json()
    assert contenido["dataset"] == "pacientes"
    assert contenido["columnas"] == columnas
    assert contenido["filtros"] == [{"campo": "estado", "operador": "eq", "valor": True}]
    assert contenido["orden"] == []
    assert contenido["limit"] == 200
    assert contenido["requiere_aclaracion"] is False
    assert contenido["accion_sugerida"] == "previsualizar"
    assert contenido["formato_sugerido"] is None


@pytest.mark.parametrize("variantes", [
    {"accion_sugerida": "listar"},
    {"formato_sugerido": "tabla"},
    {"accion_sugerida": "listar", "formato_sugerido": "tabla"},
])
def test_variaciones_observadas_se_normalizan(voz_client, monkeypatch, variantes):
    from app.modules.integracion_ia.services import service

    monkeypatch.setattr(service.deepseek_provider, "generar_json", lambda *args: _respuesta(**variantes))
    response = voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "configuración"}
    )
    assert response.status_code == 200


def test_propiedad_adicional_sigue_produciendo_502(voz_client, monkeypatch):
    from app.modules.integracion_ia.services import service

    monkeypatch.setattr(
        service.deepseek_provider,
        "generar_json",
        lambda *args: _respuesta(propiedad_inventada="no permitida"),
    )
    response = voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "pacientes activos"}
    )
    assert response.status_code == 502


@pytest.mark.parametrize("respuesta", [
    _respuesta(dataset="dataset_inventado"),
    _respuesta(columnas=["campo_inventado"]),
    _respuesta(filtros=[{"campo": "fecha_consulta", "operador": "raw_sql", "valor": "x"}]),
    _respuesta(orden=[{"campo": "fecha_consulta", "direccion": "asc"}] * 4),
    _respuesta(limit=201),
])
def test_ia_no_acepta_configuracion_fuera_del_registry(voz_client, monkeypatch, respuesta):
    from app.modules.integracion_ia.services import service

    monkeypatch.setattr(service.deepseek_provider, "generar_json", lambda *args: respuesta)
    assert voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "configuración"}
    ).status_code == 502


def test_json_invalido_y_provider_caido(voz_client, monkeypatch):
    from app.modules.integracion_ia.services import service

    monkeypatch.setattr(service.deepseek_provider, "generar_json", lambda *args: "no json")
    assert voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "consulta"}
    ).status_code == 502

    def caido(*args):
        raise IAProveedorNoDisponibleError()

    monkeypatch.setattr(service.deepseek_provider, "generar_json", caido)
    assert voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "consulta"}
    ).status_code == 503


@pytest.mark.parametrize("body", [{"texto": "   "}, {"texto": "ok", "extra": True}])
def test_request_estricto(voz_client, body):
    assert voz_client.post("/ia/reportes/interpretar", json=body).status_code == 422


def test_jwt_rol_y_permiso(voz_client, db_historial):
    voz_client.headers.pop("Authorization")
    assert voz_client.post("/ia/reportes/interpretar", json={"texto": "consulta"}).status_code == 401

    voz_client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
    db_historial.execute(text("UPDATE rol SET nombre='Recepción' WHERE id=1"))
    db_historial.commit()
    assert voz_client.post("/ia/reportes/interpretar", json={"texto": "consulta"}).status_code == 403

    db_historial.execute(text("UPDATE rol SET nombre='Administrador' WHERE id=1"))
    db_historial.execute(text("DELETE FROM rol_funcion WHERE id=4"))
    db_historial.commit()
    assert voz_client.post("/ia/reportes/interpretar", json={"texto": "consulta"}).status_code == 403


def test_bitacora_es_generica_y_no_contiene_texto_dictado(voz_client, db_historial, monkeypatch):
    from app.modules.integracion_ia.services import service

    texto = "CLINICAL_SECRET nombre paciente CI-999 teléfono 70000000"
    monkeypatch.setattr(service.deepseek_provider, "generar_json", lambda *args: _respuesta())
    response = voz_client.post("/ia/reportes/interpretar", json={"texto": texto})
    assert response.status_code == 200
    registro = db_historial.query(Bitacora).filter_by(accion="INTERPRETAR_REPORTE_IA").one()
    assert registro.entidad_afectada == "reporte"
    assert registro.descripcion == "Interpretación de consulta de reporte mediante IA"
    assert texto not in (registro.descripcion or "")


def test_prompt_usa_registry_real_sin_filas_bd(voz_client, monkeypatch):
    from app.modules.integracion_ia.services import service

    capturado = {}
    monkeypatch.setattr(
        service.deepseek_provider, "generar_json",
        lambda system, user: capturado.update(user=user) or _respuesta(),
    )
    assert voz_client.post(
        "/ia/reportes/interpretar", json={"texto": "pacientes"}
    ).status_code == 200
    catalogo = json.loads(capturado["user"].split("Catálogo permitido: ", 1)[1].split("\nTexto", 1)[0])
    assert {item["key"] for item in catalogo} == set(DATASETS)
    assert all("filas" not in item and "model" not in item for item in catalogo)
    assert "CI-001" not in capturado["user"]


def test_prompt_exige_schema_keys_literales_booleanos_y_accion_formato(
    voz_client, monkeypatch,
):
    from app.modules.integracion_ia.services import service

    capturado = {}
    monkeypatch.setattr(
        service.deepseek_provider,
        "generar_json",
        lambda system, user: capturado.update(system=system, user=user) or _respuesta(),
    )
    response = voz_client.post(
        "/ia/reportes/interpretar",
        json={"texto": "Muéstrame los pacientes activos."},
    )
    assert response.status_code == 200
    for texto in (
        "únicas propiedades JSON permitidas",
        "Copia exactamente las keys del catálogo",
        "No uses labels",
        "booleanos JSON true o false",
        "accion_sugerida solo puede ser previsualizar o exportar",
        "formato_sugerido debe ser null",
        "xlsx, pdf, csv o html",
    ):
        assert texto in capturado["system"] or texto in capturado["user"]
