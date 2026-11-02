"""CU30 against seven isolated SQLite tenants; never connects to PostgreSQL."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import time
from app.core.config import JWT_ALGORITHM, JWT_SECRET_KEY
from app.core.security import crear_access_token, crear_saas_access_token, crear_tenant_access_token
from app.core.tenancy.dependencies import get_tenant_engine_registry
from app.core.tenancy.models import Empresa, PlanSaas, Suscripcion, TenantDatabase
from app.database import session as clinical_session
from app.main import app
from app.modules.gestion_inventario_proveedores.services import inventario as service
from app.modules.gestion_inventario_proveedores.repositories.inventario import _fuente_no_disponible


EMPRESAS = (
    "VISION-CLARA", "OFTALMO-NORTE", "VISUAL-ORIENTAL", "INSTITUTO-VISION",
    "OFTALMOCARE", "VISTA-SUR", "MEDICO-OCULAR",
)
HOY = date(2026, 10, 26)
BASE = "/inventario-proveedores"
LISTADO = BASE + "/inventario"
ALERTAS = BASE + "/alertas"


def _engine():
    return create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                         connect_args={"check_same_thread": False})


def _crear_tenant(engine, codigo, numero):
    # DDL and fixtures exist solely inside this disposable test database.
    with engine.begin() as db:
        esquemas = {fila[1] for fila in db.exec_driver_sql("PRAGMA database_list")}
        if "public" not in esquemas:
            db.exec_driver_sql("ATTACH DATABASE ':memory:' AS public")
        for ddl in (
            "CREATE TABLE rol (id INTEGER PRIMARY KEY, nombre TEXT, descripcion TEXT, "
            "estado BOOLEAN, protegido BOOLEAN, fecha_creacion TIMESTAMP)",
            "CREATE TABLE usuario (id INTEGER PRIMARY KEY, correo TEXT, password_hash TEXT, "
            "estado BOOLEAN, fecha_creacion TIMESTAMP, rol_id INTEGER)",
            "CREATE TABLE modulo (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN)",
            "CREATE TABLE funcion (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN, modulo_id INTEGER)",
            "CREATE TABLE accion (id INTEGER PRIMARY KEY, nombre TEXT, estado BOOLEAN)",
            "CREATE TABLE rol_funcion (id INTEGER PRIMARY KEY, rol_id INTEGER, funcion_id INTEGER, accion_id INTEGER)",
            "CREATE TABLE public.categoria (id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, estado BOOLEAN)",
            "CREATE TABLE public.producto (id INTEGER PRIMARY KEY, categoria_id INTEGER NOT NULL, "
            "codigo TEXT NOT NULL, nombre TEXT NOT NULL, stock_minimo INTEGER NOT NULL CHECK(stock_minimo >= 0), "
            "estado BOOLEAN NOT NULL)",
            "CREATE TABLE public.lote (id INTEGER PRIMARY KEY, producto_id INTEGER NOT NULL, "
            "numero_lote TEXT NOT NULL, fecha_vencimiento DATE, estado BOOLEAN NOT NULL)",
            "CREATE TABLE public.inventario (id INTEGER PRIMARY KEY, lote_id INTEGER NOT NULL UNIQUE, "
            "stock_actual INTEGER NOT NULL CHECK(stock_actual >= 0), "
            "stock_reservado INTEGER NOT NULL CHECK(stock_reservado BETWEEN 0 AND stock_actual))",
        ):
            db.exec_driver_sql(ddl)
        db.exec_driver_sql("INSERT INTO rol VALUES (1, 'Administrador', NULL, 1, 0, '2026-01-01')")
        db.exec_driver_sql("INSERT INTO usuario VALUES (7, ?, 'unused', 1, '2026-01-01', 1)",
                           (codigo.lower() + "@example.test",))
        db.exec_driver_sql("INSERT INTO modulo VALUES (4, 'Inventario y Proveedores', 1)")
        db.exec_driver_sql("INSERT INTO funcion VALUES (30, 'Consultar inventario y alertas', 1, 4)")
        db.exec_driver_sql("INSERT INTO accion VALUES (1,'LECTURA',1),(2,'ESCRITURA',1),(3,'AMBAS',1)")
        db.exec_driver_sql("INSERT INTO rol_funcion VALUES (1,1,30,1)")
        db.exec_driver_sql("INSERT INTO public.categoria VALUES (1,'Medicamentos',1),(2,'Insumos',0)")
        productos = [
            (1, 1, "GC-1", "Gotas " + codigo, 10, 1),
            (2, 1, "AG-1", "Agotado " + codigo, 5, 1),
            (3, 2, "NO-1", "Normal " + codigo, 2, 1),
            (4, 2, "SM-1", "Sin minimo positivo " + codigo, 0, 1),
            (5, 1, "LI-1", "Literal%_ " + codigo, 1, 1),
            (6, 1, "IN-1", "Inactivo " + codigo, 1000, 0),
            (7, 2, "SL-1", "Sin lotes " + codigo, 1, 1),
            (100 + numero, 2, "EX-1", "Exclusivo " + codigo, 1, 1),
        ]
        db.exec_driver_sql("INSERT INTO public.producto VALUES (?,?,?,?,?,?)", productos)
        lotes = [
            (1, 1, codigo + "-HOY", HOY.isoformat(), 1),
            (2, 1, codigo + "-LIMITE", (HOY + timedelta(days=30)).isoformat(), 1),
            (3, 1, codigo + "-DESPUES", (HOY + timedelta(days=31)).isoformat(), 1),
            (4, 1, codigo + "-VENCIDO", (HOY - timedelta(days=1)).isoformat(), 1),
            (5, 1, codigo + "-SIN-FECHA", None, 1),
            (6, 2, codigo + "-VACIO", (HOY - timedelta(days=1)).isoformat(), 1),
            (7, 3, codigo + "-NORMAL", None, 1),
            (8, 4, codigo + "-UMBRAL", (HOY - timedelta(days=1)).isoformat(), 1),
            (9, 5, codigo + "-%_", None, 1),
            (10, 6, codigo + "-PRODUCTO-INACTIVO", HOY.isoformat(), 1),
            (11, 100 + numero, codigo + "-SIN-EXISTENCIA", None, 1),
            (99, 1, codigo + "-LOTE-INACTIVO", HOY.isoformat(), 0),
        ]
        db.exec_driver_sql("INSERT INTO public.lote VALUES (?,?,?,?,?)", lotes)
        existencias = [(1, 2, 1), (2, 3, 0), (3, 1, 0), (4, 4, 1), (5, 0, 0),
                       (6, 0, 0), (7, 12 + numero, 5), (8, 3, 0), (9, 5, 0),
                       (10, 1000, 0), (99, 500, 0)]
        db.exec_driver_sql("INSERT INTO public.inventario VALUES (?,?,?,?)",
                           [(lote, lote, cantidad, reserva) for lote, cantidad, reserva in existencias])


@pytest.fixture
def tenants(monkeypatch):
    monkeypatch.setattr(time, "fecha_local_aplicacion", lambda: HOY)
    monkeypatch.setattr(service, "fecha_local_aplicacion", lambda: HOY)
    control = _engine()

    @event.listens_for(control, "connect")
    def _control_schema(connection, _):
        connection.execute("ATTACH DATABASE ':memory:' AS saas_control")

    with control.begin() as db:
        for model in (Empresa, PlanSaas, Suscripcion, TenantDatabase):
            model.__table__.create(db)
    with Session(control) as db:
        db.add(PlanSaas(id=1, codigo="PRO", estado=True))
        for numero, codigo in enumerate(EMPRESAS, 1):
            db.add_all([
                Empresa(id=numero, codigo=codigo, slug=codigo.lower(), estado="ACTIVA"),
                Suscripcion(id=numero, empresa_id=numero, plan_id=1, estado="ACTIVA",
                             fecha_inicio=date(2026, 1, 1), fecha_fin=date(2026, 12, 31)),
                TenantDatabase(id=numero, empresa_id=numero,
                               database_name="tenant_" + codigo.lower().replace("-", "_"), estado="ACTIVA"),
            ])
        db.commit()

    engines = {codigo: _engine() for codigo in EMPRESAS}
    sentencias = []
    for numero, (codigo, engine) in enumerate(engines.items(), 1):
        _crear_tenant(engine, codigo, numero)

        @event.listens_for(engine, "before_cursor_execute")
        def _registrar(_connection, _cursor, statement, _parameters, _context, _many):
            sentencias.append(statement)

    class Registry:
        calls = []

        def get_session(self, context):
            self.calls.append(context.empresa_codigo)
            return Session(engines[context.empresa_codigo], autoflush=False)

    registry = Registry()
    monkeypatch.setattr(clinical_session, "open_control_session", lambda: Session(control))

    def _legacy_prohibido():
        pytest.fail("CU30 must never open the legacy database")

    monkeypatch.setattr(clinical_session, "SessionLocal", _legacy_prohibido)
    anteriores = app.dependency_overrides.copy()
    app.dependency_overrides[get_tenant_engine_registry] = lambda: registry
    try:
        with TestClient(app) as client:
            yield client, engines, control, registry, sentencias
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anteriores)
        for engine in (*engines.values(), control):
            engine.dispose()


def _headers(codigo=EMPRESAS[0]):
    numero = EMPRESAS.index(codigo) + 1
    return {"Authorization": "Bearer " + crear_tenant_access_token(7, 1, numero, numero, codigo)}


def _get(tenants, path=LISTADO, params=None, codigo=EMPRESAS[0]):
    response = tenants[0].get(path, params=params, headers=_headers(codigo))
    assert response.status_code == 200, response.text
    return response.json()


def test_consulta_real_suma_stock_fisico_excluye_inactivos_y_no_escribe(tenants):
    datos = _get(tenants)
    assert datos["fecha_corte"] == HOY.isoformat()
    assert datos["total"] == 7
    assert datos["resumen"] == {
        "productos": 7, "stock_bajo": 1, "agotados": 3,
        "lotes_proximos": 2, "lotes_vencidos": 2,
    }
    gotas = next(p for p in datos["items"] if p["id"] == 1)
    assert gotas["stock_actual"] == 10  # Includes reserved stock; excludes inactive lot 99.
    assert isinstance(gotas["stock_actual"], (int, float))
    assert gotas["estado_stock"] == "BAJO"  # Equality with the minimum is low stock.
    assert {l["id"] for l in gotas["lotes"]} == {1, 2, 3, 4, 5}
    assert {p["id"] for p in datos["items"]}.isdisjoint({6})
    normal = next(p for p in datos["items"] if p["id"] == 3)
    assert normal["categoria"] == "Insumos"  # Inactive category does not hide active stock.
    assert normal["stock_actual"] == 13
    sin_existencia = next(p for p in datos["items"] if p["id"] == 101)
    assert sin_existencia["stock_actual"] == 0
    assert sin_existencia["lotes"][0]["existencia"] == 0
    assert tenants[3].calls == [EMPRESAS[0]]  # One shared tenant session for auth, permission, query.
    assert all(s.lstrip().upper().startswith("SELECT") for s in tenants[4])


@pytest.mark.parametrize("params,ids", [
    ({"q": "  gotas  "}, {1}),
    ({"q": "gc-1"}, {1}),
    ({"q": "limite"}, {1}),
    ({"q": "%_"}, {5}),
    ({"q": "' OR 1=1 --"}, set()),
    ({"categoria": "  medicamentos "}, {1, 2, 5}),
    ({"stock": "BAJO"}, {1}),
    ({"stock": "AGOTADO"}, {2, 7, 101}),
    ({"stock": "NORMAL"}, {3, 4, 5}),
    ({"stock": "SIN_UMBRAL"}, set()),
    ({"vencimiento": "PROXIMO"}, {1}),
    ({"vencimiento": "VENCIDO"}, {1, 2, 4}),
    ({"vencimiento": "SIN_FECHA"}, {1, 3, 5, 101}),
    ({"categoria": "Medicamentos", "stock": "BAJO", "q": "gc-1"}, {1}),
])
def test_busqueda_y_filtros(tenants, params, ids):
    datos = _get(tenants, params=params)
    assert {p["id"] for p in datos["items"]} == ids
    assert datos["total"] == len(ids)
    assert datos["resumen"]["productos"] == len(ids)


def test_paginacion_estable_resumen_previo_y_sin_resultados(tenants):
    primera = _get(tenants, params={"page_size": 2})
    segunda = _get(tenants, params={"page_size": 2, "page": 2})
    assert [p["id"] for p in primera["items"]] == [2, 101]
    assert [p["id"] for p in segunda["items"]] == [1, 5]
    assert primera["total_pages"] == 4
    assert primera["resumen"] == segunda["resumen"]
    fuera = _get(tenants, params={"page": 99})
    assert fuera["items"] == [] and fuera["total"] == 7
    vacia = _get(tenants, params={"q": "sin coincidencia"})
    assert vacia["items"] == [] and vacia["total"] == 0 and vacia["total_pages"] == 0
    assert vacia["resumen"] == {
        "productos": 0, "stock_bajo": 0, "agotados": 0,
        "lotes_proximos": 0, "lotes_vencidos": 0,
    }


def test_lotes_limites_vencimiento_y_ventana_configurable(tenants):
    datos = _get(tenants, BASE + "/productos/1/lotes")
    estados = {l["id"]: l["estado_vencimiento"] for l in datos["lotes"]}
    assert estados == {1: "PROXIMO", 2: "PROXIMO", 3: "VIGENTE", 4: "VENCIDO", 5: "SIN_FECHA"}
    datos = _get(tenants, BASE + "/productos/1/lotes", {"dias_vencimiento": 31})
    assert next(l for l in datos["lotes"] if l["id"] == 3)["estado_vencimiento"] == "PROXIMO"
    sin_lotes = _get(tenants, BASE + "/productos/7/lotes")
    assert sin_lotes["lotes"] == []
    filtrados = _get(tenants, params={"vencimiento": "PROXIMO"})
    assert {l["id"] for l in filtrados["items"][0]["lotes"]} == {1, 2}
    assert filtrados["items"][0]["stock_actual"] == 10  # A lot filter does not redefine product stock.
    assert filtrados["resumen"]["lotes_vencidos"] == 0


def test_alertas_no_duplican_agotado_y_no_alertan_lotes_vacios(tenants):
    datos = _get(tenants, ALERTAS)
    tipos = [a["tipo"] for a in datos["items"]]
    assert tipos.count("STOCK_BAJO") == 1
    assert tipos.count("AGOTADO") == 3
    assert tipos.count("PROXIMO_VENCIMIENTO") == 2
    assert tipos.count("VENCIDO") == 2
    assert datos["total"] == 8
    assert not any(a["lote_id"] in {5, 6, 10, 99} for a in datos["items"])
    assert all(a["existencia"] > 0 for a in datos["items"] if a["lote_id"] is not None)
    agotados = _get(tenants, ALERTAS, {"stock": "AGOTADO", "page_size": 2})
    assert agotados["total"] == 3 and agotados["total_pages"] == 2
    assert all(a["tipo"] == "AGOTADO" for a in agotados["items"])


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page_size": 0}, {"page_size": 101}, {"dias_vencimiento": 0},
    {"dias_vencimiento": 366}, {"stock": "INVALIDO"}, {"vencimiento": "INVALIDO"},
    {"q": "x" * 121}, {"categoria": "x" * 121},
])
@pytest.mark.parametrize("path", [LISTADO, ALERTAS])
def test_filtros_invalidos_422(tenants, params, path):
    response = tenants[0].get(path, params=params, headers=_headers())
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("path", [BASE + "/productos/0/lotes", BASE + "/productos/no/lotes"])
def test_id_invalido_422(tenants, path):
    assert tenants[0].get(path, headers=_headers()).status_code == 422


@pytest.mark.parametrize("producto_id", [6, 999, 102])
def test_producto_inactivo_inexistente_o_de_otra_empresa_404(tenants, producto_id):
    response = tenants[0].get(BASE + f"/productos/{producto_id}/lotes", headers=_headers())
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("sql,status", [
    ("UPDATE rol_funcion SET accion_id=3", 200),
    ("UPDATE rol_funcion SET accion_id=2", 403),
    ("DELETE FROM rol_funcion", 403),
    ("DELETE FROM funcion", 403),
    ("UPDATE funcion SET estado=0", 403),
    ("UPDATE modulo SET estado=0", 403),
    ("UPDATE rol SET estado=0", 403),
    ("UPDATE modulo SET nombre='Otro modulo'", 403),
    ("UPDATE accion SET estado=0 WHERE id=1", 403),
    ("UPDATE funcion SET nombre='Gestionar movimientos de inventario'", 403),
    ("UPDATE usuario SET estado=0", 401),
])
@pytest.mark.parametrize("path", [LISTADO, ALERTAS, BASE + "/productos/1/lotes"])
def test_permisos_reales_y_usuario_activo(tenants, sql, status, path):
    with tenants[1][EMPRESAS[0]].begin() as db:
        db.exec_driver_sql(sql)
    response = tenants[0].get(path, headers=_headers())
    assert response.status_code == status, response.text


def test_funcion_con_el_mismo_nombre_en_otro_modulo_no_autoriza(tenants):
    with tenants[1][EMPRESAS[0]].begin() as db:
        db.exec_driver_sql("INSERT INTO modulo VALUES (5, 'Otro modulo', 1)")
        db.exec_driver_sql("INSERT INTO funcion VALUES (31, 'Consultar inventario y alertas', 1, 5)")
        db.exec_driver_sql("UPDATE rol_funcion SET funcion_id=31")
    for path in (LISTADO, ALERTAS, BASE + "/productos/1/lotes"):
        response = tenants[0].get(path, headers=_headers())
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("kind", ["ausente", "legacy", "saas", "firma", "empresa", "tenant", "rol", "expirado"])
def test_rechaza_tokens_sin_contexto_seguro_sin_fallback_legacy(tenants, kind):
    claims = {"sub": "7", "rol_id": 1, "tenant_id": 1, "empresa_id": 1,
              "empresa_codigo": EMPRESAS[0], "token_type": "tenant",
              "exp": datetime.now(timezone.utc) + timedelta(minutes=30)}
    if kind == "ausente":
        headers = {}
    elif kind == "legacy":
        headers = {"Authorization": "Bearer " + crear_access_token(7, 1)}
    elif kind == "saas":
        headers = {"Authorization": "Bearer " + crear_saas_access_token(7)}
    elif kind == "firma":
        headers = {"Authorization": "Bearer " + jwt.encode(claims, "wrong-secret-that-is-at-least-32-bytes", algorithm=JWT_ALGORITHM)}
    else:
        if kind == "empresa":
            claims["empresa_id"] = 2
        elif kind == "tenant":
            claims["tenant_id"] = 2
        elif kind == "rol":
            claims["rol_id"] = 2
        else:
            claims["exp"] = datetime.now(timezone.utc) - timedelta(minutes=1)
        headers = {"Authorization": "Bearer " + jwt.encode(claims, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)}
    for path in (LISTADO, ALERTAS, BASE + "/productos/1/lotes"):
        response = tenants[0].get(path, headers=headers)
        assert response.status_code in {401, 403}, response.text


def test_aislamiento_siete_empresas_ids_repetidos_y_navegacion_alternada(tenants):
    for numero, codigo in enumerate(EMPRESAS, 1):
        datos = _get(tenants, codigo=codigo)
        assert all(codigo in p["nombre"] for p in datos["items"])
        assert next(p for p in datos["items"] if p["id"] == 3)["stock_actual"] == 12 + numero
        assert any(p["id"] == 100 + numero for p in datos["items"])
        lotes = _get(tenants, BASE + "/productos/1/lotes", codigo=codigo)
        assert all(l["codigo"].startswith(codigo) for l in lotes["lotes"])
        alertas = _get(tenants, ALERTAS, codigo=codigo)
        assert all(codigo in a["producto_nombre"] for a in alertas["items"])
    assert tenants[3].calls == [codigo for codigo in EMPRESAS for _ in range(3)]
    # Visiting an earlier tenant again cannot return the most recently queried tenant.
    assert all(EMPRESAS[0] in p["nombre"] for p in _get(tenants)["items"])


def test_empresa_en_query_o_header_no_sustituye_jwt(tenants):
    response = tenants[0].get(LISTADO, params={"empresa_codigo": EMPRESAS[1], "tenant_id": 2},
                              headers={**_headers(), "X-Tenant-Id": "2", "X-Empresa-Codigo": EMPRESAS[1]})
    assert response.status_code == 422, response.text
    response = tenants[0].get(LISTADO, headers={**_headers(), "X-Tenant-Id": "2", "X-Empresa-Codigo": EMPRESAS[1]})
    assert response.status_code == 200, response.text
    assert all(EMPRESAS[0] in p["nombre"] for p in response.json()["items"])
    assert EMPRESAS[1] not in tenants[3].calls


def test_suspension_y_fallo_conexion_no_leen_otro_tenant(tenants, monkeypatch):
    _get(tenants)
    with Session(tenants[2]) as db:
        db.get(Empresa, 1).estado = "SUSPENDIDA"
        db.commit()
    assert tenants[0].get(LISTADO, headers=_headers()).status_code in {401, 503}
    assert tenants[3].calls == [EMPRESAS[0]]
    monkeypatch.setattr(tenants[3], "get_session", lambda _context: (_ for _ in ()).throw(RuntimeError("secret-db")))
    response = tenants[0].get(LISTADO, headers=_headers(EMPRESAS[1]))
    assert response.status_code == 503
    assert "secret-db" not in response.text


def test_esquema_faltante_error_controlado_sin_datos_ficticios(tenants):
    with tenants[1][EMPRESAS[0]].begin() as db:
        db.exec_driver_sql("DROP TABLE public.inventario")
    for path in (LISTADO, ALERTAS, BASE + "/productos/1/lotes"):
        response = tenants[0].get(path, headers=_headers())
        assert response.status_code == 503, response.text
        assert "items" not in response.json()
        assert "SELECT " not in response.text and "no such table" not in response.text
    assert _get(tenants, codigo=EMPRESAS[1])["total"] == 7


def test_proyeccion_futura_sin_umbral_no_asume_cero():
    # Existing physical stock_minimo is NOT NULL; this defensive branch belongs
    # to the read projection seam, not to fabricated database fixtures.
    assert service.estado_stock(Decimal(5), None) == "SIN_UMBRAL"
    assert service.estado_stock(Decimal(0), None) == "AGOTADO"


@pytest.mark.parametrize("sqlstate", ["42P01", "42703"])
def test_esquema_postgresql_ausente_detalla_bloqueo_sin_exponer_sql(sqlstate):
    class ErrorServidor(Exception):
        pass

    original = ErrorServidor("sensitive-host-and-sql")
    original.sqlstate = sqlstate
    error = _fuente_no_disponible(ProgrammingError("sensitive-sql", {}, original))
    assert error.status_code == 503
    assert error.detail["codigo"] == "INVENTARIO_DEPENDENCIAS_PENDIENTES"
    assert error.detail["dependencias"] == ["CU26", "CU27", "CU28", "CU29"]
    assert "sensitive" not in str(error.detail)
