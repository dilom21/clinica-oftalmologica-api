from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.core.dependencies import ACCION_AMBAS, requerir_permiso
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


def test_requerir_permiso_currently_resolves_function_by_global_name():
    db = MagicMock()
    db.execute.return_value.first.return_value = SimpleNamespace(accion_nombre=ACCION_AMBAS)
    user = SimpleNamespace(rol_id=7)
    assert requerir_permiso("Gestionar movimientos de inventario")(usuario=user, db=db) is user
    sql = str(db.execute.call_args.args[0])
    assert "funcion.nombre" in sql
    assert "modulo.nombre" not in sql


def test_compatible_proposal_is_module_qualified_without_editing_dependency():
    dependency = (ROOT / "app" / "core" / "dependencies.py").read_text(encoding="utf-8")
    assert "Funcion.nombre == nombre_funcion" in dependency
    assert "modulo_id" not in dependency.split("def requerir_permiso", 1)[1]
    # The seed remains safe because it resolves functions by module_id + name.
    sql = SEED.read_text(encoding="utf-8")
    assert "modulo_id = v_modulo_id" in sql
