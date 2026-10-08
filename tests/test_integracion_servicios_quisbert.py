"""Integracion selectiva del catalogo de servicios, sin DDL de produccion."""

import inspect
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core import dependencies
from app.core.security import crear_access_token, crear_tenant_access_token
from app.database import session
from app.database.base import Base
from app.main import allowed_origins, app
from app.modules.gestion_historial_clinico.models.models import (
    ServicioOftalmologico,
)
from app.modules.gestion_servicios.schemas.schemas import ServicioCrear
from app.modules.gestion_servicios.services import service
from app.modules.gestion_usuarios_seguridad.models.models import Bitacora, Usuario
from tests.test_clinical_request_routing import routed_client  # noqa: F401


URL = "/servicios-oftalmologicos/"
DATOS = {
    "nombre": "Tomografia de coherencia optica",
    "descripcion": "Estudio de retina",
    "precio_base": 350.25,
    "duracion_estimada": 30,
    "estado": True,
}


@pytest.fixture
def db_catalogo(db_historial):
    db_historial.execute(text("""
        CREATE TABLE servicio_oftalmologico (
            id INTEGER PRIMARY KEY,
            nombre VARCHAR(150) NOT NULL,
            descripcion TEXT,
            precio NUMERIC(10,2),
            duracion INTEGER,
            estado BOOLEAN
        )
    """))
    db_historial.execute(text("""
        INSERT INTO servicio_oftalmologico
            (id, nombre, descripcion, precio, duracion, estado)
        VALUES
            (1, 'Tonometria', 'Presion intraocular', 120.50, 20, 1),
            (2, 'Agudeza visual', NULL, 80.00, NULL, 1)
    """))
    db_historial.execute(text(
        "INSERT INTO rol VALUES (2, 'Recepcionista', NULL, 1, 0, '2026-01-01')",
    ))
    db_historial.execute(text(
        "INSERT INTO usuario VALUES (8, 'recepcion@example.test', 'x', 1, '2026-01-01', 2)",
    ))
    db_historial.execute(text(
        "INSERT INTO funcion VALUES (30, 'Registrar servicios realizados', 1)",
    ))
    db_historial.execute(text(
        "INSERT INTO rol_funcion VALUES (4, 1, 30, 3)",
    ))
    db_historial.commit()
    return db_historial


@pytest.fixture
def cliente_catalogo(db_catalogo):
    def db_local():
        yield db_catalogo

    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[session.get_db] = db_local
    app.dependency_overrides[dependencies.get_db] = db_local
    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = f"Bearer {crear_access_token(7, 1)}"
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)


def autenticar(client, usuario_id, rol_id):
    client.headers["Authorization"] = (
        f"Bearer {crear_access_token(usuario_id, rol_id)}"
    )


def test_listar_y_consultar_mapean_contrato_angular(cliente_catalogo):
    listado = cliente_catalogo.get(URL)
    assert listado.status_code == 200, listado.text
    assert [item["nombre"] for item in listado.json()] == [
        "Agudeza visual", "Tonometria",
    ]
    tonometria = cliente_catalogo.get(f"{URL}1")
    assert tonometria.status_code == 200
    assert tonometria.json() == {
        "id": 1,
        "nombre": "Tonometria",
        "descripcion": "Presion intraocular",
        "precio_base": 120.5,
        "duracion_estimada": 20,
        "estado": True,
    }


def test_crear_servicio_convierte_campos_y_audita(cliente_catalogo, db_catalogo):
    respuesta = cliente_catalogo.post(URL, json=DATOS)
    assert respuesta.status_code == 201, respuesta.text
    creado = respuesta.json()
    assert creado["precio_base"] == 350.25
    assert creado["duracion_estimada"] == 30
    registro = db_catalogo.get(ServicioOftalmologico, creado["id"])
    assert registro.precio == Decimal("350.25")
    assert registro.duracion == 30
    auditoria = db_catalogo.scalar(
        select(Bitacora).where(
            Bitacora.accion == "CREAR_SERVICIO_OFTALMOLOGICO",
        ),
    )
    assert auditoria.usuario_id == 7
    assert auditoria.entidad_afectada == "servicio_oftalmologico"
    assert auditoria.id_registro_afectado == creado["id"]


def test_actualizacion_es_parcial_y_permite_limpiar_opcionales(
    cliente_catalogo,
    db_catalogo,
):
    respuesta = cliente_catalogo.put(
        f"{URL}1",
        json={"precio_base": "199.99", "descripcion": "  ", "duracion_estimada": None},
    )
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json() == {
        "id": 1,
        "nombre": "Tonometria",
        "descripcion": None,
        "precio_base": 199.99,
        "duracion_estimada": None,
        "estado": True,
    }
    actualizado = db_catalogo.get(ServicioOftalmologico, 1)
    assert actualizado.precio == Decimal("199.99")
    assert db_catalogo.scalar(select(func.count(Bitacora.id)).where(
        Bitacora.accion == "ACTUALIZAR_SERVICIO_OFTALMOLOGICO",
    )) == 1


@pytest.mark.parametrize("cambios", [
    {"nombre": ""},
    {"nombre": "x" * 151},
    {"precio_base": -0.01},
    {"precio_base": "NaN"},
    {"precio_base": "Infinity"},
    {"precio_base": True},
    {"precio_base": 1.001},
    {"precio_base": 100000000},
    {"duracion_estimada": 0},
    {"duracion_estimada": -1},
    {"duracion_estimada": True},
    {"id": 99},
])
def test_rechaza_datos_invalidos(cliente_catalogo, db_catalogo, cambios):
    respuesta = cliente_catalogo.post(URL, json=DATOS | cambios)
    assert respuesta.status_code == 422, respuesta.text
    assert db_catalogo.scalar(select(func.count(ServicioOftalmologico.id))) == 2


