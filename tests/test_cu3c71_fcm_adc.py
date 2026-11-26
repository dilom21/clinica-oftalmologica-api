from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.modules.notificaciones_push.providers import fcm


AHORA = datetime(2026, 11, 21, 20, 0, tzinfo=timezone.utc)


class CredencialesFalsas:
    def __init__(self, *, token=None, expiry=None, error=None):
        self.token = token
        self.expiry = expiry
        self.valid = bool(token)
        self.error = error
        self.refreshes = 0

    def refresh(self, request):
        del request
        self.refreshes += 1
        if self.error is not None:
            raise RuntimeError(self.error)
        self.token = "access-token-renovado-no-registrar"
        self.expiry = AHORA + timedelta(hours=1)
        self.valid = True


class RespuestaFalsa:
    status_code = 200
    headers = {}

    def json(self):
        return {"name": "projects/demo/messages/mensaje-1"}


class ClienteFalso:
    def __init__(self):
        self.llamadas = []

    def post(self, url, **kwargs):
        self.llamadas.append((url, kwargs))
        return RespuestaFalsa()


def proveedor(credenciales):
    return fcm.ProveedorAccessTokenAdc(
        credenciales,
        request_factory=lambda: object(),
        reloj=lambda: AHORA,
    )


def test_reutiliza_token_vigente_sin_refresh():
    credenciales = CredencialesFalsas(
        token="access-token-vigente-no-registrar",
        expiry=AHORA + timedelta(minutes=10),
    )
    adc = proveedor(credenciales)

    assert adc.access_token() == "access-token-vigente-no-registrar"
    assert adc.access_token() == "access-token-vigente-no-registrar"
    assert credenciales.refreshes == 0


@pytest.mark.parametrize(
    "expiry",
    [AHORA - timedelta(seconds=1), AHORA + timedelta(seconds=30), None],
)
def test_refresca_token_expirado_proximo_o_ausente(expiry):
    credenciales = CredencialesFalsas(token="token-anterior", expiry=expiry)
    if expiry is None:
        credenciales.valid = False

    assert proveedor(credenciales).access_token() == "access-token-renovado-no-registrar"
    assert credenciales.refreshes == 1


