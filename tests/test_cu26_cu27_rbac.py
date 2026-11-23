from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.dependencies import ACCION_AMBAS, ACCION_LECTURA, get_db, get_tenant_claims, requerir_permiso
from app.core.security import crear_access_token, crear_saas_access_token, crear_tenant_access_token
from app.modules.gestion_inventario_proveedores.api import catalogo, movimientos
from app.modules.gestion_inventario_proveedores.api.catalogo import router as catalogo_router
from app.modules.gestion_inventario_proveedores.api.movimientos import router as movimientos_router
from scripts.saas.preflight_rbac_cu26_cu27 import (
    EXPECTED_SCHEMA,
    PreflightError,
    preflight,
    validate_control_rows,
    validate_tenant_rows,
)


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "database" / "tenant_rbac" / "20261102_seed_cu26_cu27.sql"


def test_seed_is_postgresql_transactional_and_contains_exact_contract():
    sql = SEED.read_text(encoding="utf-8")
    assert sql.startswith("--") and "BEGIN;" in sql and "COMMIT;" in sql
    assert "Gestionar productos, categorías y lotes" in sql
    assert "Gestionar movimientos de inventario" in sql
    assert "Inventario y Proveedores" in sql
    assert "GENERATED" not in sql
    assert "WHERE modulo_id = v_modulo_id" in sql
    assert "WHERE nombre = 'Administrador'" in sql
    assert "WHERE nombre = 'AMBAS'" in sql


def test_seed_is_fail_closed_and_does_not_update_unrelated_permissions():
    sql = SEED.read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in sql
    assert "UPDATE public.rol_funcion" not in sql
    assert "DELETE FROM" not in sql
    assert "INSERT INTO public.rol_funcion" in sql
    assert "accion_id = v_accion_id" in sql


def test_seed_blocks_global_target_function_homonyms_before_resolution():
    sql = SEED.read_text(encoding="utf-8")
    guard = "WHERE nombre = v_nombre AND modulo_id <> v_modulo_id"
    assert guard in sql
    assert sql.index("RAISE EXCEPTION 'Global function homonym") > sql.index(guard)


def test_fake_seed_is_idempotent_and_rolls_back_on_ambiguity():
    state = {
        "modules": [{"id": 11, "name": "Inventario y Proveedores"}],
        "actions": [{"id": 3, "name": "AMBAS", "active": True}],
        "roles": [{"id": 7, "name": "Administrador", "active": True}],
        "functions": [],
        "assignments": [],
    }

    def apply():
        original = {key: [dict(row) for row in value] for key, value in state.items()}
        try:
            module = [r for r in state["modules"] if r["name"] == "Inventario y Proveedores"]
            if len(module) != 1:
                raise PreflightError("ambiguous module")
            action = [r for r in state["actions"] if r["name"] == "AMBAS"]
            role = [r for r in state["roles"] if r["name"] == "Administrador"]
            if len(action) != 1 or len(role) != 1:
                raise PreflightError("ambiguous global reference")
            for name in ("Gestionar productos, categorías y lotes", "Gestionar movimientos de inventario"):
                found = [r for r in state["functions"] if r["module_id"] == module[0]["id"] and r["name"] == name]
                if not found:
                    function = {"id": len(state["functions"]) + 20, "module_id": module[0]["id"], "name": name}
                    state["functions"].append(function)
                    state["assignments"].append({"role_id": role[0]["id"], "function_id": function["id"], "action_id": action[0]["id"]})
                elif len(found) != 1:
                    raise PreflightError("ambiguous function")
        except Exception:
            state.clear()
            state.update(original)
            raise

    apply()
    first = {key: [dict(row) for row in value] for key, value in state.items()}
    apply()
    assert state == first
    state["modules"].append({"id": 12, "name": "Inventario y Proveedores"})
    before = {key: [dict(row) for row in value] for key, value in state.items()}
    with pytest.raises(PreflightError):
        apply()
    assert state == before


def rows():
    return [(i, f"EMPRESA-{i}", "ACTIVA", 100 + i, f"tenant_empresa_{i}", "ACTIVA", EXPECTED_SCHEMA) for i in range(1, 8)]


def test_control_plane_requires_seven_unique_active_versioned_tenants():
    assert validate_control_rows(rows())["companies"] == 7
    with pytest.raises(PreflightError, match="expected 7"):
        validate_control_rows(rows()[:-1])
    duplicate = rows()
    duplicate[1] = (2, duplicate[0][1], *duplicate[1][2:])
    with pytest.raises(PreflightError, match="duplicate active company code"):
        validate_control_rows(duplicate)
    missing_version = rows()
    missing_version[0] = (*missing_version[0][:6], None)
    with pytest.raises(PreflightError, match="missing"):
        validate_control_rows(missing_version)