@pytest.mark.parametrize("datos", [{}, {"nombre": None}, {"precio_base": None}, {"estado": None}])
def test_put_rechaza_actualizacion_vacia_o_null_no_valido(cliente_catalogo, datos):
    assert cliente_catalogo.put(f"{URL}1", json=datos).status_code == 422


def test_campos_obligatorios_y_404(cliente_catalogo):
    datos = DATOS.copy()
    datos.pop("nombre")
    assert cliente_catalogo.post(URL, json=datos).status_code == 422
    datos = DATOS.copy()
    datos.pop("precio_base")
    assert cliente_catalogo.post(URL, json=datos).status_code == 422
    assert cliente_catalogo.get(f"{URL}999").status_code == 404
    assert cliente_catalogo.put(f"{URL}999", json={"nombre": "Otro"}).status_code == 404


def test_requiere_token_y_administrador_para_escrituras(cliente_catalogo):
    cliente_catalogo.headers.pop("Authorization")
    assert cliente_catalogo.get(URL).status_code == 401
    assert cliente_catalogo.post(URL, json=DATOS).status_code == 401
    autenticar(cliente_catalogo, 8, 2)
    assert cliente_catalogo.get(URL).status_code == 403
    assert cliente_catalogo.post(URL, json=DATOS).status_code == 403
    assert cliente_catalogo.put(f"{URL}1", json={"nombre": "Otro"}).status_code == 403


def test_service_revalida_rol_administrador(db_catalogo):
    usuario = db_catalogo.get(Usuario, 8)
    with pytest.raises(Exception) as exc:
        service.crear(
            db_catalogo,
            ServicioCrear(**DATOS),
            usuario,
        )
    assert getattr(exc.value, "status_code", None) == 403


def test_fallo_de_bitacora_revierte_servicio(cliente_catalogo, db_catalogo, monkeypatch):
    def fallar(*args, **kwargs):
        raise SQLAlchemyError("Fallo simulado")

    monkeypatch.setattr(service, "registrar_bitacora", fallar)
    with pytest.raises(SQLAlchemyError):
        cliente_catalogo.post(URL, json=DATOS)
    assert db_catalogo.scalar(select(func.count(ServicioOftalmologico.id))) == 2
    assert db_catalogo.scalar(select(func.count(Bitacora.id))) == 0


def test_catalogo_esta_aislado_entre_tenants(routed_client):
    client, engines, _, _ = routed_client
    for codigo, nombre in (("ALPHA", "Servicio Alpha"), ("BETA", "Servicio Beta")):
        with engines[codigo].begin() as conexion:
            conexion.exec_driver_sql("""
                CREATE TABLE servicio_oftalmologico (
                    id INTEGER PRIMARY KEY, nombre VARCHAR(150) NOT NULL,
                    descripcion TEXT, precio NUMERIC(10,2), duracion INTEGER,
                    estado BOOLEAN
                )
            """)
            conexion.exec_driver_sql(
                "INSERT INTO servicio_oftalmologico VALUES (1, ?, NULL, 10.00, 10, 1)",
                (nombre,),
            )
            conexion.exec_driver_sql(
                "INSERT INTO funcion VALUES (30, 'Registrar servicios realizados', 1)",
            )
            conexion.exec_driver_sql(
                "INSERT INTO rol_funcion VALUES (30, 1, 30, 1)",
            )

    alpha = crear_tenant_access_token(7, 1, 1, 1, "ALPHA")
    beta = crear_tenant_access_token(7, 1, 2, 2, "BETA")
    encabezado = lambda token: {"Authorization": f"Bearer {token}"}
    assert client.get(URL, headers=encabezado(alpha)).json()[0]["nombre"] == "Servicio Alpha"
    assert client.get(URL, headers=encabezado(beta)).json()[0]["nombre"] == "Servicio Beta"
    creado = client.post(URL, headers=encabezado(alpha), json=DATOS)
    assert creado.status_code == 201, creado.text
    with engines["ALPHA"].connect() as conexion:
        assert conexion.scalar(text("SELECT count(*) FROM servicio_oftalmologico")) == 2
    with engines["BETA"].connect() as conexion:
        assert conexion.scalar(text("SELECT count(*) FROM servicio_oftalmologico")) == 1


def test_integracion_no_duplica_modelos_rutas_ni_tablas():
    publicas = {tabla.name for tabla in Base.metadata.tables.values() if tabla.schema is None}
    assert len(publicas) == 28
    assert "servicio_oftalmologico" in publicas
    assert "servicios_oftalmologicos" not in Base.metadata.tables
    assert ServicioOftalmologico.__table__ is Base.metadata.tables["servicio_oftalmologico"]

    rutas = app.openapi()["paths"]
    operaciones = {
        (ruta, metodo)
        for ruta, definicion in rutas.items()
        if ruta.startswith("/servicios-oftalmologicos")
        for metodo in definicion
    }
    assert operaciones == {
        (URL, "get"),
        (URL, "post"),
        (f"{URL}{{servicio_id}}", "get"),
        (f"{URL}{{servicio_id}}", "put"),
    }


def test_main_conserva_cors_y_no_crea_tablas_automaticamente():
    import app.main as main_module

    assert allowed_origins == [
        "http://localhost:4201",
        "http://127.0.0.1:4201",
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        "https://clinica-oftalmologica-web.vercel.app",
    ]
    assert "create_all" not in inspect.getsource(main_module)


def test_openapi_publica_solo_el_crud_solicitado():
    rutas = app.openapi()["paths"]
    assert set(rutas[f"{URL}"]) == {"get", "post"}
    assert set(rutas[f"{URL}{{servicio_id}}"]) == {"get", "put"}