def test_refresh_concurrente_se_realiza_una_sola_vez():
    credenciales = CredencialesFalsas()
    adc = proveedor(credenciales)
    barrera = threading.Barrier(8)
    resultados = []

    def obtener():
        barrera.wait()
        resultados.append(adc.access_token())

    hilos = [threading.Thread(target=obtener) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert resultados == ["access-token-renovado-no-registrar"] * 8
    assert credenciales.refreshes == 1


def test_error_refresh_es_saneado_y_repr_oculta_credenciales():
    secreto = "private_key=material-ultrasecreto"
    adc = proveedor(CredencialesFalsas(error=secreto))

    with pytest.raises(fcm.FcmNoConfigurado) as error:
        adc.access_token()

    assert secreto not in str(error.value)
    assert error.value.__cause__ is None
    assert secreto not in repr(adc)
    assert "<ocultas>" in repr(adc)


def test_desde_entorno_usa_scope_exclusivo(monkeypatch):
    credenciales = CredencialesFalsas(
        token="token-scope",
        expiry=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    observado = {}

    def adc_default(*, scopes):
        observado["scopes"] = scopes
        return credenciales, "proyecto-detectado-no-usado"

    monkeypatch.setattr(fcm.google.auth, "default", adc_default)

    adc = fcm.ProveedorAccessTokenAdc.desde_entorno()

    assert observado["scopes"] == [fcm.FCM_OAUTH_SCOPE]
    assert adc.access_token() == "token-scope"


def test_adc_ausente_es_error_saneado(monkeypatch):
    secreto = '{"private_key":"NO_FILTRAR"}'

    def adc_default(*, scopes):
        del scopes
        raise RuntimeError(secreto)

    monkeypatch.setattr(fcm.google.auth, "default", adc_default)

    with pytest.raises(fcm.FcmNoConfigurado) as error:
        fcm.ProveedorAccessTokenAdc.desde_entorno()

    assert secreto not in str(error.value)
    assert error.value.__cause__ is None


def test_factory_sin_project_id_no_intenta_adc(monkeypatch):
    monkeypatch.setattr(fcm.app_config, "FCM_PROJECT_ID", None)
    monkeypatch.setattr(
        fcm.ProveedorAccessTokenAdc,
        "desde_entorno",
        classmethod(lambda cls: pytest.fail("ADC no debe resolverse")),
    )

    adaptador = fcm.obtener_proveedor_fcm()

    assert adaptador.configurado is False
    assert adaptador.enviar(token="x", titulo="t", cuerpo="c").clasificacion == fcm.NO_CONFIGURADO


def test_factory_adc_ausente_permanece_no_configurado(monkeypatch):
    monkeypatch.setattr(fcm.app_config, "FCM_PROJECT_ID", "proyecto-demo")

    def sin_adc(cls):
        raise fcm.FcmNoConfigurado("mensaje saneado")

    monkeypatch.setattr(
        fcm.ProveedorAccessTokenAdc, "desde_entorno", classmethod(sin_adc),
    )

    assert fcm.obtener_proveedor_fcm().configurado is False


def test_integracion_http_v1_simulada_usa_token_adc(monkeypatch):
    credenciales = CredencialesFalsas(
        token="access-token-integracion-no-registrar",
        expiry=AHORA + timedelta(minutes=10),
    )
    cliente = ClienteFalso()
    adaptador = fcm.FcmHttpV1(
        proyecto_id="proyecto-demo",
        credenciales=proveedor(credenciales),
        cliente=cliente,
    )

    resultado = adaptador.enviar(
        token="token-dispositivo-ficticio",
        titulo="Titulo generico",
        cuerpo="Cuerpo generico",
        datos={"ruta": "/citas"},
    )

    assert resultado.aceptado is True
    assert len(cliente.llamadas) == 1
    url, argumentos = cliente.llamadas[0]
    assert url == "https://fcm.googleapis.com/v1/projects/proyecto-demo/messages:send"
    assert argumentos["headers"]["Authorization"] == (
        "Bearer access-token-integracion-no-registrar"
    )


@pytest.mark.parametrize("status", [401, 403])
def test_401_403_siguen_siendo_error_global_no_token_invalido(status):
    respuesta = RespuestaFalsa()
    respuesta.status_code = status
    resultado = fcm.FcmHttpV1()._clasificar(respuesta)

    assert resultado.clasificacion == fcm.ERROR_CONFIGURACION
    assert resultado.token_invalido is False

class RespuestaHttpFalsa:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self.headers = {}
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


ID_FCM_CONVENCIONAL = "projects/demo/messages/mensaje-1"
ID_FCM_CON_PORCENTAJE = (
    "projects/myproject-b5ae1/messages/"
    "0:1500415314455276%31bd1c9631bd1c96"
)


@pytest.mark.parametrize(
    "mensaje_id",
    [ID_FCM_CONVENCIONAL, ID_FCM_CON_PORCENTAJE],
)
def test_sanitizar_acepta_ids_fcm_http_v1_validos(mensaje_id):
    assert fcm.sanitizar_mensaje_id(mensaje_id) == mensaje_id


@pytest.mark.parametrize(
    "mensaje_id",
    [
        None,
        123,
        "",
        "projects/demo/messages/",
        "projects//messages/123",
        "projects/demo/messages/id/extra",
        "projects/demo/messages/id con espacios",
        " projects/demo/messages/id",
        "projects/demo/messages/id ",
        "projects/demo/messages/id\n",
        "projects/../messages/123",
        "projects/demo/messages/..",
        "projects/demo/messages/id?query=1",
        "projects/demo/messages/id\\\\otro",
        "projects/demo/messages/" + ("x" * 256),
    ],
)
def test_sanitizar_rechaza_ids_fcm_invalidos_o_inseguros(mensaje_id):
    assert fcm.sanitizar_mensaje_id(mensaje_id) is None


@pytest.mark.parametrize(
    "mensaje_id",
    [ID_FCM_CONVENCIONAL, ID_FCM_CON_PORCENTAJE],
)
def test_http_2xx_con_id_fcm_valido_se_clasifica_aceptado(mensaje_id):
    respuesta = RespuestaHttpFalsa({"name": mensaje_id})

    resultado = fcm.FcmHttpV1()._clasificar(respuesta)

    assert resultado.clasificacion == fcm.ACEPTADO
    assert resultado.codigo == "ACEPTADO"
    assert resultado.aceptado is True
    assert resultado.mensaje_id == mensaje_id


@pytest.mark.parametrize("status_code", [200, 201, 204])
def test_http_2xx_sin_identificador_sigue_siendo_reintentable(status_code):
    respuesta = RespuestaHttpFalsa({}, status_code=status_code)

    resultado = fcm.FcmHttpV1()._clasificar(respuesta)

    assert resultado.clasificacion == fcm.REINTENTABLE
    assert resultado.codigo == "RESPUESTA_SIN_ID"
    assert resultado.aceptado is False
    assert resultado.mensaje_id is None


def test_http_2xx_con_json_invalido_sigue_siendo_reintentable():
    respuesta = RespuestaHttpFalsa(ValueError("JSON inválido"))

    resultado = fcm.FcmHttpV1()._clasificar(respuesta)

    assert resultado.clasificacion == fcm.REINTENTABLE
    assert resultado.codigo == "RESPUESTA_SIN_ID"
    assert resultado.aceptado is False


@pytest.mark.parametrize(
    "mensaje_id",
    [
        "projects/demo/messages/id con espacios",
        "projects/demo/messages/id/extra",
        "projects/../messages/id",
    ],
)
def test_http_2xx_con_identificador_invalido_no_se_acepta(mensaje_id):
    respuesta = RespuestaHttpFalsa({"name": mensaje_id})

    resultado = fcm.FcmHttpV1()._clasificar(respuesta)

    assert resultado.clasificacion == fcm.REINTENTABLE
    assert resultado.codigo == "RESPUESTA_SIN_ID"
    assert resultado.aceptado is False
    assert resultado.mensaje_id is None
