"""Pruebas aisladas CU26: SQLite en memoria, jamás Supabase.
Se ejecutan con pytest. El conftest existente bloquea conexión compartida.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.dependencies import (
    get_db,
    get_tenant_claims,
    obtener_administrador_actual,
    obtener_usuario_actual,
)
from app.modules.gestion_inventario_proveedores.api.router import router


@pytest.fixture
def cliente_cu26():
    engine = create_engine('sqlite+pysqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    ddl = [
        '''CREATE TABLE categoria (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre VARCHAR(100) NOT NULL, descripcion TEXT, estado BOOLEAN NOT NULL DEFAULT 1, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
        '''CREATE UNIQUE INDEX uq_categoria_nombre_normalizado ON categoria(lower(trim(nombre)))''',
        '''CREATE TABLE producto (id INTEGER PRIMARY KEY AUTOINCREMENT, categoria_id INTEGER NOT NULL REFERENCES categoria(id), codigo VARCHAR(50) NOT NULL UNIQUE, nombre VARCHAR(160) NOT NULL, descripcion TEXT, unidad_medida VARCHAR(30) NOT NULL DEFAULT 'UNIDAD', stock_minimo INTEGER NOT NULL DEFAULT 0, estado BOOLEAN NOT NULL DEFAULT 1, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
        '''CREATE TABLE lote (id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL REFERENCES producto(id), numero_lote VARCHAR(80) NOT NULL, fecha_fabricacion DATE, fecha_vencimiento DATE, observaciones TEXT, estado BOOLEAN NOT NULL DEFAULT 1, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(producto_id, numero_lote))''',
        '''CREATE TABLE inventario (id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id INTEGER UNIQUE NOT NULL, stock_actual INTEGER NOT NULL DEFAULT 0, stock_reservado INTEGER NOT NULL DEFAULT 0)'''
    ]
    with engine.begin() as conn:
        for statement in ddl:
            conn.exec_driver_sql(statement)
    api = FastAPI()
    api.include_router(router)
    def fake_db():
        with Session(engine, autoflush=False) as db:
            yield db
    api.dependency_overrides[get_db] = fake_db
    api.dependency_overrides[get_tenant_claims] = lambda: {
        "sub": "7",
        "rol_id": 1,
        "tenant_id": 1,
        "empresa_id": 1,
        "empresa_codigo": "PRUEBA",
        "token_type": "tenant",
    }
    api.dependency_overrides[obtener_administrador_actual] = lambda: type('Admin', (), {'id': 7})()
    api.state.cu26_engine = engine
    try:
        with TestClient(api) as client:
            yield client
    finally:
        api.dependency_overrides.clear()
        engine.dispose()


def test_cu26_flujo_completo(cliente_cu26):
    c = cliente_cu26
    r = c.post('/inventario-proveedores/categorias', json={'nombre': '  Lentes  ', 'descripcion': 'Insumos ópticos'})
    assert r.status_code == 201, r.text
    categoria_id = r.json()['id']
    assert r.json()['nombre'] == 'Lentes'
    r = c.post('/inventario-proveedores/productos', json={'categoria_id': categoria_id, 'codigo': 'LENT-001', 'nombre': 'Lente intraocular', 'stock_minimo': 5})
    assert r.status_code == 201, r.text
    producto_id = r.json()['id']
    r = c.post('/inventario-proveedores/lotes', json={'producto_id': producto_id, 'numero_lote': 'LOT-A', 'fecha_fabricacion': '2026-01-01', 'fecha_vencimiento': '2028-01-01'})
    assert r.status_code == 201, r.text
    lote_id = r.json()['id']
    assert len(c.get('/inventario-proveedores/categorias').json()) == 1
    assert len(c.get('/inventario-proveedores/productos', params={'categoria_id': categoria_id}).json()) == 1
    assert len(c.get('/inventario-proveedores/lotes', params={'producto_id': producto_id}).json()) == 1
    assert c.get(f'/inventario-proveedores/lotes/{lote_id}').json()['numero_lote'] == 'LOT-A'
    assert c.patch(f'/inventario-proveedores/categorias/{categoria_id}/estado', json={'estado': False}).status_code == 409
    assert c.patch(f'/inventario-proveedores/productos/{producto_id}/estado', json={'estado': False}).status_code == 409
    assert c.patch(f'/inventario-proveedores/lotes/{lote_id}/estado', json={'estado': False}).status_code == 200
    assert c.patch(f'/inventario-proveedores/productos/{producto_id}/estado', json={'estado': False}).status_code == 200
    assert c.patch(f'/inventario-proveedores/categorias/{categoria_id}/estado', json={'estado': False}).status_code == 200


def test_cu26_validaciones(cliente_cu26):
    c = cliente_cu26
    assert c.post('/inventario-proveedores/categorias', json={'nombre': '   '}).status_code == 422
    assert c.post('/inventario-proveedores/productos', json={'categoria_id': 999, 'codigo': 'X', 'nombre': 'X'}).status_code == 404
    assert c.get('/inventario-proveedores/categorias/999').status_code == 404
    assert c.post('/inventario-proveedores/categorias', json={'nombre': 'Equipo'}).status_code == 201
    assert c.post('/inventario-proveedores/categorias', json={'nombre': 'equipo'}).status_code == 409
    assert c.post('/inventario-proveedores/lotes', json={'producto_id': 1, 'numero_lote': 'TEST', 'fecha_fabricacion': '2026-10-25', 'fecha_vencimiento': '2026-10-24'}).status_code == 422


def test_cu26_no_autorizado_sin_token():
    # No usamos los overrides de administrador en esta prueba.
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: None  # Sin acceso a bases en prueba de autenticación
    with TestClient(api) as c:
        r = c.get('/inventario-proveedores/categorias')
        assert r.status_code in (401, 403)




def test_cu26_solo_admite_jwt_tenant(monkeypatch):
    from app.core.security import (
        crear_access_token,
        crear_saas_access_token,
        crear_tenant_access_token,
    )
    from app.modules.gestion_inventario_proveedores.services import (
        catalogo as catalogo_service,
    )

    # Simulacion sin conexiones a bases reales.
    monkeypatch.setattr(
        catalogo_service,
        "listar_categorias",
        lambda db, incluir_inactivos: [],
    )

    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: None
    api.dependency_overrides[obtener_administrador_actual] = (
        lambda: object()
    )

    ruta = "/inventario-proveedores/categorias"

    with TestClient(api) as cliente:
        for token in (
            crear_access_token(7, 1),
            crear_saas_access_token(7),
            "not-a-jwt",
        ):
            respuesta = cliente.get(
                ruta,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert respuesta.status_code == 401, respuesta.text

        token_tenant = crear_tenant_access_token(
            7, 1, 1, 1, "PRUEBA"
        )
        respuesta = cliente.get(
            ruta,
            headers={"Authorization": f"Bearer {token_tenant}"},
        )

        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.json() == []


def test_cu26_rechaza_token_tenant_expirado():
    token = jwt.encode(
        {
            "sub": "7",
            "rol_id": 1,
            "tenant_id": 17,
            "empresa_id": 4,
            "empresa_codigo": "VISION-CLARA",
            "token_type": "tenant",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: None

    with TestClient(api) as client:
        respuesta = client.get(
            "/inventario-proveedores/categorias",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert respuesta.status_code == 401


def test_cu26_rechaza_rol_no_administrador_sin_anular_guard_tenant():
    from app.core.security import crear_tenant_access_token

    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: None
    api.dependency_overrides[obtener_usuario_actual] = lambda: SimpleNamespace(
        rol=SimpleNamespace(nombre="Profesional")
    )
    token = crear_tenant_access_token(7, 2, 17, 4, "VISION-CLARA")

    with TestClient(api) as client:
        respuesta = client.get(
            "/inventario-proveedores/categorias",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert respuesta.status_code == 403


def test_cu26_unidad_inmutable_con_lotes_y_baja_bloqueada_por_inventario(
    cliente_cu26,
):
    cliente = cliente_cu26
    categoria = cliente.post(
        "/inventario-proveedores/categorias",
        json={"nombre": "Óptica"},
    ).json()
    producto = cliente.post(
        "/inventario-proveedores/productos",
        json={
            "categoria_id": categoria["id"],
            "codigo": "OPT-001",
            "nombre": "Producto",
        },
    ).json()

    cambio_previo = cliente.put(
        f'/inventario-proveedores/productos/{producto["id"]}',
        json={
            "categoria_id": categoria["id"],
            "codigo": "OPT-001",
            "nombre": "Producto",
            "unidad_medida": "CAJA",
            "stock_minimo": 0,
        },
    )
    assert cambio_previo.status_code == 200, cambio_previo.text

    lote = cliente.post(
        "/inventario-proveedores/lotes",
        json={"producto_id": producto["id"], "numero_lote": "LOT-1"},
    ).json()
    cambio_con_lote = cliente.put(
        f'/inventario-proveedores/productos/{producto["id"]}',
        json={
            "categoria_id": categoria["id"],
            "codigo": "OPT-001",
            "nombre": "Producto",
            "unidad_medida": "PZA",
            "stock_minimo": 0,
        },
    )
    assert cambio_con_lote.status_code == 409

    with cliente.app.state.cu26_engine.begin() as conexion:
        conexion.exec_driver_sql(
            "INSERT INTO inventario "
            "(lote_id, stock_actual, stock_reservado) VALUES (?, ?, ?)",
            (lote["id"], 2, 2),
        )
    baja = cliente.patch(
        f'/inventario-proveedores/lotes/{lote["id"]}/estado',
        json={"estado": False},
    )
    assert baja.status_code == 409

