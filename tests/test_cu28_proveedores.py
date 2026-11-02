"""CU28: pruebas aisladas con SQLite; nunca conectan a Supabase."""

from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db, get_tenant_claims
from app.modules.gestion_inventario_proveedores.api.proveedores import (
    permiso_escritura,
    permiso_lectura,
)
from app.modules.gestion_inventario_proveedores.api.router import router
from app.modules.gestion_inventario_proveedores.services import proveedor as service


def _crear_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conexion:
        conexion.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
        conexion.exec_driver_sql(
            """CREATE TABLE public.proveedores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                razon_social VARCHAR(150) NOT NULL,
                nit VARCHAR(30),
                telefono VARCHAR(20),
                correo VARCHAR(100),
                direccion VARCHAR(255),
                contacto VARCHAR(100),
                estado BOOLEAN NOT NULL DEFAULT 1
            )"""
        )
        conexion.exec_driver_sql(
            "CREATE UNIQUE INDEX public.ix_proveedores_nit "
            "ON proveedores(nit)"
        )
        conexion.exec_driver_sql(
            """CREATE TABLE bitacora (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                fecha_hora TIMESTAMP NOT NULL,
                ip TEXT,
                accion VARCHAR(100) NOT NULL,
                entidad_afectada VARCHAR(100),
                id_registro_afectado INTEGER,
                descripcion TEXT
            )"""
        )
    return engine


@contextmanager
def _cliente(engine, *, lectura=True, escritura=True):
    api = FastAPI()
    api.include_router(router)

    def fake_db():
        with Session(engine, autoflush=False) as db:
            yield db

    api.dependency_overrides[get_db] = fake_db
    api.dependency_overrides[get_tenant_claims] = lambda: {
        "tenant_id": 1,
        "empresa_id": 1,
        "empresa_codigo": "CU28-TEST",
    }
    usuario = SimpleNamespace(id=7)
    if lectura:
        api.dependency_overrides[permiso_lectura] = lambda: usuario
    else:
        api.dependency_overrides[permiso_lectura] = lambda: (_ for _ in ()).throw(
            HTTPException(403, "Sin permiso de lectura")
        )
    if escritura:
        api.dependency_overrides[permiso_escritura] = lambda: usuario
    else:
        api.dependency_overrides[permiso_escritura] = lambda: (_ for _ in ()).throw(
            HTTPException(403, "Sin permiso de escritura")
        )

    with TestClient(api, raise_server_exceptions=False) as client:
        yield client
    api.dependency_overrides.clear()


@pytest.fixture
def entorno_cu28():
    engine = _crear_engine()
    try:
        with _cliente(engine) as client:
            yield client, engine
    finally:
        engine.dispose()


def _auth():
    return {"Authorization": "Bearer token-tenant-de-prueba"}


def _crear(client, nit="123456"):
    return client.post(
        "/proveedores/",
        headers=_auth(),
        json={
            "razon_social": "  Optica Prueba SRL  ",
            "nit": nit,
            "telefono": " 70000000 ",
            "correo": "proveedor@example.com",
        },
    )


def test_cu28_crud_logico_busqueda_y_bitacora(entorno_cu28):
    client, engine = entorno_cu28
    respuesta = _crear(client)
    assert respuesta.status_code == 201, respuesta.text
    proveedor_id = respuesta.json()["proveedor"]["id"]
    assert respuesta.json()["proveedor"]["razon_social"] == "Optica Prueba SRL"

    listado = client.get(
        "/proveedores/", headers=_auth(), params={"buscar": "123"}
    )
    assert listado.status_code == 200
    assert listado.json()["total"] == 1
    assert listado.json()["items"][0]["id"] == proveedor_id

    actualizado = client.put(
        f"/proveedores/{proveedor_id}",
        headers=_auth(),
        json={"contacto": "Ana Prueba"},
    )
    assert actualizado.status_code == 200
    assert actualizado.json()["proveedor"]["contacto"] == "Ana Prueba"

    sin_confirmar = client.delete(
        f"/proveedores/{proveedor_id}", headers=_auth()
    )
    assert sin_confirmar.status_code == 400
    baja = client.delete(
        f"/proveedores/{proveedor_id}",
        headers=_auth(),
        params={"confirmar": True},
    )
    assert baja.status_code == 204
    assert client.get("/proveedores/", headers=_auth()).json()["total"] == 0
    inactivos = client.get(
        "/proveedores/", headers=_auth(), params={"estado": False}
    ).json()
    assert inactivos["total"] == 1

    with engine.connect() as conexion:
        acciones = conexion.execute(
            text("SELECT accion FROM bitacora ORDER BY id")
        ).scalars().all()
    assert acciones == [
        "CREAR_PROVEEDOR",
        "ACTUALIZAR_PROVEEDOR",
        "BAJA_PROVEEDOR",
    ]