def test_preflight_only_executes_select_query():
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = rows()
    assert preflight(connection)["valid"] is True
    statement = connection.cursor.return_value.__enter__.return_value.execute.call_args.args[0]
    assert statement.lstrip().startswith("SELECT")
    assert "INSERT" not in statement and "UPDATE" not in statement and "DELETE" not in statement


def test_preflight_validates_each_tenant_and_returns_pending_when_targets_are_absent():
    connection = MagicMock()
    control_cursor = connection.cursor.return_value.__enter__.return_value
    control_cursor.fetchall.return_value = rows()
    tenant = MagicMock()
    tenant.__enter__.return_value = tenant
    tenant.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, "Inventario y Proveedores", True, 10, "Gestionar proveedores", True,
         7, "Administrador", True, 3, "AMBAS", True, 20, 7, 10, 3)
    ]
    opened = []
    def connect(name):
        opened.append(name)
        return tenant
    result = preflight(connection, connect_tenant=connect)
    assert result["tenants_validated"] == [f"tenant_empresa_{i}" for i in range(1, 8)]
    assert opened == result["tenants_validated"]


def test_tenant_rejects_inactive_or_incorrect_provider_assignment_and_duplicate_functions():
    base = (1, "Inventario y Proveedores", True, 10, "Gestionar proveedores", True,
            7, "Administrador", True, 3, "AMBAS", True, 20, 7, 10, 3)
    with pytest.raises(PreflightError, match="Gestionar proveedores"):
        validate_tenant_rows([base[:11] + (False,) + base[12:]])
    duplicate = list(base)
    duplicate[0] = 2
    duplicate[3] = 11
    duplicate[4] = "Gestionar proveedores"
    duplicate[1] = "Other module"
    with pytest.raises(PreflightError, match="ambiguous"):
        validate_tenant_rows([base, tuple(duplicate)])
    same_module_duplicate = list(base)
    same_module_duplicate[3] = 11
    with pytest.raises(PreflightError, match="duplicated within module"):
        validate_tenant_rows([base, tuple(same_module_duplicate)])


def test_tenant_rejects_target_function_homonym_but_allows_absent_targets_as_pending():
    provider = (1, "Inventario y Proveedores", True, 10, "Gestionar proveedores", True,
                7, "Administrador", True, 3, "AMBAS", True, 20, 7, 10, 3)
    homonym = (2, "Other module", True, 11, "Gestionar movimientos de inventario", True,
               None, None, None, None, None, None, None, None, None, None)
    with pytest.raises(PreflightError, match="ambiguous"):
        validate_tenant_rows([provider, homonym])
    assert validate_tenant_rows([provider])["state"] == "pending"


def test_dynamic_menu_contract_is_not_static_in_seed():
    sql = SEED.read_text(encoding="utf-8")
    assert "menu" not in sql.lower()
    assert "rol_funcion" in sql


def test_requerir_permiso_resolves_function_within_its_module():
    db = MagicMock()
    db.execute.return_value.first.return_value = SimpleNamespace(accion_nombre=ACCION_AMBAS)
    user = SimpleNamespace(rol_id=7)
    assert requerir_permiso("Gestionar movimientos de inventario")(usuario=user, db=db) is user
    sql = str(db.execute.call_args.args[0])
    assert "funcion.nombre" in sql
    assert "JOIN modulo ON modulo.id = funcion.modulo_id" in sql
    assert "modulo.estado" in sql
    assert "rol.estado" in sql


@pytest.mark.parametrize("dependency", [
    catalogo.permiso_cu26_lectura,
    catalogo.permiso_cu26_escritura,
    movimientos.permiso_cu27_lectura,
    movimientos.permiso_cu27_escritura,
])
def test_cu26_cu27_endpoint_dependencies_accept_seeded_ambas(dependency):
    db = MagicMock()
    db.execute.return_value.first.return_value = SimpleNamespace(accion_nombre=ACCION_AMBAS)
    user = SimpleNamespace(rol_id=7)

    assert dependency(usuario=user, db=db) is user


