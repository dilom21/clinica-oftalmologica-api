"""Turnos sociales: respuesta local, sin proveedor, sin perder contexto."""
import copy
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tests import test_cu38_catalogo as base
from tests import test_cu41_chatbot as anterior
from app.modules.notificaciones_interaccion_chatbot.casos_uso import cu41_interactuar_chatbot as caso_uso
from app.modules.notificaciones_interaccion_chatbot.services import herramientas
from app.modules.notificaciones_interaccion_chatbot.services.social import respuesta_social

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


def _citas(caso):
    with caso.registry.get_session(SimpleNamespace(empresa_codigo="ALPHA")) as db:
        return db.scalar(text("SELECT count(*) FROM cita"))


@pytest.mark.parametrize("texto", [
    "Gracias", "Muchas gracias", "Okay muchas gracias", "Okey, gracias",
    "Perfecto, gracias", "Eso es todo, gracias", "Hola", "Buenos días",
    "Adiós", "Hasta luego", "Gracias, eso es todo", "¿Okay, muchas gracias?",
])
def test_detector_social_inequivoco(texto):
    assert respuesta_social(texto)


@pytest.mark.parametrize("texto", [
    "Gracias, ¿qué horarios hay mañana?",
    "Gracias, muéstrame mis citas.",
    "Hola, necesito una cita.",
    "Perfecto, ¿y el precio?",
    "Gracias, tengo dolor intenso en el ojo.",
    "Okay",
    "Perfecto",
    "Quiero agendar una cita.",
])
def test_detector_no_intercepta_peticiones(texto):
    assert respuesta_social(texto) is None


@pytest.mark.parametrize("mensaje", [
    "Gracias", "Muchas gracias", "Okay muchas gracias",
    "Okey, gracias", "Perfecto, gracias", "Eso es todo, gracias",
])
@pytest.mark.parametrize("modalidad", ["texto", "voz"])
def test_agradecimiento_local_sin_proveedor(caso, mensaje, modalidad):
    id = crear(caso)
    r = enviar(caso, id, mensaje, modalidad=modalidad)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["conversacion_id"] == id
    assert cuerpo["tipo"] == "texto" and cuerpo["tarjetas"] == []
    assert "de nada" in cuerpo["respuesta"].lower()
    assert cuerpo["texto_voz"] and "**" not in cuerpo["texto_voz"]
    assert caso.model.payloads == []


def test_saludo_y_despedida_locales(caso):
    id = crear(caso)
    saludo = enviar(caso, id, "Hola").json()
    assert "hola" in saludo["respuesta"].lower() and saludo["tarjetas"] == []
    despedida = enviar(caso, id, "Adiós", modalidad="voz").json()
    assert "hasta luego" in despedida["respuesta"].lower()
    assert caso.model.payloads == []


def test_mensaje_mixto_sigue_flujo_normal(caso):
    id = crear(caso)
    caso.model.respuestas = [
        llamada("consultar_disponibilidad", {"oftalmologo_id": 1, "fecha": caso.fecha.isoformat()}),
        {"content": "Horarios consultados."},
    ]
    r = enviar(caso, id, "Gracias, ¿qué horarios hay mañana?").json()
    assert r["tarjetas"][0]["tipo"] == "disponibilidad"
    assert len(caso.model.payloads) >= 1
    assert "de nada" not in r["respuesta"].lower()


@pytest.mark.parametrize("modalidad", ["texto", "voz"])
def test_agradecimiento_conserva_contexto_de_servicios(caso, modalidad):
    id = crear(caso)
    assert enviar(caso, id, "¿Qué servicios tienen?").json()["tarjetas"][0]["servicios"]
    antes = copy.deepcopy(caso.store.sesiones[id].contexto)
    r = enviar(caso, id, "Okay muchas gracias", modalidad=modalidad).json()
    assert r["tarjetas"] == [] and caso.model.payloads == []
    assert caso.store.sesiones[id].contexto.servicios == antes.servicios
    referencia = enviar(caso, id, "¿Cuánto cuesta el primero?").json()
    assert referencia["tarjetas"][0]["servicios"][0]["id"] == 1
    assert caso.model.payloads == []


@pytest.mark.parametrize("modalidad", ["texto", "voz"])
def test_agradecimiento_conserva_contexto_de_disponibilidad(caso, modalidad):
    id = crear(caso)
    caso.model.respuestas = [
        llamada("consultar_disponibilidad", {"oftalmologo_id": 1, "fecha": caso.fecha.isoformat()})
    ]
    assert enviar(caso, id, "¿Qué horarios tiene ALPHA Profesional el lunes?").status_code == 200
    antes = caso.store.sesiones[id].contexto.model_dump(mode="json")
    payloads = len(caso.model.payloads)
    r = enviar(caso, id, "Muchas gracias", modalidad=modalidad)
    assert r.status_code == 200 and r.json()["tarjetas"] == []
    assert len(caso.model.payloads) == payloads
    assert caso.store.sesiones[id].contexto.model_dump(mode="json") == antes


@pytest.mark.parametrize("modalidad", ["texto", "voz"])
def test_agradecimiento_tras_propuesta_no_reserva(caso, modalidad):
    id = crear(caso)
    caso.model.respuestas = [
        llamada("consultar_disponibilidad", {"oftalmologo_id": 1, "fecha": caso.fecha.isoformat()})
    ]
    assert enviar(caso, id, "Horarios disponibles").status_code == 200
    assert enviar(caso, id, "Prefiero por la mañana", modalidad="voz").status_code == 200
    propuesta = enviar(caso, id, "A las nueve está bien", modalidad="voz").json()
    assert propuesta["tarjetas"][0]["tipo"] == "propuesta_cita"
    assert caso.store.sesiones[id].contexto.propuesta_preparada is True
    citas_antes = _citas(caso)
    payloads = len(caso.model.payloads)
    r = enviar(caso, id, "Perfecto, gracias", modalidad=modalidad).json()
    assert r["conversacion_id"] == id and r["tarjetas"] == []
    assert "pendiente" in r["respuesta"].lower()
    assert "reservada" not in r["respuesta"].lower()
    assert caso.store.sesiones[id].contexto.propuesta_preparada is True
    assert len(caso.model.payloads) == payloads
    assert _citas(caso) == citas_antes


def test_privacidad_tiene_prioridad_sobre_social(caso):
    id = crear(caso)
    r = enviar(caso, id, "Gracias, tengo dolor intenso en el ojo").json()
    assert r["tipo"] == "privacidad" and r["tarjetas"] == []
    assert caso.model.payloads == []


def test_respaldo_sigue_rechazando_salida_insegura(caso):
    id = crear(caso)
    caso.model.respuestas = [{"content": "La cita quedó reservada para el 27 de noviembre a las 15:00."}]
    r = enviar(caso, id, "Necesito orientación").json()
    assert "No pude obtener información verificada" in r["respuesta"]
    assert "reservada" not in r["respuesta"].lower()


def test_social_no_cierra_la_conversacion_y_respeta_tenant(caso):
    id = crear(caso)
    assert enviar(caso, id, "Hola").status_code == 200
    assert enviar(caso, id, "¿Qué servicios tienen?").status_code == 200
    assert enviar(caso, id, "Gracias", token=base._tenant("BETA")).status_code == 404
    assert id in caso.store.sesiones