def test_cu28_nit_duplicado_y_validaciones(entorno_cu28):
    client, _ = entorno_cu28
    assert _crear(client).status_code == 201
    duplicado = _crear(client)
    assert duplicado.status_code == 409
    assert "NIT" in duplicado.json()["detail"]
    assert client.post(
        "/proveedores/", headers=_auth(), json={"razon_social": "   "}
    ).status_code == 422
    assert client.post(
        "/proveedores/",
        headers=_auth(),
        json={"razon_social": "X", "correo": "correo-invalido"},
    ).status_code == 422
    assert client.post(
        "/proveedores/",
        headers=_auth(),
        json={"razon_social": "X", "estado": False},
    ).status_code == 422


def test_cu28_rollback_si_falla_bitacora(entorno_cu28, monkeypatch):
    client, engine = entorno_cu28

    def fallar_bitacora(**_kwargs):
        raise RuntimeError("fallo de bitacora simulado")

    monkeypatch.setattr(service, "registrar_bitacora", fallar_bitacora)
    assert _crear(client).status_code == 500
    with engine.connect() as conexion:
        assert conexion.scalar(text("SELECT count(*) FROM public.proveedores")) == 0
        assert conexion.scalar(text("SELECT count(*) FROM bitacora")) == 0


def test_cu28_exige_bearer_y_permisos_separados():
    engine = _crear_engine()
    try:
        with _cliente(engine, lectura=True, escritura=False) as client:
            assert client.get("/proveedores/").status_code == 401
            assert client.get("/proveedores/", headers=_auth()).status_code == 200
            assert _crear(client).status_code == 403
        with _cliente(engine, lectura=False, escritura=True) as client:
            assert client.get("/proveedores/", headers=_auth()).status_code == 403
            assert _crear(client).status_code == 201
    finally:
        engine.dispose()


def test_cu28_solo_admite_jwt_tenant(monkeypatch):
    from app.core.security import (
        crear_access_token,
        crear_saas_access_token,
        crear_tenant_access_token,
    )

    monkeypatch.setattr(
        service,
        "listar",
        lambda db, estado, buscar, pagina: {
            "items": [],
            "total": 0,
            "pagina": pagina,
            "tamanio": 20,
        },
    )
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: None
    api.dependency_overrides[permiso_lectura] = lambda: SimpleNamespace(id=7)

    with TestClient(api) as client:
        for token in (crear_access_token(7, 1), crear_saas_access_token(7)):
            respuesta = client.get(
                "/proveedores/",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert respuesta.status_code == 401, respuesta.text

        tenant_token = crear_tenant_access_token(7, 1, 1, 1, "CU28-TEST")
        respuesta = client.get(
            "/proveedores/",
            headers={"Authorization": f"Bearer {tenant_token}"},
        )
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json()["items"] == []


def test_cu28_aislamiento_entre_sesiones_tenant():
    engine_a = _crear_engine()
    engine_b = _crear_engine()
    try:
        with ExitStack() as pila:
            tenant_a = pila.enter_context(_cliente(engine_a))
            tenant_b = pila.enter_context(_cliente(engine_b))
            assert _crear(tenant_a, nit="TENANT-A").status_code == 201
            assert tenant_a.get(
                "/proveedores/", headers=_auth()
            ).json()["total"] == 1
            assert tenant_b.get(
                "/proveedores/", headers=_auth()
            ).json()["total"] == 0
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_cu28_openapi_sin_rutas_duplicadas():
    api = FastAPI()
    api.include_router(router)
    rutas = api.openapi()["paths"]
    assert set(rutas["/proveedores/"]) == {"get", "post"}
    assert set(rutas["/proveedores/{proveedor_id}"]) == {
        "get",
        "put",
        "delete",
    }
    identificadores = [
        operacion["operationId"]
        for metodos in rutas.values()
        for operacion in metodos.values()
    ]
    assert len(identificadores) == len(set(identificadores))
