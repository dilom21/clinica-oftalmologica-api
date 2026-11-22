"""Pruebas aisladas CU26: SQLite en memoria, jamás Supabase.
Se ejecutan con pytest. El conftest existente bloquea conexión compartida.

Las dependencias RBAC reales (`get_tenant_claims`, `obtener_usuario_actual`,
`requerir_permiso` y `permiso_cu26_lectura/escritura`) NO se sustituyen: solo se
reemplaza la sesión por una base SQLite aislada con rol/función/acción sembrados.
"""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.dependencies import get_db
from app.core.security import crear_tenant_access_token
from app.modules.gestion_inventario_proveedores.api.router import router


CU26_FUNCION = "Gestionar productos, categorías y lotes"

RBAC_DDL = [
    """CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, descripcion TEXT,
       estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL)""",
    """CREATE TABLE usuario (id INTEGER PRIMARY KEY, correo TEXT NOT NULL,
       password_hash TEXT NOT NULL, estado BOOLEAN NOT NULL,
       fecha_creacion TIMESTAMP NOT NULL, rol_id INTEGER NOT NULL REFERENCES rol(id))""",
    """CREATE TABLE modulo (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
       descripcion TEXT, estado BOOLEAN NOT NULL)""",
    """CREATE TABLE funcion (id INTEGER PRIMARY KEY, modulo_id INTEGER NOT NULL REFERENCES modulo(id),
       nombre TEXT NOT NULL, descripcion TEXT, estado BOOLEAN NOT NULL)""",
    """CREATE TABLE accion (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
       descripcion TEXT, estado BOOLEAN NOT NULL)""",
    """CREATE TABLE rol_funcion (id INTEGER PRIMARY KEY,
       rol_id INTEGER NOT NULL REFERENCES rol(id),
       funcion_id INTEGER NOT NULL REFERENCES funcion(id),
       accion_id INTEGER NOT NULL REFERENCES accion(id), descripcion TEXT)""",
]

RBAC_SEED = [
    "INSERT INTO rol VALUES (1,'Administrador',NULL,1,0,'2026-01-01'),"
    "(2,'SoloLectura',NULL,1,0,'2026-01-01'),(3,'Profesional',NULL,1,0,'2026-01-01')",
    "INSERT INTO usuario VALUES (7,'admin@example.test','x',1,'2026-01-01',1),"
    "(8,'lectura@example.test','x',1,'2026-01-01',2),"
    "(9,'profesional@example.test','x',1,'2026-01-01',3)",
    "INSERT INTO modulo VALUES (11,'Inventario y Proveedores',NULL,1)",
    f"INSERT INTO funcion VALUES (13,11,'{CU26_FUNCION}',NULL,1)",
    "INSERT INTO accion VALUES (1,'LECTURA',NULL,1),(2,'ESCRITURA',NULL,1),(3,'AMBAS',NULL,1)",
    # Administrador: AMBAS (lectura + escritura); SoloLectura: LECTURA;
    # Profesional: sin rol_funcion (debe recibir 403 real).
    "INSERT INTO rol_funcion VALUES (1,1,13,3,NULL),(2,2,13,1,NULL)",
]


def token_cu26(usuario_id: int = 7, rol_id: int = 1) -> str:
    return crear_tenant_access_token(usuario_id, rol_id, 1, 1, "PRUEBA")


