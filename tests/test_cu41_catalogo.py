"""Regresiones del catálogo: listado general determinista, filtros y vacíos."""
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests import test_cu41_chatbot as anterior
from app.modules.notificaciones_interaccion_chatbot.casos_uso import cu41_interactuar_chatbot as caso_uso
from app.modules.notificaciones_interaccion_chatbot.schemas.chatbot import ServiciosTarjeta
from app.modules.notificaciones_interaccion_chatbot.services import herramientas
from app.modules.notificaciones_interaccion_chatbot.services.composicion import componer
from app.modules.notificaciones_interaccion_chatbot.services.contexto import (
    ContextoPublico, listado_general_servicios,
)

chat = anterior.chat
cliente_catalogo = anterior.cliente_catalogo
crear = anterior.crear
enviar = anterior.enviar
llamada = anterior.llamada


@pytest.fixture
def caso(request, monkeypatch):
    hoy = date(2032, 11, 21)
    monkeypatch.setattr(anterior, "fecha_local_aplicacion", lambda: hoy)
    monkeypatch.setattr(herramientas, "fecha_local_aplicacion", lambda: hoy)
    monkeypatch.setattr(caso_uso, "fecha_local_aplicacion", lambda: hoy)
    return request.getfixturevalue("chat")


@pytest.mark.parametrize("texto", [
    "¿Qué servicios oftalmológicos ofrece esta clínica y cuáles son sus precios?",
    "¿Qué servicios tienen?",
    "¿Cuáles son los precios de las atenciones disponibles?",
    "Muéstrame el catálogo de esta clínica.",
    "¿Qué atenciones ofrecen?",
    "Quiero ver el catálogo, por favor.",
    "Dime los servicios disponibles.",
    "Necesito información sobre servicios.",
])
def test_listado_general_resuelve_localmente_sin_filtros(caso, texto):
    id = crear(caso)
    caso.model.respuestas = [{"content": "No debería llamarse al modelo"}]
    r = enviar(caso, id, texto).json()
    assert caso.model.payloads == [], "el listado general no debe consumir proveedor"
    assert r["tipo"] == "datos"
    tarjeta = r["tarjetas"][0]
    assert tarjeta["tipo"] == "servicios"
    assert tarjeta["busqueda"] in (None, ""), tarjeta
    assert [s["id"] for s in tarjeta["servicios"]] == [1, 3]
    assert tarjeta["servicios"][0]["precio"] == "100.50"
    assert tarjeta["servicios"][1]["precio"] is None
    assert "100" in r["texto_voz"] and "precio no registrado" in r["texto_voz"]
    assert "**" not in r["texto_voz"] and "\n" not in r["texto_voz"]


@pytest.mark.parametrize("texto,esperado", [
    ("¿Qué servicios oftalmológicos ofrece esta clínica?", True),
    ("¿Cuáles son los precios de las atenciones disponibles?", True),
    ("Muéstrame el catálogo de esta clínica.", True),
    ("Dime los servicios disponibles.", True),
    ("¿Cuánto cuesta el Servicio 100% especial?", False),
    ("¿Cuánto cuesta la consulta general?", False),
    ("Busco consulta general oftalmológica", False),
    ("¿Tienen cirugía láser de retina?", False),
    ("Detalle del servicio 3", False),
    ("¿Y el primero?", False),
    ("Continúa con más servicios", False),
    ("¿Tienen el servicio de consulta general?", False),
])
def test_gate_de_listado_distingue_busquedas(texto, esperado):
    assert listado_general_servicios(texto) is esperado


def test_busqueda_inexistente_no_devuelve_catalogo_completo(caso):
    id = crear(caso)
    caso.model.respuestas = [
        llamada("consultar_servicios", {"busqueda": "cirugia laser retina"}),
        {"content": "No encontré ese servicio."},
    ]
    r = enviar(caso, id, "¿Tienen cirugía láser de retina?").json()
    tarjeta = r["tarjetas"][0]
    assert tarjeta["tipo"] == "servicios" and tarjeta["servicios"] == []
    assert tarjeta["busqueda"] == "cirugia laser retina"
    assert "no encontré servicios que coincidan" in r["respuesta"].lower()
    assert "página" not in r["respuesta"].lower()
    assert len(caso.model.payloads) == 2  # búsqueda semántica, no listado completo


