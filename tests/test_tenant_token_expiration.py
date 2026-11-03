"""Tenant JWT expiry is mandatory before opening any request database."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.core import dependencies, security
from app.core.config import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, JWT_SECRET_KEY
from app.database import session as clinical_session
from tests.test_cu30_inventario_alertas import EMPRESAS, tenants


RUTAS_CU30 = (
    "/inventario-proveedores/inventario",
    "/inventario-proveedores/alertas",
    "/inventario-proveedores/productos/1/lotes",
)
EXP_INVALIDAS = ("sin_exp", "expirado", "null", "objeto", "lista", "infinito", "nan", "texto")


def _claims(estado="vigente", codigo=EMPRESAS[0]):
    numero = EMPRESAS.index(codigo) + 1
    claims = {"sub": "7", "rol_id": 1, "tenant_id": numero, "empresa_id": numero,
              "empresa_codigo": codigo, "token_type": "tenant"}
    if estado == "sin_exp":
        return claims
    expiraciones = {
        "vigente": datetime.now(timezone.utc) + timedelta(minutes=30),
        "expirado": datetime.now(timezone.utc) - timedelta(minutes=1),
        "null": None, "objeto": {"no": "fecha"}, "lista": [1, 2],
        "infinito": float("inf"), "nan": float("nan"), "texto": "fecha-invalida",
    }
    claims["exp"] = expiraciones[estado]
    return claims


def _token(estado="vigente", codigo=EMPRESAS[0]):
    return jwt.encode(_claims(estado, codigo), JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _credentials(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _assert_401(error):
    assert error.status_code == 401
    assert error.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("estado", EXP_INVALIDAS)
def test_decoder_no_acepta_tenant_sin_exp_o_con_exp_invalida(estado):
    with pytest.raises(jwt.InvalidTokenError):
        security.decodificar_access_token(_token(estado))


@pytest.mark.parametrize("estado", EXP_INVALIDAS)
def test_claims_tenant_exige_exp_aun_sin_intervencion_de_get_db(estado):
    with pytest.raises(HTTPException) as captured:
        dependencies.get_tenant_claims(_credentials(_token(estado)))
    _assert_401(captured.value)


class UsuarioDBPrueba:
    def __init__(self):
        self.lecturas = 0
        self.usuario = SimpleNamespace(id=7, rol_id=1, estado=True)

    def get(self, _modelo, usuario_id):
        self.lecturas += 1
        assert usuario_id == 7
        return self.usuario


@pytest.mark.parametrize("estado", EXP_INVALIDAS)
def test_obtener_usuario_rechaza_exp_antes_de_leer_incluso_con_db_inyectada(estado):
    db = UsuarioDBPrueba()
    with pytest.raises(HTTPException) as captured:
        dependencies.obtener_usuario_actual(_credentials(_token(estado)), db)
    _assert_401(captured.value)
    assert db.lecturas == 0


@pytest.mark.parametrize("estado", ("sin_exp", "expirado", "null", "objeto", "lista"))
def test_override_get_db_no_evade_validacion_exp_del_usuario(estado):
    api = FastAPI()
    db = UsuarioDBPrueba()

    @api.get("/usuario-actual")
    def usuario_actual(usuario=Depends(dependencies.obtener_usuario_actual)):
        return {"id": usuario.id}

    api.dependency_overrides[dependencies.get_db] = lambda: db
    with TestClient(api) as client:
        response = client.get("/usuario-actual", headers={"Authorization": "Bearer " + _token(estado)})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert db.lecturas == 0


def test_tenant_vigente_conserva_claims_y_usuario_actual():
    token = _token()
    claims = security.decodificar_access_token(token)
    assert claims["token_type"] == "tenant" and claims["exp"] > datetime.now(timezone.utc).timestamp()
    assert dependencies.get_tenant_claims(_credentials(token)) == claims
    db = UsuarioDBPrueba()
    assert dependencies.obtener_usuario_actual(_credentials(token), db) is db.usuario
    assert db.lecturas == 1


def test_emisor_tenant_incluye_exp_utc_futura_sin_cambiar_contrato():
    antes = datetime.now(timezone.utc).timestamp()
    token = security.crear_tenant_access_token(7, 1, 1, 1, EMPRESAS[0])
    despues = datetime.now(timezone.utc).timestamp()
    claims = security.decodificar_access_token(token)
    assert isinstance(claims["exp"], int)
    assert antes < claims["exp"] <= despues + JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert claims["sub"] == "7" and claims["rol_id"] == 1
    assert claims["tenant_id"] == claims["empresa_id"] == 1
    assert claims["empresa_codigo"] == EMPRESAS[0] and claims["token_type"] == "tenant"


@pytest.mark.parametrize("tipo", ("legacy", "saas_admin"))
def test_decoder_generico_conserva_contrato_sin_exp_no_tenant(tipo):
    claims = {"sub": "7", "rol_id": 1}
    if tipo == "saas_admin":
        claims.update(token_type="saas_admin", saas_usuario_id=7)
    token = jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    assert security.decodificar_access_token(token) == claims
    with pytest.raises(HTTPException) as captured:
        dependencies.get_tenant_claims(_credentials(token))
    _assert_401(captured.value)
    if tipo == "legacy":
        db = UsuarioDBPrueba()
        assert dependencies.obtener_usuario_actual(_credentials(token), db) is db.usuario
        assert db.lecturas == 1


@pytest.mark.parametrize("tipo", ("legacy", "saas_admin"))
def test_decoder_generico_sigue_rechazando_exp_no_tenant_expirada(tipo):
    claims = {"sub": "7", "rol_id": 1,
              "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}
    if tipo == "saas_admin":
        claims.update(token_type="saas_admin", saas_usuario_id=7)
    token = jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    with pytest.raises(jwt.ExpiredSignatureError):
        security.decodificar_access_token(token)


@pytest.mark.parametrize("caso", ("firma", "algoritmo"))
def test_decoder_mantiene_verificacion_de_firma_y_algoritmo(caso):
    token = jwt.encode(_claims(),
                       "firma-de-prueba-invalida-con-longitud-suficiente" if caso == "firma" else JWT_SECRET_KEY,
                       algorithm=JWT_ALGORITHM if caso == "firma" else "HS384")
    with pytest.raises(jwt.InvalidTokenError):
        security.decodificar_access_token(token)


@pytest.fixture
def cliente_con_aperturas(tenants, monkeypatch):
    client, _engines, _control, registry, _sentencias = tenants
    aperturas = {"control": 0, "tenant": 0, "legacy": 0}
    control_original = clinical_session.open_control_session
    tenant_original = registry.get_session

    def control():
        aperturas["control"] += 1
        return control_original()

    def tenant(contexto):
        aperturas["tenant"] += 1
        return tenant_original(contexto)

    def legacy():
        aperturas["legacy"] += 1
        pytest.fail("Un token tenant no debe abrir la base legacy")

    monkeypatch.setattr(clinical_session, "open_control_session", control)
    monkeypatch.setattr(clinical_session, "SessionLocal", legacy)
    monkeypatch.setattr(registry, "get_session", tenant)
    return client, aperturas


@pytest.mark.parametrize("codigo", EMPRESAS)
@pytest.mark.parametrize("estado", ("vigente", "expirado", "sin_exp"))
def test_cu30_tres_get_exp_y_aislamiento_en_siete_tenants(cliente_con_aperturas, codigo, estado):
    client, aperturas = cliente_con_aperturas
    headers = {"Authorization": "Bearer " + _token(estado, codigo)}
    for ruta in RUTAS_CU30:
        response = client.get(ruta, headers=headers)
        if estado == "vigente":
            assert response.status_code == 200
            datos = response.json()
            if ruta.endswith("/inventario"):
                assert all(codigo in producto["nombre"] for producto in datos["items"])
            elif ruta.endswith("/alertas"):
                assert all(codigo in alerta["producto_nombre"] for alerta in datos["items"])
            else:
                assert all(lote["codigo"].startswith(codigo) for lote in datos["lotes"])
        else:
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
    esperadas = 3 if estado == "vigente" else 0
    assert aperturas == {"control": esperadas, "tenant": esperadas, "legacy": 0}


@pytest.mark.parametrize("ruta", ("/pacientes/1", "/seguridad/usuarios", "/seguridad/menu", "/reportes/catalogo"))
@pytest.mark.parametrize("estado", ("sin_exp", "expirado", "null", "objeto", "lista"))
def test_rutas_clinicas_compartidas_rechazan_antes_de_cualquier_apertura(cliente_con_aperturas, ruta, estado):
    client, aperturas = cliente_con_aperturas
    response = client.get(ruta, headers={"Authorization": "Bearer " + _token(estado)})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert aperturas == {"control": 0, "tenant": 0, "legacy": 0}


def test_saas_sin_exp_sigue_sin_ser_token_clinico(cliente_con_aperturas):
    client, aperturas = cliente_con_aperturas
    token = jwt.encode({"sub": "7", "saas_usuario_id": 7, "token_type": "saas_admin"},
                       JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    for ruta in (*RUTAS_CU30, "/pacientes/1", "/seguridad/menu"):
        response = client.get(ruta, headers={"Authorization": "Bearer " + token})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert aperturas == {"control": 0, "tenant": 0, "legacy": 0}
