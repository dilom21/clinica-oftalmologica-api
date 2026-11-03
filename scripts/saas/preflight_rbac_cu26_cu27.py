"""Read-only, fail-closed preflight for the CU26/CU27 tenant RBAC seed."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

EXPECTED_TENANTS = 7
EXPECTED_SCHEMA = "v4-sprint3-cu32-cu33"
URL_ENV = "RBAC_PREFLIGHT_DATABASE_URL"
TARGET_MODULE = "Inventario y Proveedores"
TARGET_FUNCTIONS = ("Gestionar productos, categorías y lotes", "Gestionar movimientos de inventario")
PROVIDER_FUNCTION = "Gestionar proveedores"


class PreflightError(RuntimeError):
    pass


CONTROL_QUERY = """SELECT e.id, e.codigo, e.razon_social, e.estado,
       td.id, td.database_name, td.estado, td.version_schema
FROM saas_control.empresa AS e
JOIN saas_control.tenant_database AS td ON td.empresa_id = e.id
WHERE e.estado = 'ACTIVA' ORDER BY e.id, td.id"""

TENANT_QUERY = """SELECT m.id, m.nombre, m.estado, f.id, f.nombre, f.estado,
       r.id, r.nombre, r.estado, a.id, a.nombre, a.estado,
       rf.id, rf.rol_id, rf.funcion_id, rf.accion_id
