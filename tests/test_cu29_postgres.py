"""CU29/CU27 sobre migración REAL en bases locales efímeras.

Opt-in: CU29_TEST_POSTGRES=1 python -m pytest tests/test_cu29_postgres.py -q
No acepta DSN externos. Requiere postgres local y roles anon/authenticated ya
existentes. Solo elimina la base aleatoria creada por esta misma fixture.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4
import os

import psycopg
from psycopg import sql
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from app.modules.gestion_inventario_proveedores.schemas.compras import CompraCrear
from app.modules.gestion_inventario_proveedores.schemas.movimientos import MovimientoManualCrear
from app.modules.gestion_inventario_proveedores.services import compras, movimientos


pytestmark = pytest.mark.skipif(os.getenv("CU29_TEST_POSTGRES") != "1", reason="Requiere opt-in PostgreSQL local aislado")
LOCAL = "postgresql://postgres@127.0.0.1:5432/postgres"


@pytest.fixture
def pg():
    name = "test_cu29_" + uuid4().hex
    with psycopg.connect(LOCAL, autocommit=True, connect_timeout=5) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(
        f"postgresql+psycopg://postgres@127.0.0.1:5432/{name}",
        connect_args={"options": "-c lock_timeout=8000 -c statement_timeout=15000", "application_name": name},
    )
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE usuario (id BIGINT PRIMARY KEY)")
            conn.exec_driver_sql("""CREATE TABLE bitacora (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                usuario_id BIGINT REFERENCES usuario(id), fecha_hora TIMESTAMPTZ NOT NULL,
                ip VARCHAR(45), accion VARCHAR(100), entidad_afectada VARCHAR(100),
                id_registro_afectado BIGINT, descripcion TEXT)""")
            migration = Path("database/tenant_migrations/versions/20261024150000_cu26_cu27_cu29.sql").read_text(encoding="utf-8")
            conn.exec_driver_sql(migration)
            conn.exec_driver_sql("INSERT INTO usuario VALUES (7)")
            conn.exec_driver_sql("INSERT INTO proveedores (razon_social,estado) VALUES ('Prueba A',true),('Prueba B',true)")
            conn.exec_driver_sql("INSERT INTO categoria (nombre) VALUES ('Insumos')")
            conn.exec_driver_sql("INSERT INTO producto (categoria_id,codigo,nombre) VALUES (1,'P1','Gotas'),(1,'P2','Gasas')")
            conn.exec_driver_sql("INSERT INTO lote (producto_id,numero_lote) VALUES (1,'L1'),(2,'L2')")
            conn.exec_driver_sql("INSERT INTO inventario (lote_id,stock_actual,stock_reservado) VALUES (1,10,2),(2,10,2)")
        yield engine, name
    finally:
        engine.dispose()
        with psycopg.connect(LOCAL, autocommit=True, connect_timeout=5) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def compra(db, proveedor=1, *, nuevo=False, invertir=False, documento=None):
    detalles = [
        {"producto_id": 1, **({"numero_lote": nuevo if isinstance(nuevo, str) else "NUEVO"} if nuevo else {"lote_id": 1}), "cantidad": 3, "costo_unitario": "1.25"},
        {"producto_id": 2, "lote_id": 2, "cantidad": 2, "costo_unitario": "0.10"},
    ]
    if invertir:
        detalles.reverse()
    db.info["tenant_context"] = SimpleNamespace(empresa_id=1)
    return compras.crear(db, CompraCrear(proveedor_id=proveedor, numero_documento=documento, detalles=detalles), SimpleNamespace(id=7))


def concurrentes(pg, segunda, *, nueva=False):
    engine, name = pg
    retenida, liberar, iniciada = Event(), Event(), Event()

    def primera():
        with Session(engine) as db:
            def retener(_):
                retenida.set()
                assert liberar.wait(10), "No se liberó la primera transacción"
            event.listen(db, "before_commit", retener, once=True)
            return compra(db, nuevo=nueva)

    def otra():
        with Session(engine) as db:
            iniciada.set()
            return segunda(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        uno = pool.submit(primera)
        try:
            assert retenida.wait(10)
            dos = pool.submit(otra)
            assert iniciada.wait(5)
            # Evidencia real de espera en PostgreSQL, no inferencia desde SQLite.
            bloqueada = False
            with psycopg.connect(LOCAL, autocommit=True) as observer:
                limite = monotonic() + 5
                while monotonic() < limite:
                    bloqueada = observer.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND cardinality(pg_blocking_pids(pid))>0)", (name,),
                    ).fetchone()[0]
                    if bloqueada:
                        break
                    sleep(0.02)
            assert bloqueada, "La segunda transacción debe esperar el bloqueo real"
        finally:
            liberar.set()
        return uno.result(timeout=10), dos.result(timeout=10)


def test_compras_concurrentes_orden_inverso(pg):
    concurrentes(pg, lambda db: compra(db, 2, invertir=True))
    with pg[0].connect() as conn:
        assert conn.execute(text("SELECT stock_actual,stock_reservado FROM inventario ORDER BY lote_id")).all() == [(16, 2), (14, 2)]
        assert conn.execute(text("SELECT count(*) FROM compra")).scalar() == 2
        assert conn.execute(text("SELECT count(*) FROM movimiento_inventario")).scalar() == 4
        assert conn.execute(text("SELECT count(*) FROM bitacora")).scalar() == 2
        assert conn.execute(text("SELECT count(*) FROM movimiento_inventario m JOIN detalle_compra d ON d.id=m.referencia_id WHERE m.referencia_tipo='DETALLE_COMPRA' AND m.tipo='ENTRADA_COMPRA' AND d.cantidad=m.cantidad")).scalar() == 4


def test_compra_concurrente_con_salida_cu27(pg):
    concurrentes(pg, lambda db: movimientos.registrar_manual(
        db, MovimientoManualCrear(lote_id=1, tipo="SALIDA_USO", cantidad=4), 7,
    ))
    with pg[0].connect() as conn:
        assert conn.execute(text("SELECT stock_actual,stock_reservado FROM inventario WHERE lote_id=1")).one() == (9, 2)
        assert conn.execute(text("SELECT stock_anterior,stock_posterior FROM movimiento_inventario WHERE tipo='SALIDA_USO'")).one() == (13, 9)


def test_creacion_concurrente_mismo_lote_sin_inventario(pg):
    concurrentes(pg, lambda db: compra(db, 2, nuevo=True), nueva=True)
    with pg[0].connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM lote WHERE numero_lote='NUEVO'")).scalar() == 1
        assert conn.execute(text("SELECT stock_actual,stock_reservado FROM inventario i JOIN lote l ON l.id=i.lote_id WHERE l.numero_lote='NUEVO'")).one() == (6, 0)
        assert conn.execute(text("SELECT count(*) FROM movimiento_inventario")).scalar() == 4


def test_postgres_lote_nuevo_documento_duplicado_y_rollback(pg, monkeypatch):
    engine, _ = pg
    with Session(engine) as db:
        resultado = compra(db, nuevo=True, documento="F-1")
        assert resultado.compra.total == Decimal("3.95")
        with pytest.raises(HTTPException) as error:
            compra(db, nuevo=True, documento="F-1")
        assert error.value.status_code == 409
    original = compras.registrar_movimiento_automatico
    llamadas = 0

    def fallar(*args, **kwargs):
        nonlocal llamadas
        llamadas += 1
        if llamadas == 2:
            raise RuntimeError("Fallo ficticio")
        return original(*args, **kwargs)

    monkeypatch.setattr(compras, "registrar_movimiento_automatico", fallar)
    with engine.connect() as conn:
        antes = {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY id")).all() for t in ("compra", "detalle_compra", "lote", "inventario", "movimiento_inventario", "bitacora")}
    with Session(engine) as db, pytest.raises(RuntimeError):
        compra(db, 2, nuevo="REVERTIR")
    with engine.connect() as conn:
        despues = {t: conn.execute(text(f"SELECT * FROM {t} ORDER BY id")).all() for t in antes}
    assert despues == antes


def test_desbordamiento_postgres_revierte_hasta_lote_nuevo(pg):
    engine, _ = pg
    with engine.begin() as conn:
        conn.execute(text("UPDATE inventario SET stock_actual=2147483647 WHERE lote_id=2"))
    with Session(engine) as db, pytest.raises(HTTPException) as error:
        compra(db, nuevo="NO_PERSISTIR")
    assert error.value.status_code == 409
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM compra")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM detalle_compra")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM movimiento_inventario")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM bitacora")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM lote WHERE numero_lote='NO_PERSISTIR'")).scalar() == 0
        assert conn.execute(text("SELECT stock_actual,stock_reservado FROM inventario WHERE lote_id=1")).one() == (10, 2)