@pytest.mark.parametrize("dependency", [
    catalogo.permiso_cu26_escritura,
    movimientos.permiso_cu27_escritura,
])
def test_cu26_cu27_write_dependencies_reject_read_only_assignment(dependency):
    db = MagicMock()
    db.execute.return_value.first.return_value = SimpleNamespace(accion_nombre=ACCION_LECTURA)

    with pytest.raises(HTTPException) as denied:
        dependency(usuario=SimpleNamespace(rol_id=7), db=db)

    assert denied.value.status_code == 403


@pytest.mark.parametrize("dependency", [
    catalogo.permiso_cu26_lectura,
    catalogo.permiso_cu26_escritura,
    movimientos.permiso_cu27_lectura,
    movimientos.permiso_cu27_escritura,
])
def test_verified_call_sites_bind_functions_to_their_module(dependency):
    db = MagicMock()
    db.execute.return_value.first.return_value = SimpleNamespace(
        accion_nombre=ACCION_AMBAS,
    )

    dependency(usuario=SimpleNamespace(rol_id=7), db=db)

    sql = str(db.execute.call_args.args[0].compile(
        compile_kwargs={"literal_binds": True},
    ))
    assert "modulo.nombre" in sql
    assert "'Inventario y Proveedores'" in sql


# ---------------------------------------------------------------------------
# M-5: router-level integration with the REAL RBAC dependencies.
#
# ``permiso_cu26_lectura``/``permiso_cu26_escritura``/``permiso_cu27_*``,
# ``requerir_permiso``, ``obtener_usuario_actual`` and ``get_tenant_claims`` are
# all exercised as-is. Only the lowest level (the session factory) is replaced
# by an isolated SQLite tenant database, so the assertions fail if the router
# stops consuming the dependency or if the resolver breaks.
# ---------------------------------------------------------------------------
CU26_FUNCION = "Gestionar productos, categorías y lotes"
CU27_FUNCION = "Gestionar movimientos de inventario"
ACCION_IDS = {"LECTURA": 1, "ESCRITURA": 2, "AMBAS": 3}

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

INVENTORY_DDL = [
    """CREATE TABLE categoria (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
       descripcion TEXT, estado BOOLEAN NOT NULL DEFAULT 1,
       fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE producto (id INTEGER PRIMARY KEY AUTOINCREMENT, categoria_id INTEGER NOT NULL,
       codigo TEXT NOT NULL UNIQUE, nombre TEXT NOT NULL, descripcion TEXT,
       unidad_medida TEXT NOT NULL DEFAULT 'UNIDAD', stock_minimo INTEGER NOT NULL DEFAULT 0,
       estado BOOLEAN NOT NULL DEFAULT 1,
       fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
       fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE lote (id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL,
       numero_lote TEXT NOT NULL, fecha_fabricacion DATE, fecha_vencimiento DATE,
       observaciones TEXT, estado BOOLEAN NOT NULL DEFAULT 1,
       fecha_creacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE inventario (id INTEGER PRIMARY KEY AUTOINCREMENT, lote_id INTEGER UNIQUE NOT NULL,
       stock_actual INTEGER NOT NULL DEFAULT 0, stock_reservado INTEGER NOT NULL DEFAULT 0,
       fecha_actualizacion TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE movimiento_inventario (id INTEGER PRIMARY KEY AUTOINCREMENT,
       inventario_id INTEGER NOT NULL, usuario_id INTEGER, tipo TEXT NOT NULL,
       cantidad INTEGER NOT NULL, stock_anterior INTEGER NOT NULL,
       stock_posterior INTEGER NOT NULL, fecha_hora TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
       observaciones TEXT, referencia_tipo TEXT, referencia_id INTEGER)""",
]

# Tenant A owns a fully seeded RBAC; tenant B has the roles/users but no grants,
# so a permission of tenant A can never authorize a tenant B request.
TENANT_A_GRANTS = (
    (1, CU26_FUNCION, "AMBAS"),
    (1, CU27_FUNCION, "AMBAS"),
    (2, CU26_FUNCION, "LECTURA"),
    (2, CU27_FUNCION, "LECTURA"),
)
TENANT_B_GRANTS: tuple = ()


