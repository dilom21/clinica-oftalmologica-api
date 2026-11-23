"""Pruebas locales CU27: SQLite en memoria. Nunca conectan a Supabase.

Las dependencias RBAC reales (`get_tenant_claims`, `obtener_usuario_actual`,
`requerir_permiso`, `permiso_cu27_*`) se mantienen; solo se sustituye la sesión
por una base SQLite aislada con rol/función/acción sembrados.
"""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_db
from app.core.security import crear_tenant_access_token
from app.modules.gestion_inventario_proveedores.api.movimientos import router
from app.modules.gestion_inventario_proveedores.services import movimientos


CU27_FUNCION = "Gestionar movimientos de inventario"


@pytest.fixture
def entorno_cu27():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    ddl = [
        """CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL,
           descripcion TEXT, estado BOOLEAN NOT NULL, protegido BOOLEAN NOT NULL,
           fecha_creacion TIMESTAMP NOT NULL)""",
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
        """CREATE TABLE categoria (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL DEFAULT 'Pruebas',
           descripcion TEXT, estado BOOLEAN NOT NULL, fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE producto (id INTEGER PRIMARY KEY, categoria_id INTEGER NOT NULL, codigo TEXT NOT NULL DEFAULT 'P-1',
           nombre TEXT NOT NULL DEFAULT 'Producto prueba', descripcion TEXT, unidad_medida TEXT NOT NULL DEFAULT 'UNIDAD',
           stock_minimo INTEGER NOT NULL DEFAULT 0, estado BOOLEAN NOT NULL,
           fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
           fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE lote (id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL,
           numero_lote TEXT NOT NULL, fecha_fabricacion DATE, fecha_vencimiento DATE,
           observaciones TEXT, estado BOOLEAN NOT NULL,
           fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE inventario (id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id INTEGER NOT NULL UNIQUE REFERENCES lote(id),
           stock_actual INTEGER NOT NULL DEFAULT 0 CHECK(stock_actual >= 0),
           stock_reservado INTEGER NOT NULL DEFAULT 0 CHECK(stock_reservado >= 0 AND stock_reservado <= stock_actual),
           fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        """CREATE TABLE movimiento_inventario (id INTEGER PRIMARY KEY AUTOINCREMENT,
           inventario_id INTEGER NOT NULL REFERENCES inventario(id),
           usuario_id INTEGER REFERENCES usuario(id),
           tipo TEXT NOT NULL, cantidad INTEGER NOT NULL CHECK(cantidad > 0),
           stock_anterior INTEGER NOT NULL, stock_posterior INTEGER NOT NULL,
           fecha_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
           observaciones TEXT, referencia_tipo TEXT, referencia_id INTEGER)""",
    ]
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        for statement in ddl:
            conn.exec_driver_sql(statement)
        conn.exec_driver_sql(
            "INSERT INTO rol VALUES (1,'Administrador',NULL,1,0,'2026-01-01'),"
            "(2,'SoloLectura',NULL,1,0,'2026-01-01'),(3,'Profesional',NULL,1,0,'2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO usuario VALUES (7,'admin@example.test','x',1,'2026-01-01',1),"
            "(8,'lectura@example.test','x',1,'2026-01-01',2),"
            "(9,'profesional@example.test','x',1,'2026-01-01',3)"
        )
        conn.exec_driver_sql(
            "INSERT INTO modulo VALUES (11,'Inventario y Proveedores',NULL,1)"
        )
        conn.exec_driver_sql(
            "INSERT INTO funcion VALUES (14,11,?,NULL,1)", (CU27_FUNCION,)
        )
        conn.exec_driver_sql(
            "INSERT INTO accion VALUES (1,'LECTURA',NULL,1),(2,'ESCRITURA',NULL,1),(3,'AMBAS',NULL,1)"
        )
        # Administrador: AMBAS; SoloLectura: LECTURA; Profesional: sin permiso.
        conn.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES (1,1,14,3,NULL),(2,2,14,1,NULL)"
        )
        conn.execute(text("INSERT INTO categoria (id,estado) VALUES (1,1)"))
        conn.execute(text("INSERT INTO producto (id,categoria_id,estado) VALUES (1,1,1)"))
        conn.execute(text(
            "INSERT INTO lote (id,producto_id,numero_lote,estado,fecha_vencimiento) "
            "VALUES (1,1,'LOTE-1',1,'2099-01-01'), (2,1,'LOTE-2',1,'2020-01-01')"
        ))

    app = FastAPI()
    app.include_router(router)

    def fake_db():
        with Session(engine, autoflush=False) as db:
            yield db

    app.dependency_overrides[get_db] = fake_db

    try:
        with TestClient(app) as client:
            client.headers["Authorization"] = (
                f"Bearer {crear_tenant_access_token(7, 1, 1, 1, 'PRUEBA')}"
            )
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_cu27_stock_inicial_salida_e_historial(entorno_cu27):
    client, engine = entorno_cu27
    r = client.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 20,
    })
    assert r.status_code == 201, r.text
    assert r.json()["stock_anterior"] == 0
    assert r.json()["stock_posterior"] == 20
    assert r.json()["usuario_id"] == 7

    r = client.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "SALIDA_USO", "cantidad": 6,
    })
    assert r.status_code == 201, r.text
    assert r.json()["stock_anterior"] == 20
    assert r.json()["stock_posterior"] == 14

    r = client.get("/inventario-proveedores/existencias/1")
    assert r.status_code == 200
    assert r.json()["stock_disponible"] == 14
    r = client.get("/inventario-proveedores/movimientos", params={"lote_id": 1})
    assert r.status_code == 200 and len(r.json()) == 2
    assert [m["stock_posterior"] for m in r.json()] == [14, 20]


def test_cu27_no_stock_negativo_ni_consumir_reservas(entorno_cu27):
    c, engine = entorno_cu27
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "SALIDA_USO", "cantidad": 1,
    }).status_code == 409
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 10,
    }).status_code == 201
    with engine.begin() as con:
        con.execute(text("UPDATE inventario SET stock_reservado=8 WHERE lote_id=1"))
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "SALIDA_USO", "cantidad": 3,
    }).status_code == 409
    assert c.get("/inventario-proveedores/existencias/1").json()["stock_actual"] == 10
    assert len(c.get("/inventario-proveedores/movimientos").json()) == 1


def test_cu27_validaciones_y_origen_no_falsificable(entorno_cu27):
    c, _ = entorno_cu27
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "ENTRADA_COMPRA", "cantidad": 10,
    }).status_code == 422
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "AJUSTE_ENTRADA", "cantidad": 5,
    }).status_code == 422
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": -1,
    }).status_code == 422
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 999, "tipo": "STOCK_INICIAL", "cantidad": 2,
    }).status_code == 404
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 2, "usuario_id": 99,
    }).status_code == 422


def test_cu27_vencimiento_y_unicidad_stock_inicial(entorno_cu27):
    c, _ = entorno_cu27
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 2, "tipo": "STOCK_INICIAL", "cantidad": 4,
    }).status_code == 201
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 2, "tipo": "STOCK_INICIAL", "cantidad": 4,
    }).status_code == 409
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 2, "tipo": "SALIDA_USO", "cantidad": 1,
    }).status_code == 409
    assert c.post("/inventario-proveedores/movimientos", json={
        "lote_id": 2, "tipo": "SALIDA_VENCIMIENTO",
        "cantidad": 4, "observaciones": "Retiro por vencimiento",
    }).status_code == 201
    assert c.get("/inventario-proveedores/existencias/2").json()["stock_actual"] == 0


def test_cu27_automatico_usa_transaccion_del_llamador(entorno_cu27):
    _, engine = entorno_cu27
    with Session(engine) as db:
        mov = movimientos.registrar_movimiento_automatico(
            db, lote_id=1, tipo="ENTRADA_COMPRA", cantidad=5, usuario_id=7,
            referencia_tipo="DETALLE_COMPRA", referencia_id=123,
        )
        assert mov.stock_posterior == 5
        db.rollback()
    with Session(engine) as db:
        assert movimientos.consultar_existencia(db, 1).stock_actual == 0


def test_cu27_rollback_si_falla_antes_de_confirmar(entorno_cu27, monkeypatch):
    c, engine = entorno_cu27
    def fallar(*args):
        raise RuntimeError("Fallo simulado")
    monkeypatch.setattr(movimientos, "_representar", fallar)
    with pytest.raises(RuntimeError, match="Fallo simulado"):
        c.post("/inventario-proveedores/movimientos", json={
            "lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 8,
        })
    with engine.connect() as con:
        assert con.scalar(text("SELECT count(*) FROM inventario")) == 0
        assert con.scalar(text("SELECT count(*) FROM movimiento_inventario")) == 0


def test_cu27_no_duplicar_referencia_automatica(entorno_cu27):
    _, engine = entorno_cu27
    with Session(engine) as db:
        movimientos.registrar_movimiento_automatico(
            db, lote_id=1, tipo="ENTRADA_COMPRA", cantidad=5, usuario_id=7,
            referencia_tipo="DETALLE_COMPRA", referencia_id=321,
        )
        db.commit()
        with pytest.raises(HTTPException) as exc:
            movimientos.registrar_movimiento_automatico(
                db, lote_id=1, tipo="ENTRADA_COMPRA", cantidad=5, usuario_id=7,
                referencia_tipo="DETALLE_COMPRA", referencia_id=321,
            )
        assert exc.value.status_code == 409
        db.rollback()
        assert movimientos.consultar_existencia(db, 1).stock_actual == 5
