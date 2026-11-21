"""Resources/privacy/provider unit tests. SDK HTTP mocked in-process."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

from fastapi import HTTPException
import httpx
import pytest
from pydantic import ValidationError

from app.core import config
from app.modules.notificaciones_interaccion_chatbot.settings import ChatbotSettings
from app.modules.notificaciones_interaccion_chatbot.services import proveedor as provider
from app.modules.notificaciones_interaccion_chatbot.services.memoria import MemoriaConversaciones, tamano
from app.modules.notificaciones_interaccion_chatbot.services.privacidad import respuesta_local, salida_segura
from app.modules.notificaciones_interaccion_chatbot.casos_uso.cu41_interactuar_chatbot import conversar
from app.modules.notificaciones_interaccion_chatbot.schemas.chatbot import MensajeEntrada, ServiciosTarjeta, ServicioPublico
from tests.test_cu41_chatbot import ModeloFalso, llamada


@pytest.mark.parametrize("campo,valor", [("enabled","incorrecto"),("max_output_tokens",0), ("max_history_messages",1),
    ("max_tool_iterations",99),("max_message_length",0), ("rate_limit_per_minute",0),
    ("deepseek_timeout_seconds",float("inf")), ("deepseek_base_url","http://127.0.0.1"),
    ("request_timeout_seconds",121), ("max_tool_calls",100), ("session_ttl_seconds",0), ("max_context_bytes",0)])
def test_configuracion_invalida(campo,valor):
    with pytest.raises(ValidationError):
        ChatbotSettings(**{campo:valor})


def test_independencia_y_default(monkeypatch):
    for nombre in ChatbotSettings.model_fields:
        monkeypatch.delenv("CHATBOT_"+nombre.upper(),raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY","clave-clinica-no-chatbot")
    cfg = ChatbotSettings.from_env()
    assert not cfg.enabled and not cfg.deepseek_api_key.get_secret_value()
    assert cfg.deepseek_model == "deepseek-flash"
    anterior = config.DEEPSEEK_API_KEY
    monkeypatch.setenv("CHATBOT_DEEPSEEK_API_KEY","clave-chatbot-ficticia")
    assert ChatbotSettings.from_env().deepseek_api_key.get_secret_value() == "clave-chatbot-ficticia"
    assert config.DEEPSEEK_API_KEY == anterior
    monkeypatch.setenv("CHATBOT_MAX_TOOL_CALLS","malo")
    with pytest.raises(RuntimeError) as error: ChatbotSettings.from_env()
    assert "clave-chatbot-ficticia" not in str(error.value)


def test_memoria_propiedad_expiracion():
    reloj = [0.0]; store = MemoriaConversaciones(lambda: reloj[0])
    cfg = ChatbotSettings(session_ttl_seconds=5); owner = (1,1,1,1,"sesion")
    c = store.crear(owner,cfg)
    for ajeno in ((2,2,1,1,"sesion"),(1,1,2,2,"sesion"),(1,1,1,1,"otra")):
        with pytest.raises(HTTPException) as e:
            with store.usar(c.id,ajeno,cfg): pass
        assert e.value.status_code == 404
    reloj[0] = 6
    with pytest.raises(HTTPException):
        with store.usar(c.id,owner,cfg): pass
    assert not store.sesiones


def test_memoria_limites_historial_tamano_y_sesiones():
    cfg = ChatbotSettings(max_history_messages=2,max_sessions=2,max_sessions_per_patient=1,max_context_bytes=8192)
    store = MemoriaConversaciones(); c = store.crear((1,1,1,1,"a"),cfg)
    for i in range(5):
        store.guardar(c,[{"role":"user","content":"a"*1500},{"role":"assistant","content":"b"*1500}],cfg)
    assert len(c.historial) == 2 and tamano(c.historial) <= cfg.max_context_bytes//2
    with pytest.raises(HTTPException): store.crear((1,1,1,1,"a"),cfg)
    store.crear((1,1,2,2,"b"),cfg)
    with pytest.raises(HTTPException): store.crear((2,2,1,1,"c"),cfg)


def test_presupuesto_global_memoria():
    cfg = ChatbotSettings(max_memory_bytes=8192); store = MemoriaConversaciones()
    c=store.crear((1,1,1,1,"a"),cfg)
    store.guardar(c,[{"role":"user","content":"a"*1500},{"role":"assistant","content":"b"*1500}],cfg)
    with pytest.raises(HTTPException) as e: store.crear((2,2,2,2,"b"),cfg)
    assert e.value.status_code == 429


def test_concurrencia_y_presupuesto_global():
    store = MemoriaConversaciones(); cfg = ChatbotSettings(max_concurrent_requests=1)
    owner=(1,1,1,1,"a"); c=store.crear(owner,cfg); otra=store.crear((2,2,2,2,"b"),cfg)
    with store.usar(c.id,owner,cfg):
        for id, actor in ((c.id,owner),(otra.id,otra.propietario)):
            with ThreadPoolExecutor(max_workers=1) as executor:
                def concurrente():
                    with store.usar(id,actor,cfg): pass
                with pytest.raises(HTTPException) as e: executor.submit(concurrente).result()
                assert e.value.status_code == 409
    assert store.activas == 0


@pytest.mark.parametrize("tipo",["paciente","tenant","global","proveedor"])
def test_limites_no_evaden_multiples_conversaciones(tipo):
    reloj=[0.0]; store=MemoriaConversaciones(lambda:reloj[0])
    cambios = {"rate_limit_per_minute":10,"tenant_rate_limit_per_minute":100,"global_rate_limit_per_minute":500}
    if tipo == "paciente": cambios["rate_limit_per_minute"]=1
    if tipo == "tenant": cambios["tenant_rate_limit_per_minute"]=1
    if tipo in {"global","proveedor"}: cambios["global_rate_limit_per_minute"]=1
    cfg=ChatbotSettings(**cambios)
    store.consumir((1,1,1,1,"a"),cfg,proveedor=tipo=="proveedor")
    actor=(1,1,1,1,"b") if tipo=="paciente" else ((1,1,2,2,"b") if tipo=="tenant" else (2,2,2,2,"b"))
    with pytest.raises(HTTPException) as e: store.consumir(actor,cfg,proveedor=tipo=="proveedor")
    assert e.value.status_code == 429
    reloj[0]=61; store.consumir(actor,cfg,proveedor=tipo=="proveedor")


def test_historico_sensible_no_se_reenvia():
    cfg=ChatbotSettings(enabled=True,deepseek_api_key="fake"); store=MemoriaConversaciones()
    c=store.crear((1,1,1,1,"a"),cfg)
    c.historial=[{"role":"user","content":"mi diagnóstico privado"},{"role":"assistant","content":"password: secreto"}]
    model=ModeloFalso()
    asyncio.run(conversar(c,MensajeEntrada(mensaje="Quisiera orientación"),SimpleNamespace(propietario=c.propietario),SimpleNamespace(rollback=lambda:None),cfg,store,model))
    assert "diagnóstico" not in json.dumps(model.payloads,ensure_ascii=False)
    assert "password" not in json.dumps(model.payloads)


@pytest.mark.parametrize("texto",["2026-12-15 por la mañana", "Quiero una cita el lunes", "Tengo una cita", "Consulta general"])
def test_entradas_administrativas_validas(texto):
    assert respuesta_local(texto) is None


@pytest.mark.parametrize("texto",["La consulta cuesta cien", "He reservado tu cita", "https://malo.test", "password secreto", "toma medicamento"])
def test_salida_modelo_no_autoriza_datos_o_acciones(texto):
    assert not salida_segura(texto)


def _sdk(monkeypatch,response=None,handler=None):
    real=provider.AsyncOpenAI
    def factory(**kwargs):
        return real(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler or (lambda request: response))))
    monkeypatch.setattr(provider,"AsyncOpenAI",factory)


@pytest.mark.parametrize("status",[401,402,429,500,503])
def test_errores_sdk_controlados(monkeypatch,status):
    _sdk(monkeypatch,httpx.Response(status,json={"error":{"message":"SECRET-INTERNAL","type":"provider_error"}}))
    with pytest.raises(HTTPException) as e:
        asyncio.run(provider.ProveedorConversacional().completar([{"role":"user","content":"hola"}],[],ChatbotSettings(deepseek_api_key="fake"),1))
    assert e.value.status_code == 503 and "SECRET" not in e.value.detail


def test_sdk_real_con_tools_y_config_independiente(monkeypatch):
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer CU41-fake-only"
        return httpx.Response(200,json={"id":"fake","object":"chat.completion","created":1,"model":"deepseek-flash","choices":[{
            "index":0,"finish_reason":"tool_calls","message":{"role":"assistant","content":None,"reasoning_content":"NEVER-EXPOSE",
            "tool_calls":llamada("consultar_servicios")["tool_calls"]}}]})
    _sdk(monkeypatch,handler=handler)
    cfg=ChatbotSettings(deepseek_api_key="CU41-fake-only")
    result=asyncio.run(provider.ProveedorConversacional().completar([{"role":"user","content":"Servicios"}],[],cfg,1))
    assert result.tool_calls[0].id == "call_1" and "NEVER" not in result.model_dump_json()
    assert captured[0]["thinking"] == {"type":"disabled"}
    assert captured[0]["model"] == "deepseek-flash" and captured[0]["max_tokens"] == 512
    assert captured[0]["stream"] is False


def test_timeout_sdk_sin_reintentos(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(1); raise httpx.ReadTimeout("secret-timeout",request=request)
    _sdk(monkeypatch,handler=handler)
    with pytest.raises(HTTPException) as e:
        asyncio.run(provider.ProveedorConversacional().completar([],[],ChatbotSettings(deepseek_api_key="fake"),1))
    assert e.value.status_code == 503 and len(calls)==1


@pytest.mark.parametrize("value",[{}, {"content":" "}, {"content":{}}, {"tool_calls":[{"id":"x","function":{"name":"a","arguments":"{}"}}]*2}])
def test_turno_invalido(value):
    with pytest.raises(HTTPException) as e: provider.validar_turno(value)
    assert e.value.status_code == 502


@pytest.mark.parametrize("caso",["tools","args","resultado","contexto"])
def test_presupuestos_orquestador(monkeypatch,caso):
    cfg=ChatbotSettings(enabled=True,deepseek_api_key="fake")
    cambios={"tools":{"max_tool_calls":1},"args":{"max_tool_arguments_bytes":10},
             "resultado":{"max_tool_result_bytes":10},"contexto":{"max_context_bytes":10}}
    cfg=cfg.model_copy(update=cambios[caso])
    store=MemoriaConversaciones(); c=store.crear((1,1,1,1,"a"),cfg); model=ModeloFalso()
    value=llamada("consultar_servicios",{"busqueda":"consulta"})
    if caso=="tools": value["tool_calls"]+=llamada("consultar_profesionales",id="other")["tool_calls"]
    model.respuestas=[value]
    from app.modules.notificaciones_interaccion_chatbot.services import herramientas
    monkeypatch.setattr(herramientas,"ejecutar",lambda *a: ServiciosTarjeta(servicios=[ServicioPublico(id=1,nombre="Consulta",descripcion=None,precio=None,duracion_minutos=None)]))
    with pytest.raises(HTTPException) as e:
        asyncio.run(conversar(c,MensajeEntrada(mensaje="Servicios"),SimpleNamespace(propietario=c.propietario),SimpleNamespace(rollback=lambda:None),cfg,store,model))
    assert e.value.status_code in (422,429,502)