def build_tenant_database(grants) -> object:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        for statement in RBAC_DDL + INVENTORY_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO rol VALUES (1,'Administrador',NULL,1,0,'2026-01-01'),"
            "(2,'SoloLectura',NULL,1,0,'2026-01-01'),(3,'Profesional',NULL,1,0,'2026-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO usuario VALUES "
            "(7,'admin@example.test','x',1,'2026-01-01',1),"
            "(8,'lectura@example.test','x',1,'2026-01-01',2),"
            "(9,'profesional@example.test','x',1,'2026-01-01',3)"
        )
        connection.exec_driver_sql(
            "INSERT INTO modulo VALUES (11,'Inventario y Proveedores',NULL,1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO funcion VALUES (13,11,?,NULL,1),(14,11,?,NULL,1)",
            (CU26_FUNCION, CU27_FUNCION),
        )
        connection.exec_driver_sql(
            "INSERT INTO accion VALUES (1,'LECTURA',NULL,1),(2,'ESCRITURA',NULL,1),(3,'AMBAS',NULL,1)"
        )
        for index, (rol_id, funcion, accion) in enumerate(grants, start=1):
            funcion_id = 13 if funcion == CU26_FUNCION else 14
            connection.exec_driver_sql(
                "INSERT INTO rol_funcion VALUES (?,?,?,?,NULL)",
                (index, rol_id, funcion_id, ACCION_IDS[accion]),
            )
        connection.exec_driver_sql(
            "INSERT INTO categoria (id,nombre,estado) VALUES (1,'Insumos',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO producto (id,categoria_id,codigo,nombre,estado) "
            "VALUES (1,1,'LOT-001','Lente',1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO lote (id,producto_id,numero_lote,estado,fecha_vencimiento) "
            "VALUES (1,1,'LOTE-1',1,'2099-01-01')"
        )
    return engine


def test_requerir_permiso_denies_global_function_homonym_but_accepts_scoped_module():
    engine = build_tenant_database(TENANT_A_GRANTS)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO modulo VALUES (12,'Otro modulo',NULL,1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO funcion VALUES (99,12,?,NULL,1)", (CU27_FUNCION,)
            )
            connection.exec_driver_sql(
                "INSERT INTO rol_funcion VALUES (90,1,99,3,NULL)"
            )
        with Session(engine, autoflush=False) as db:
            user = SimpleNamespace(rol_id=1)

            with pytest.raises(HTTPException) as denied:
                requerir_permiso(CU27_FUNCION)(usuario=user, db=db)
            assert denied.value.status_code == 403

            assert requerir_permiso(
                CU27_FUNCION, modulo="Inventario y Proveedores",
            )(usuario=user, db=db) is user
    finally:
        engine.dispose()


@pytest.mark.parametrize("tabla", ["rol", "modulo"])
def test_requerir_permiso_denies_inactive_rol_or_modulo(tabla):
    engine = build_tenant_database(TENANT_A_GRANTS)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f"UPDATE {tabla} SET estado = 0")
        with Session(engine, autoflush=False) as db:
            with pytest.raises(HTTPException) as denied:
                requerir_permiso(
                    CU26_FUNCION, modulo="Inventario y Proveedores",
                )(usuario=SimpleNamespace(rol_id=1), db=db)
            assert denied.value.status_code == 403
    finally:
        engine.dispose()


@pytest.fixture
def rbac_router():
    engines = {
        "11": build_tenant_database(TENANT_A_GRANTS),
        "12": build_tenant_database(TENANT_B_GRANTS),
    }
    api = FastAPI()
    api.include_router(catalogo_router)
    api.include_router(movimientos_router)

    def tenant_session(claims: dict = Depends(get_tenant_claims)):
        # Tenant selection comes from the signed JWT claims, exactly like
        # ``get_db``; only the physical connection is local SQLite.
        engine = engines[str(claims["tenant_id"])]
        with Session(engine, autoflush=False) as db:
            yield db

    api.dependency_overrides[get_db] = tenant_session
    try:
        with TestClient(api) as client:
            yield client, engines
    finally:
        api.dependency_overrides.clear()
        for engine in engines.values():
            engine.dispose()


def auth(usuario_id: int, rol_id: int, *, tenant_id: int = 11, empresa_id: int = 1):
    token = crear_tenant_access_token(usuario_id, rol_id, tenant_id, empresa_id, "CLINICA-A")
    return {"Authorization": f"Bearer {token}"}


