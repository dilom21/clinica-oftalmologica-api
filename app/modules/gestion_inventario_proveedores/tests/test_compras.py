"""Pruebas del CU29 en SQLite en memoria; nunca escriben en Supabase.

Ejecutar: python -m pytest app/modules/gestion_inventario_proveedores/tests -q
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import jwt
import psycopg
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.dependencies import get_db, get_tenant_claims
from app.core.time import fecha_hora_utc, fecha_local_aplicacion
from app.modules.gestion_inventario_proveedores.api.router import router
from app.modules.gestion_inventario_proveedores.models.compras import Inventario
from app.modules.gestion_inventario_proveedores.repositories import compras as repo
from app.modules.gestion_inventario_proveedores.schemas.compras import CompraCrear, MAX_INT
from app.modules.gestion_inventario_proveedores.services import compras as service


# DDL exclusivo de bases efímeras SQLite. Tipos/relaciones y restricciones
# relevantes reproducen las tablas inspeccionadas; no usa Base.create_all.
DDL = [
    """CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT,
        estado BOOLEAN, protegido BOOLEAN, fecha_creacion TIMESTAMP)""",
    """CREATE TABLE usuario (id INTEGER PRIMARY KEY, correo TEXT, password_hash TEXT,
        estado BOOLEAN, fecha_creacion TIMESTAMP, rol_id INTEGER REFERENCES rol(id))""",
    "CREATE TABLE funcion (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN)",
    "CREATE TABLE accion (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN)",
    """CREATE TABLE rol_funcion (id INTEGER PRIMARY KEY, rol_id INTEGER,
        funcion_id INTEGER, accion_id INTEGER)""",
    """CREATE TABLE proveedores (id INTEGER PRIMARY KEY, razon_social TEXT NOT NULL,
        nit TEXT, telefono TEXT, correo TEXT, direccion TEXT, contacto TEXT, estado BOOLEAN)""",
    """CREATE TABLE producto (id INTEGER PRIMARY KEY, codigo TEXT NOT NULL,
        nombre TEXT NOT NULL, unidad_medida TEXT NOT NULL, estado BOOLEAN NOT NULL)""",
    """CREATE TABLE lote (id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL REFERENCES producto(id),
        numero_lote TEXT NOT NULL, fecha_fabricacion DATE, fecha_vencimiento DATE,
        observaciones TEXT, estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL,
        UNIQUE(producto_id, numero_lote), CHECK(fecha_vencimiento >= fecha_fabricacion))""",
    """CREATE TABLE inventario (id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL UNIQUE REFERENCES lote(id),
        stock_actual INTEGER NOT NULL CHECK(stock_actual >= 0), stock_reservado INTEGER NOT NULL,
        fecha_actualizacion TIMESTAMP NOT NULL,
        CHECK(stock_reservado >= 0 AND stock_reservado <= stock_actual))""",
    """CREATE TABLE compra (id INTEGER PRIMARY KEY, proveedor_id INTEGER NOT NULL REFERENCES proveedores(id),
        usuario_id INTEGER NOT NULL REFERENCES usuario(id), numero_documento TEXT,
        fecha_compra TIMESTAMP NOT NULL, fecha_recepcion TIMESTAMP, moneda TEXT NOT NULL,
        estado TEXT NOT NULL, observaciones TEXT, fecha_creacion TIMESTAMP NOT NULL,
        fecha_actualizacion TIMESTAMP NOT NULL, UNIQUE(proveedor_id, numero_documento),
        CHECK(fecha_recepcion >= fecha_compra),
        CHECK((estado = 'RECIBIDA' AND fecha_recepcion IS NOT NULL)
            OR (estado != 'RECIBIDA' AND fecha_recepcion IS NULL)))""",
    """CREATE TABLE detalle_compra (id INTEGER PRIMARY KEY,
        compra_id INTEGER NOT NULL REFERENCES compra(id), lote_id INTEGER NOT NULL REFERENCES lote(id),
        cantidad INTEGER NOT NULL CHECK(cantidad > 0), costo_unitario NUMERIC NOT NULL CHECK(costo_unitario >= 0),
        observaciones TEXT, UNIQUE(compra_id, lote_id))""",
    """CREATE TABLE movimiento_inventario (id INTEGER PRIMARY KEY,
        inventario_id INTEGER NOT NULL REFERENCES inventario(id), usuario_id INTEGER REFERENCES usuario(id),
        tipo TEXT NOT NULL, cantidad INTEGER NOT NULL CHECK(cantidad > 0),
        stock_anterior INTEGER NOT NULL, stock_posterior INTEGER NOT NULL,
        fecha_hora TIMESTAMP NOT NULL, observaciones TEXT, referencia_tipo TEXT, referencia_id INTEGER,
        CHECK(stock_posterior = stock_anterior + cantidad), UNIQUE(referencia_tipo, referencia_id))""",
    """CREATE TABLE bitacora (id INTEGER PRIMARY KEY, usuario_id INTEGER REFERENCES usuario(id),
        fecha_hora TIMESTAMP NOT NULL, ip TEXT, accion TEXT NOT NULL,
        entidad_afectada TEXT, id_registro_afectado INTEGER, descripcion TEXT)""",
]


@pytest.fixture(autouse=True)
def prohibir_postgres(monkeypatch):
    def rechazar(*args, **kwargs):
        pytest.fail("Esta prueba no puede conectarse a PostgreSQL/Supabase")
    monkeypatch.setattr(psycopg, "connect", rechazar)


def crear_base(empresa_id):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"public": None}},
    )
    hoy = fecha_local_aplicacion()
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for ddl in DDL:
            conn.exec_driver_sql(ddl)
        conn.exec_driver_sql("INSERT INTO rol VALUES (1, 'Administrador', NULL, 1, 0, '2026-01-01'), (2, 'Lector', NULL, 1, 0, '2026-01-01'), (3, 'Sin permiso', NULL, 1, 0, '2026-01-01')")
        conn.exec_driver_sql("INSERT INTO usuario VALUES (7, 'a@test.invalid', 'x', 1, '2026-01-01', 1), (8, 'b@test.invalid', 'x', 1, '2026-01-01', 2), (9, 'c@test.invalid', 'x', 1, '2026-01-01', 3)")
        conn.exec_driver_sql("INSERT INTO funcion VALUES (1, 'Registrar compras a proveedor', 1), (2, 'Gestionar proveedores', 1)")
        conn.exec_driver_sql("INSERT INTO accion VALUES (1, 'AMBAS', 1), (2, 'LECTURA', 1)")
        conn.exec_driver_sql("INSERT INTO rol_funcion VALUES (1, 1, 1, 1), (2, 2, 1, 2), (3, 1, 2, 1)")
        conn.exec_driver_sql("INSERT INTO proveedores (id, razon_social, estado) VALUES (1, ?, 1), (2, 'Inactivo', 0), (3, 'Nulo', NULL)", (f"Proveedor empresa {empresa_id}",))
        conn.exec_driver_sql("INSERT INTO producto VALUES (1, 'P1', 'Gotas', 'UNIDAD', 1), (2, 'P2', 'Gasas', 'UNIDAD', 1), (3, 'P3', 'Inactivo', 'UNIDAD', 0)")
        conn.exec_driver_sql("INSERT INTO lote VALUES (1, 1, 'EXISTENTE', ?, ?, NULL, 1, '2026-01-01'), (2, 2, 'OTRO', NULL, NULL, NULL, 1, '2026-01-01'), (3, 1, 'VENCIDO', NULL, ?, NULL, 1, '2026-01-01'), (4, 1, 'INACTIVO', NULL, NULL, NULL, 0, '2026-01-01')", ((hoy - timedelta(days=30)).isoformat(), (hoy + timedelta(days=30)).isoformat(), (hoy - timedelta(days=1)).isoformat()))
        conn.exec_driver_sql("INSERT INTO inventario VALUES (1, 1, 10, 2, '2026-01-01')")
    return engine


@pytest.fixture
def bases():
    engines = {1: crear_base(1), 2: crear_base(2)}
    try:
        yield engines
    finally:
        for engine in engines.values():
            engine.dispose()


def token(empresa=1, usuario=7, **cambios):
    payload = {
        "sub": str(usuario), "rol_id": {7: 1, 8: 2, 9: 3}[usuario],
        "tenant_id": empresa, "empresa_id": empresa, "empresa_codigo": f"E{empresa}",
        "token_type": "tenant", "exp": fecha_hora_utc() + timedelta(minutes=5),
    }
    payload.update(cambios)
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def cabecera(empresa=1, usuario=7, **cambios):
    return {"Authorization": f"Bearer {token(empresa, usuario, **cambios)}"}


@pytest.fixture
def cliente(bases):
    app = FastAPI()
    app.include_router(router)

    def db_local(claims=Depends(get_tenant_claims)):
        with Session(bases[claims["empresa_id"]], autoflush=False) as db:
            db.info["tenant_context"] = SimpleNamespace(empresa_id=claims["empresa_id"])
            yield db

    app.dependency_overrides[get_db] = db_local
    with TestClient(app) as client:
        client.headers.update(cabecera())
        yield client


def entrada(**cambios):
    datos = {"proveedor_id": 1, "detalles": [
        {"producto_id": 1, "lote_id": 1, "cantidad": 3, "costo_unitario": "0.10"},
    ]}
    datos.update(cambios)
    return datos


def estado(engine):
    with engine.connect() as conn:
        return {
            tabla: conn.exec_driver_sql(f"SELECT * FROM {tabla} ORDER BY id").all()
            for tabla in ("compra", "detalle_compra", "lote", "inventario", "movimiento_inventario", "bitacora")
        }


def test_registro_completo_y_consulta(cliente, bases):
    datos = entrada(numero_documento=" FAC-001 ")
    datos["detalles"].append({"producto_id": 2, "numero_lote": "NUEVO", "cantidad": 2, "costo_unitario": "1.25"})
    respuesta = cliente.post("/compras/", json=datos)
    assert respuesta.status_code == 201, respuesta.text
    compra = respuesta.json()["compra"]
    assert compra["empresa_id"] == 1 and compra["usuario_id"] == 7
    assert compra["estado"] == "RECIBIDA" and compra["numero_documento"] == "FAC-001"
    assert Decimal(compra["total"]) == Decimal("2.80")
    assert [Decimal(d["subtotal"]) for d in compra["detalles"]] == [Decimal("0.30"), Decimal("2.50")]
    assert compra["detalles"][0]["fecha_vencimiento"] is not None
    with bases[1].connect() as conn:
        assert conn.exec_driver_sql("SELECT stock_actual, stock_reservado FROM inventario WHERE lote_id=1").one() == (13, 2)
        movimientos = conn.exec_driver_sql("SELECT tipo, stock_anterior, stock_posterior, referencia_tipo, referencia_id FROM movimiento_inventario ORDER BY id").all()
        assert movimientos == [
            ("ENTRADA_COMPRA", 10, 13, "DETALLE_COMPRA", compra["detalles"][0]["id"]),
            ("ENTRADA_COMPRA", 0, 2, "DETALLE_COMPRA", compra["detalles"][1]["id"]),
        ]
        assert conn.exec_driver_sql("SELECT usuario_id, accion FROM bitacora").one() == (7, "REGISTRAR_COMPRA")
    consultada = cliente.get(f"/compras/{compra['id']}").json()
    # SQLite pierde tzinfo al almacenar TIMESTAMP; PostgreSQL usa timestamptz.
    for campo in ("fecha_compra", "fecha_recepcion"):
        assert datetime.fromisoformat(consultada[campo]).replace(tzinfo=None) == (
            datetime.fromisoformat(compra[campo]).replace(tzinfo=None)
        )
        consultada[campo] = compra[campo]
    assert consultada == compra
    historial = cliente.get("/compras/").json()
    assert historial["total"] == 1 and Decimal(historial["items"][0]["total"]) == Decimal("2.80")


@pytest.mark.parametrize("campo,valor", [
    ("cantidad", 0), ("cantidad", -1), ("cantidad", 1.5), ("cantidad", True),
    ("cantidad", MAX_INT + 1), ("costo_unitario", "-0.01"),
    ("costo_unitario", "1.001"), ("costo_unitario", "NaN"),
    ("costo_unitario", "Infinity"), ("costo_unitario", "10000000000.00"),
    ("producto_id", True), ("lote_id", 0), ("observaciones", "\u0000"),
])
def test_rechaza_detalles_invalidos(cliente, bases, campo, valor):
    antes = estado(bases[1])
    datos = entrada()
    datos["detalles"][0][campo] = valor
    assert cliente.post("/compras/", json=datos).status_code == 422
    assert estado(bases[1]) == antes


@pytest.mark.parametrize("campo,valor", [
    ("empresa_id", 2), ("tenant_id", 2), ("usuario_id", 8), ("total", 0),
    ("estado", "RECIBIDA"), ("proveedor_id", None), ("detalles", []),
])
def test_rechaza_identidad_importes_y_vacios(cliente, campo, valor):
    assert cliente.post("/compras/", json=entrada(**{campo: valor})).status_code == 422


@pytest.mark.parametrize("cambio,codigo", [
    ({"proveedor_id": 99}, 404), ({"proveedor_id": 2}, 409), ({"proveedor_id": 3}, 409),
    ({"producto_id": 99}, 404), ({"producto_id": 3}, 409),
    ({"lote_id": 99}, 404), ({"lote_id": 2}, 404),
    ({"lote_id": 3}, 422), ({"lote_id": 4}, 409),
    ({"fecha_vencimiento": None}, 409),
])
def test_rechaza_referencias_y_restaura_todo(cliente, bases, cambio, codigo):
    datos = entrada()
    if "proveedor_id" in cambio:
        datos.update(cambio)
    else:
        datos["detalles"][0].update(cambio)
    antes = estado(bases[1])
    respuesta = cliente.post("/compras/", json=datos)
    assert respuesta.status_code == codigo, respuesta.text
    assert estado(bases[1]) == antes


def test_documento_duplicado_no_duplica_stock(cliente, bases):
    datos = entrada(numero_documento="F-001")
    assert cliente.post("/compras/", json=datos).status_code == 201
    antes = estado(bases[1])
    assert cliente.post("/compras/", json=datos).status_code == 409
    assert estado(bases[1]) == antes


def test_mismo_lote_por_id_y_numero_revierte(cliente, bases):
    datos = entrada()
    datos["detalles"].append({"producto_id": 1, "numero_lote": "EXISTENTE", "cantidad": 1, "costo_unitario": "1.00"})
    antes = estado(bases[1])
    assert cliente.post("/compras/", json=datos).status_code == 422
    assert estado(bases[1]) == antes


@pytest.mark.parametrize("punto_fallo", ["movimiento_inventario", "bitacora", "commit"])
def test_fallo_intermedio_revierte_incluso_lotes_nuevos(cliente, bases, monkeypatch, punto_fallo):
    antes = estado(bases[1])
    datos = entrada()
    datos["detalles"].append({"producto_id": 2, "numero_lote": "NUEVO", "cantidad": 2, "costo_unitario": "1.00"})
    contador = 0

    def fallar_sql(conn, cursor, sentencia, parametros, contexto, muchos):
        nonlocal contador
        if sentencia.lstrip().startswith("INSERT INTO") and punto_fallo in sentencia:
            contador += 1
            if punto_fallo == "bitacora" or contador == 2:
                raise OperationalError("fallo local simulado", {}, Exception("fallo"))

    def fallar_commit(db):
        raise OperationalError("fallo local antes de commit", {}, Exception("fallo"))

    if punto_fallo == "commit":
        monkeypatch.setattr(Session, "commit", fallar_commit)
    else:
        event.listen(bases[1], "before_cursor_execute", fallar_sql)
    try:
        respuesta = cliente.post("/compras/", json=datos)
        assert respuesta.status_code == 500, respuesta.text
    finally:
        if punto_fallo != "commit":
            event.remove(bases[1], "before_cursor_execute", fallar_sql)
    assert estado(bases[1]) == antes


def test_stock_limite_no_se_desborda(cliente, bases):
    with bases[1].begin() as conn:
        conn.exec_driver_sql("UPDATE inventario SET stock_actual=?", (MAX_INT,))
    antes = estado(bases[1])
    assert cliente.post("/compras/", json=entrada()).status_code == 422
    assert estado(bases[1]) == antes


def test_costo_cero_y_reutilizacion_del_lote(cliente):
    datos = entrada(detalles=[{"producto_id": 1, "numero_lote": "EXISTENTE", "cantidad": 1, "costo_unitario": "0"}])
    respuesta = cliente.post("/compras/", json=datos)
    assert respuesta.status_code == 201, respuesta.text
    assert Decimal(respuesta.json()["compra"]["total"]) == 0
    assert respuesta.json()["compra"]["detalles"][0]["lote_id"] == 1


@pytest.mark.parametrize("fechas", [
    {"fecha_vencimiento": -1},
    {"fecha_fabricacion": 1},
    {"fecha_fabricacion": 2, "fecha_vencimiento": 1},
])
def test_fechas_invalidas_de_lote_nuevo(cliente, bases, fechas):
    hoy = fecha_local_aplicacion()
    detalle = {"producto_id": 1, "numero_lote": "NUEVO", "cantidad": 1, "costo_unitario": "1"}
    detalle.update({campo: (hoy + timedelta(days=dias)).isoformat() for campo, dias in fechas.items()})
    antes = estado(bases[1])
    assert cliente.post("/compras/", json=entrada(detalles=[detalle])).status_code == 422
    assert estado(bases[1]) == antes


@pytest.mark.parametrize("modo", ["sin_lote", "dos_referencias", "duplicado"])
def test_lote_obligatorio_y_unico(cliente, modo):
    datos = entrada()
    if modo == "sin_lote":
        datos["detalles"][0].pop("lote_id")
    elif modo == "dos_referencias":
        datos["detalles"][0]["numero_lote"] = "EXISTENTE"
    else:
        datos["detalles"].append(datos["detalles"][0].copy())
    assert cliente.post("/compras/", json=datos).status_code == 422


def test_catalogos_y_router_de_proveedores(cliente):
    assert [p["id"] for p in cliente.get("/compras/proveedores").json()["items"]] == [1]
    assert {p["id"] for p in cliente.get("/compras/productos").json()["items"]} == {1, 2}
    assert [l["id"] for l in cliente.get("/compras/productos/1/lotes").json()["items"]] == [1]
    assert cliente.get("/proveedores/").status_code == 200
    assert cliente.get("/compras/productos?buscar=%25").json()["total"] == 0


def test_aislamiento_de_empresas_con_ids_coincidentes(cliente, bases):
    respuesta = cliente.post("/compras/", json=entrada())
    compra_id = respuesta.json()["compra"]["id"]
    assert cliente.get(f"/compras/{compra_id}", headers=cabecera(2)).status_code == 404
    assert cliente.get("/compras/", headers=cabecera(2)).json()["total"] == 0
    assert cliente.get("/compras/proveedores", headers=cabecera(2)).json()["items"][0]["razon_social"] == "Proveedor empresa 2"
    with bases[1].begin() as conn:
        conn.exec_driver_sql("INSERT INTO producto VALUES (50, 'EXCLUSIVO', 'Solo empresa 1', 'UNIDAD', 1)")
    datos = entrada(detalles=[{"producto_id": 50, "numero_lote": "N", "cantidad": 1, "costo_unitario": "1"}])
    assert cliente.post("/compras/", json=datos, headers=cabecera(2)).status_code == 404
    with bases[2].connect() as conn:
        assert conn.exec_driver_sql("SELECT stock_actual FROM inventario WHERE id=1").scalar() == 10


def test_autenticacion_y_permisos(cliente):
    cliente.headers.pop("Authorization")
    assert cliente.get("/compras/").status_code == 401
    assert cliente.post("/compras/", json=entrada(), headers={"Authorization": "Bearer invalido"}).status_code == 401
    for tipo in (None, "saas_admin"):
        assert cliente.get("/compras/", headers=cabecera(token_type=tipo)).status_code == 401
    assert cliente.get("/compras/", headers=cabecera(exp=0)).status_code == 401
    assert cliente.get("/compras/", headers=cabecera(usuario=9)).status_code == 403
    assert cliente.get("/compras/", headers=cabecera(usuario=8)).status_code == 200
    assert cliente.post("/compras/", json=entrada(), headers=cabecera(usuario=8)).status_code == 403


def test_sin_contexto_no_opera(bases):
    with Session(bases[1]) as db:
        with pytest.raises(HTTPException) as error:
            service.crear(db, CompraCrear(**entrada()), SimpleNamespace(id=7))
        assert error.value.status_code == 401


def test_bloqueo_postgres_refresca_stock_en_sesion(bases):
    with Session(bases[1]) as db:
        inventario = db.scalar(select(Inventario).where(Inventario.id == 1))
        db.execute(text("UPDATE inventario SET stock_actual=20 WHERE id=1"))
        assert repo.bloquear_inventario(db, 1).stock_actual == 20
        assert inventario.stock_actual == 20

    class Capturar:
        def scalar(self, consulta):
            self.consulta = consulta

    captura = Capturar()
    for bloquear, identificador in (
        (repo.bloquear_proveedor, 1), (repo.bloquear_producto, 1),
        (repo.bloquear_inventario, 1),
    ):
        bloquear(captura, identificador)
        assert "FOR UPDATE" in str(captura.consulta.compile(dialect=postgresql.dialect()))
        assert captura.consulta.get_execution_options()["populate_existing"]