FROM public.modulo AS m
LEFT JOIN public.funcion AS f ON f.modulo_id = m.id
LEFT JOIN public.rol_funcion AS rf ON rf.funcion_id = f.id
LEFT JOIN public.rol AS r ON r.id = rf.rol_id
LEFT JOIN public.accion AS a ON a.id = rf.accion_id
ORDER BY m.id, f.id, rf.id"""


def _control(row):
    # Keep compatibility with the original seven-column unit fixture.
    if len(row) == 7:
        return {"id": row[0], "code": row[1], "company": row[1], "company_state": row[2],
                "tenant_id": row[3], "database": row[4], "tenant_state": row[5], "version": row[6]}
    return {"id": row[0], "code": row[1], "company": row[2], "company_state": row[3],
            "tenant_id": row[4], "database": row[5], "tenant_state": row[6], "version": row[7]}


def validate_control_rows(rows, expected_count: int = EXPECTED_TENANTS) -> dict:
    if expected_count != EXPECTED_TENANTS:
        raise PreflightError("the RBAC preflight requires exactly seven active companies")
    items = [_control(row) for row in rows]
    if len(items) != expected_count:
        raise PreflightError(f"expected {expected_count} active companies, found {len(items)}")
    for item in items:
        if item["company_state"] != "ACTIVA" or item["tenant_state"] != "ACTIVA":
            raise PreflightError(f"company/tenant is not active: {item['code']}")
        if not item["tenant_id"] or not str(item["database"] or "").strip():
            raise PreflightError(f"missing tenant id or database name: {item['code']}")
        if not str(item["version"] or "").strip():
            raise PreflightError(f"missing schema version for {item['code']}")
        if str(item["version"]).strip() != EXPECTED_SCHEMA:
            raise PreflightError(f"unsupported schema version for {item['code']}")
    for label, values in (("company code", [x["code"] for x in items]),
                          ("company", [x["company"] for x in items]),
                          ("database name", [x["database"] for x in items])):
        if len(set(values)) != len(values):
            suffix = " code" if label == "company" else ""
            raise PreflightError(f"duplicate active {label}{suffix} in control plane")
    return {"valid": True, "companies": len(items), "database_names": [x["database"] for x in items]}


def _field(row, key, index):
    return row.get(key) if isinstance(row, dict) else row[index]


def validate_tenant_rows(rows) -> dict:
    """Validate one tenant snapshot; accepts dicts or TENANT_QUERY tuples."""
    modules, functions, assignments = {}, [], []
    for row in rows:
        module = {"id": _field(row, "module_id", 0), "name": _field(row, "module_name", 1), "active": _field(row, "module_active", 2)}
        if module["id"] is not None:
            modules[module["id"]] = module
        function = {"id": _field(row, "function_id", 3), "name": _field(row, "function_name", 4),
                    "active": _field(row, "function_active", 5), "module_id": module["id"]}
        if function["id"] is not None:
            functions.append(function)
        if _field(row, "assignment_id", 12) is not None:
            assignments.append({"function_id": _field(row, "assignment_function_id", 14) or function["id"],
                                "role_id": _field(row, "role_id", 13), "role_name": _field(row, "role_name", 7),
                                "role_active": _field(row, "role_active", 8), "action_name": _field(row, "action_name", 10),
                                "action_active": _field(row, "action_active", 11),
                                "action_id": _field(row, "action_id", 15)})
    target_modules = [m for m in modules.values() if m["name"] == TARGET_MODULE]
    if len(target_modules) != 1 or not target_modules[0]["active"]:
        raise PreflightError("tenant must have exactly one active Inventario y Proveedores module")
    names = {}
    function_keys = set()
    for function in functions:
        key = (function["module_id"], function["name"])
        if key in function_keys:
            raise PreflightError("function is duplicated within module")
        function_keys.add(key)
        names.setdefault(function["name"], set()).add(function["module_id"])
    if any(len(module_ids) > 1 for module_ids in names.values()):
        raise PreflightError("function name is ambiguous between modules")
    module_names = {}
    for module in modules.values():
        module_names.setdefault(module["name"], set()).add(module["id"])
    if any(len(module_ids) > 1 for module_ids in module_names.values()):
        raise PreflightError("module name is ambiguous")
    for field, label in (("role_name", "role"), ("action_name", "action")):
        relevant = {}
        for assignment in assignments:
            name = assignment[field]
            entity_id = assignment["role_id"] if field == "role_name" else assignment["action_id"]
            if name is not None:
                relevant.setdefault(name, set()).add(entity_id)
        if any(len(entity_ids) > 1 for entity_ids in relevant.values()):
            raise PreflightError(f"{label} name is ambiguous")
    target_id = target_modules[0]["id"]
    if any(function["name"] in TARGET_FUNCTIONS and function["module_id"] != target_id for function in functions):
        raise PreflightError("function name is ambiguous between modules")
    provider = [f for f in functions if f["module_id"] == target_id and f["name"] == PROVIDER_FUNCTION]
    if not provider:
        raise PreflightError("Gestionar proveedores is missing")
    if len(provider) > 1:
        raise PreflightError("Gestionar proveedores is duplicated")
    provider_assignment = [a for a in assignments if provider and a["function_id"] == provider[0]["id"]]
    if provider:
        if not provider[0]["active"] or len(provider_assignment) != 1:
            raise PreflightError("Gestionar proveedores has an invalid assignment")
        a = provider_assignment[0]
        if a["role_name"] != "Administrador" or not a["role_active"] or a["action_name"] != "AMBAS" or not a["action_active"]:
            raise PreflightError("Gestionar proveedores assignment is incorrect")
    target = [f for f in functions if f["module_id"] == target_id and f["name"] in TARGET_FUNCTIONS]
    if target and len(target) != len(TARGET_FUNCTIONS):
        raise PreflightError("CU26/CU27 function configuration is partial")
    if target:
        for function in target:
            if not function["active"]:
                raise PreflightError("CU26/CU27 function is inactive")
            fa = [a for a in assignments if a["function_id"] == function["id"]]
            if len(fa) != 1 or fa[0]["role_name"] != "Administrador" or not fa[0]["role_active"] or fa[0]["action_name"] != "AMBAS" or not fa[0]["action_active"]:
                raise PreflightError("CU26/CU27 assignment is incorrect")
    return {"state": "configured" if target else "pending", "module": TARGET_MODULE}


def _query(connection, statement):
    with connection.cursor() as cursor:
        cursor.execute(statement)
        return cursor.fetchall()


def preflight(connection, expected_count: int = EXPECTED_TENANTS, connect_tenant=None) -> dict:
    control = validate_control_rows(_query(connection, CONTROL_QUERY), expected_count)
    if connect_tenant is None:
        return control
    tenants = []
    for database in control["database_names"]:
        with connect_tenant(database) as tenant:
            validate_tenant_rows(_query(tenant, TENANT_QUERY))
        tenants.append(database)
    control["tenants_validated"] = tenants
    return control


def _load_explicit_url(args):
    if args.database_url and args.config:
        raise PreflightError("use either --database-url or --config, not both")
    if args.config:
        value = json.loads(Path(args.config).read_text(encoding="utf-8")).get("database_url")
        if not isinstance(value, str) or not value.strip():
            raise PreflightError("config must contain a non-empty database_url")
        return value
    return args.database_url or os.environ.get(URL_ENV)


def _connect_factory(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"postgresql", "postgres", "postgresql+psycopg"} or not parsed.hostname:
        raise PreflightError("laboratory URL must be a PostgreSQL URL with a host")
    try:
        import psycopg
    except ImportError as exc:
        raise PreflightError("psycopg is required to run the laboratory preflight") from exc
    query = parse_qs(parsed.query)
    options = {"host": parsed.hostname, "port": parsed.port or 5432, "user": parsed.username,
               "password": parsed.password, "connect_timeout": 10, "application_name": "si2-rbac-preflight"}
    if query.get("sslmode"):
        options["sslmode"] = query["sslmode"][0]
    def connect(database=None):
        return psycopg.connect(**options, dbname=database or (parsed.path or "").lstrip("/"))
    return connect


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--config")
    parser.add_argument("--allow-lab", action="store_true")
    parser.add_argument("--expected-companies", type=int, default=EXPECTED_TENANTS)
    args = parser.parse_args(argv)
    try:
        url = _load_explicit_url(args)
        if not url:
            raise PreflightError("no explicit laboratory URL/configuration supplied")
        if not args.allow_lab:
            raise PreflightError("refusing connection without --allow-lab")
        connect = _connect_factory(url)
        with connect() as control:
            result = preflight(control, args.expected_companies, lambda name: connect(name))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PreflightError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PRECHECK REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