def count_categorias(engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(text("SELECT count(*) FROM categoria"))


@pytest.mark.parametrize("rol_id,usuario_id", [(1, 7), (2, 8)])
def test_router_read_allowed_with_lectura_or_ambas(rbac_router, rol_id, usuario_id):
    client, _ = rbac_router
    headers = auth(usuario_id, rol_id)

    categorias = client.get("/inventario-proveedores/categorias", headers=headers)
    movimientos_list = client.get("/inventario-proveedores/movimientos", headers=headers)

    assert categorias.status_code == 200, categorias.text
    assert categorias.json()[0]["nombre"] == "Insumos"
    assert movimientos_list.status_code == 200, movimientos_list.text


@pytest.mark.parametrize("path,payload", [
    ("/inventario-proveedores/categorias", {"nombre": "Nueva"}),
    ("/inventario-proveedores/movimientos", {"lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 5}),
])
def test_router_write_denied_with_lectura_only(rbac_router, path, payload):
    client, engines = rbac_router

    response = client.post(path, json=payload, headers=auth(8, 2))

    assert response.status_code == 403, response.text
    assert "No tiene permiso" in response.text
    assert count_categorias(engines["11"]) == 1  # no side effect


@pytest.mark.parametrize("path,payload", [
    ("/inventario-proveedores/categorias", {"nombre": "Nueva"}),
    ("/inventario-proveedores/movimientos", {"lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 5}),
])
def test_router_write_allowed_with_ambas(rbac_router, path, payload):
    client, _ = rbac_router

    response = client.post(path, json=payload, headers=auth(7, 1))

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/inventario-proveedores/categorias", None),
    ("post", "/inventario-proveedores/categorias", {"nombre": "Nueva"}),
    ("get", "/inventario-proveedores/movimientos", None),
    ("post", "/inventario-proveedores/movimientos", {"lote_id": 1, "tipo": "STOCK_INICIAL", "cantidad": 1}),
])
def test_router_denies_role_without_function(rbac_router, method, path, payload):
    client, engines = rbac_router
    kwargs = {"json": payload} if payload is not None else {}

    response = getattr(client, method)(path, headers=auth(9, 3), **kwargs)

    assert response.status_code == 403, response.text
    assert count_categorias(engines["11"]) == 1


@pytest.mark.parametrize("method,path,payload", [
    ("get", "/inventario-proveedores/categorias", None),
    ("get", "/inventario-proveedores/movimientos", None),
])
def test_router_requires_tenant_authentication(rbac_router, method, path, payload):
    client, _ = rbac_router
    kwargs = {"json": payload} if payload is not None else {}

    unauthenticated = getattr(client, method)(path, **kwargs)
    legacy = getattr(client, method)(
        path, headers={"Authorization": f"Bearer {crear_access_token(7, 1)}"}, **kwargs,
    )
    saas_admin = getattr(client, method)(
        path, headers={"Authorization": f"Bearer {crear_saas_access_token(1)}"}, **kwargs,
    )
    tampered = getattr(client, method)(
        path, headers={"Authorization": f"Bearer {auth(7, 1)['Authorization'].split()[1]}x"}, **kwargs,
    )

    assert unauthenticated.status_code in (401, 403)
    assert legacy.status_code == 401, legacy.text
    assert saas_admin.status_code == 401, saas_admin.text
    assert tampered.status_code == 401, tampered.text


def test_router_isolates_permissions_between_tenants(rbac_router):
    """A tenant A permission must not authorize the same user at tenant B."""
    client, _ = rbac_router

    allowed = client.get("/inventario-proveedores/categorias", headers=auth(7, 1))
    other_tenant = client.get(
        "/inventario-proveedores/categorias",
        headers=auth(7, 1, tenant_id=12, empresa_id=2),
    )
    other_tenant_write = client.post(
        "/inventario-proveedores/categorias",
        json={"nombre": "Cruzada"},
        headers=auth(7, 1, tenant_id=12, empresa_id=2),
    )

    assert allowed.status_code == 200, allowed.text
    assert other_tenant.status_code == 403, other_tenant.text
    assert other_tenant_write.status_code == 403, other_tenant_write.text
    assert "Insumos" not in other_tenant.text


def test_router_consumes_the_real_permission_dependency(rbac_router):
    """Removing the grant at runtime must change the outcome (no stubbed 403)."""
    client, engines = rbac_router

    assert client.get("/inventario-proveedores/categorias", headers=auth(7, 1)).status_code == 200
    with engines["11"].begin() as connection:
        connection.exec_driver_sql("DELETE FROM rol_funcion WHERE rol_id = 1")

    denied = client.get("/inventario-proveedores/categorias", headers=auth(7, 1))
    assert denied.status_code == 403, denied.text

    with engines["11"].begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO rol_funcion VALUES (99,1,13,3,NULL)"
        )
    restored = client.get("/inventario-proveedores/categorias", headers=auth(7, 1))
    assert restored.status_code == 200, restored.text
    # The unused role 3 stays denied: the dependency is evaluated per request.
    assert client.get("/inventario-proveedores/categorias", headers=auth(9, 3)).status_code == 403