def test_precio_especifico_conserva_null(caso):
    id = crear(caso)
    caso.model.respuestas = [
        llamada("consultar_servicios", {"busqueda": "100% especial"}),
        {"content": "Ese servicio no tiene precio registrado."},
    ]
    r = enviar(caso, id, "¿Cuánto cuesta el Servicio 100% especial?").json()
    tarjeta = r["tarjetas"][0]
    assert [s["id"] for s in tarjeta["servicios"]] == [3]
    assert tarjeta["servicios"][0]["precio"] is None
    assert "precio no registrado" in r["respuesta"].lower()


def test_referencia_a_servicio_previo_se_resuelve_localmente(caso):
    id = crear(caso)
    assert enviar(caso, id, "¿Qué servicios tienen?").status_code == 200
    assert caso.model.payloads == []
    por_ordinal = enviar(caso, id, "¿Cuánto cuesta el primero?").json()
    assert por_ordinal["tarjetas"][0]["servicios"][0]["id"] == 1
    assert por_ordinal["tarjetas"][0]["servicios"][0]["precio"] == "100.50"
    por_nombre = enviar(caso, id, "¿Y el Servicio 100% especial?").json()
    assert por_nombre["tarjetas"][0]["servicios"][0]["id"] == 3
    assert por_nombre["tarjetas"][0]["servicios"][0]["precio"] is None
    assert caso.model.payloads == []


def test_paginacion_solicitada_continua_en_la_misma_busqueda(caso):
    with caso.registry.get_session(SimpleNamespace(empresa_codigo="ALPHA")) as db:
        for sid in range(10, 14):
            db.execute(text(
                "INSERT INTO servicio_oftalmologico (id,nombre,precio,duracion,estado) "
                "VALUES (:id,:n,10,30,1)"), {"id": sid, "n": f"ZZ servicio {sid}"})
        db.commit()
    id = crear(caso)
    primera = enviar(caso, id, "Muéstrame el catálogo de esta clínica.").json()
    ids_primera = [s["id"] for s in primera["tarjetas"][0]["servicios"]]
    assert len(ids_primera) == 5 and primera["tarjetas"][0]["hay_mas"] is True
    segunda = enviar(caso, id, "Continúa con más servicios").json()
    ids_segunda = [s["id"] for s in segunda["tarjetas"][0]["servicios"]]
    assert segunda["tarjetas"][0]["offset"] == 5
    assert set(ids_primera).isdisjoint(ids_segunda)
    assert caso.model.payloads == []


def test_catalogo_sin_activos_no_habla_de_pagina(caso):
    with caso.registry.get_session(SimpleNamespace(empresa_codigo="ALPHA")) as db:
        db.execute(text("UPDATE servicio_oftalmologico SET estado=0"))
        db.commit()
    id = crear(caso)
    r = enviar(caso, id, "¿Qué servicios oftalmológicos ofrece esta clínica?").json()
    assert r["tarjetas"][0]["servicios"] == []
    assert "no encontré servicios activos" in r["respuesta"].lower()
    assert "página" not in r["respuesta"].lower()
    assert caso.model.payloads == []


def test_mensajes_de_vacio_distinguen_busqueda_y_pagina():
    busqueda, _ = componer(
        [ServiciosTarjeta(servicios=[], busqueda="retina")],
        "¿Tienen retina?", ContextoPublico(intencion="consultar_servicios"))
    pagina, _ = componer(
        [ServiciosTarjeta(servicios=[], busqueda="", offset=10)],
        "Continúa", ContextoPublico(intencion="consultar_servicios"))
    catalogo, _ = componer(
        [ServiciosTarjeta(servicios=[], busqueda="")],
        "Catálogo", ContextoPublico(intencion="consultar_servicios"))
    assert "coincidan con esa búsqueda" in busqueda
    assert "no hay más resultados en esa página" in pagina.lower()
    assert "no encontré servicios activos" in catalogo.lower()
    assert "página" not in busqueda.lower() and "página" not in catalogo.lower()


def test_saludo_indica_brevedad_y_voz_sin_markdown(caso):
    assert "una o dos frases" in caso_uso.SISTEMA
    assert "no enumeres todas las capacidades" in caso_uso.SISTEMA
    id = crear(caso)
    caso.model.respuestas = [{"content": "**Hola**.\n- Servicios.\n- Citas.\n- Profesionales."}]
    r = enviar(caso, id, "Hola, ¿en qué me puedes ayudar?").json()
    assert "**" not in r["texto_voz"] and "\n" not in r["texto_voz"]
    assert "Hola" in r["texto_voz"] and "Servicios" in r["texto_voz"]
