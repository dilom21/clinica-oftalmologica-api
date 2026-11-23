# PASO 7I SaaS audit log INET fix

## Objective
Correct the SaaS audit-log ORM mapping for PostgreSQL `INET` and make SaaS login audit persistence transactional without changing the database schema, tenant data, or clinical data.

## Authorized scope
- `app/modules/administracion_saas/models.py`
- `app/modules/administracion_saas/service.py`
- `app/modules/administracion_saas/repository.py` only if required by the verified fix
- `tests/test_saas_admin_backend.py`
- No real login, schema migration, tenant mutation, clinical-data mutation, commit, push, or merge.

## Checklist
- [x] Confirm live/static DB and ORM contract for `saas_bitacora`.
- [x] Map `SaasBitacora.ip` to PostgreSQL `INET`.
- [x] Preserve nullable IPv4, IPv6, and `None` behavior.
- [x] Ensure login audit failure rolls back `ultimo_acceso` and returns a sanitized error.
- [x] Add regression coverage for mapping, login persistence, rollback, and JWT boundaries.
- [x] Run targeted tests, full suite, and `git diff --check`.
- [x] Perform read-only final state verification.

## Acceptance checks
- `\.venv\Scripts\python.exe -m pytest tests/test_saas_admin_backend.py -q`
- `\.venv\Scripts\python.exe -m pytest -q`
- `git diff --check`

## Progress
- Audit completed read-only; exact DB/ORM mismatch confirmed by exploration.
- Implementation completed in `models.py`, `service.py`, and `test_saas_admin_backend.py`.
- Evidence: targeted `17 passed`; full suite `631 passed, 2 warnings`; live control-plane read-only checks show 7 active tenants and 7 completed provisions; no real login was executed.

## Route
Delegated direct writer: multiple non-trivial source/test files require coordinated changes after the read-only audit.