def construir_base(con_inventario: bool = True):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ddl = list(RBAC_DDL)
    if con_inventario:
        ddl += [
            '''CREATE TABLE categoria (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre VARCHAR(100) NOT NULL, descripcion TEXT, estado BOOLEAN NOT NULL DEFAULT 1, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE UNIQUE INDEX uq_categoria_nombre_normalizado ON categoria(lower(trim(nombre)))''',
            '''CREATE TABLE producto (id INTEGER PRIMARY KEY AUTOINCREMENT, categoria_id INTEGER NOT NULL REFERENCES categoria(id), codigo VARCHAR(50) NOT NULL UNIQUE, nombre VARCHAR(160) NOT NULL, descripcion TEXT, unidad_medida VARCHAR(30) NOT NULL DEFAULT 'UNIDAD', stock_minimo INTEGER NOT NULL DEFAULT 0, estado BOOLEAN NOT NULL DEFAULT 1, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE TABLE lote (id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL REFERENCES producto(id), numero_lote VARCHAR(80) NOT NULL, fecha_fabricacion DATE, fecha_vencimiento DATE, observaciones TEXT, estado BOOLEAN NOT NULL DEFAULT 1, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(producto_id, numero_lote))''',
            '''CREATE TABLE inventario (id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id INTEGER UNIQUE NOT NULL, stock_actual INTEGER NOT NULL DEFAULT 0, stock_reservado INTEGER NOT NULL DEFAULT 0)''',
        ]
    with engine.begin() as conn:
        for statement in ddl:
            conn.exec_driver_sql(statement)
        for statement in RBAC_SEED:
            conn.exec_driver_sql(statement)
    return engine


def cliente_con_base(engine, *, usuario_id: int = 7, rol_id: int = 1):
    api = FastAPI()
    api.include_router(router)

    def fake_db():
        with Session(engine, autoflush=False) as db:
            yield db

    api.dependency_overrides[get_db] = fake_db
    client = TestClient(api)
    client.headers["Authorization"] = f"Bearer {token_cu26(usuario_id, rol_id)}"
    return client


@pytest.fixture
def cliente_cu26():
    engine = construir_base()
    client = cliente_con_base(engine)
    client.app.state.cu26_engine = engine
    try:
        with client:
            yield client
    finally:
        client.app.dependency_overrides.clear()
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
    # Sin Authorization, el guard tenant rechaza antes de abrir cualquier sesión.
    api = FastAPI()
    api.include_router(router)

    def sin_sesion():  # pragma: no cover - debe ser inalcanzable
        pytest.fail("get_db no debe ejecutarse sin token tenant válido")

    api.dependency_overrides[get_db] = sin_sesion
    with TestClient(api) as c:
        r = c.get('/inventario-proveedores/categorias')
        assert r.status_code in (401, 403)




def test_cu26_solo_admite_jwt_tenant():
    from app.core.security import (
        crear_access_token,
        crear_saas_access_token,
    )

    engine = construir_base()
    api = FastAPI()
    api.include_router(router)

    def fake_db():
        with Session(engine, autoflush=False) as db:
            yield db

    api.dependency_overrides[get_db] = fake_db

    ruta = "/inventario-proveedores/categorias"

    try:
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

            # La dependencia RBAC real se ejecuta: rol 1 (Administrador) tiene
            # AMBAS sobre CU26 y la lista de categorías está vacía.
            assert respuesta.status_code == 200, respuesta.text
            assert respuesta.json() == []
    finally:
        api.dependency_overrides.clear()
        engine.dispose()


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

    def sin_sesion():  # pragma: no cover - debe ser inalcanzable
        pytest.fail("get_db no debe ejecutarse con un token expirado")

    api.dependency_overrides[get_db] = sin_sesion

    with TestClient(api) as client:
        respuesta = client.get(
            "/inventario-proveedores/categorias",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert respuesta.status_code == 401


def test_cu26_rechaza_rol_no_administrador_sin_anular_guard_tenant():
    """Rol sin `rol_funcion` recibe 403 de la dependencia real, no de un stub."""
    engine = construir_base()
    try:
        cliente = cliente_con_base(engine, usuario_id=9, rol_id=3)
        with cliente as client:
            respuesta = client.get("/inventario-proveedores/categorias")

        assert respuesta.status_code == 403, respuesta.text
        assert CU26_FUNCION in respuesta.text

        # La misma petición con el rol autorizado demuestra que el 403 proviene
        # de `requerir_permiso` y no de un override de prueba.
        cliente_autorizado = cliente_con_base(engine, usuario_id=7, rol_id=1)
        with cliente_autorizado as client:
            permitida = client.get("/inventario-proveedores/categorias")
        assert permitida.status_code == 200, permitida.text
    finally:
        engine.dispose()


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
