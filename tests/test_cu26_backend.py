"""Pruebas aisladas CU26: SQLite en memoria, jamás Supabase.
Se ejecutan con pytest. El conftest existente bloquea conexión compartida.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.dependencies import obtener_administrador_actual
from app.database.session import get_db
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
    api.dependency_overrides[obtener_administrador_actual] = lambda: type('Admin', (), {'id': 7})()
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
