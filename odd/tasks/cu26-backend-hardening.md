# ODD — CU26 backend hardening

## Objective
Complete and strengthen CU26 category, product, and lot management on branch `dev-josias` without changing databases, migrations, other use cases, or frontend code.

## Authorized scope
- Enforce tenant JWT authentication on all existing 15 CU26 routes while preserving administrator authorization.
- Expand isolated CU26 tests for security, tenant binding, endpoint behavior, and business rules.
- Run CU26, tenant/auth, and CU27 regression checks where the environment permits.
- Read-only diagnosis of RBAC; no seed or data changes.

## Constraints
- Preserve seven tenant databases and schema v4.
- Do not modify `get_db()` globally, migrations, `.env`, secrets, backups, or unrelated modules.
- Do not commit, push, merge, or perform destructive changes.

## Tasks
- [x] CU26-1: Inspected the current router, security/session/tenant integration, schemas/services/models, RBAC code, and tests; the router had no explicit tenant guard and isolated fixtures had no override for it.
- [x] CU26-2: Added `get_tenant_claims` as a router-level dependency; route paths and administrator authorization remain unchanged.
- [x] CU26-3: Updated authorized isolated fixtures and added focused negative token/role and catalog-rule tests without real tenant databases.
- [x] CU26-4: Execute targeted and regression tests; diagnose RBAC state from code/tests read-only and report that seven-tenant live inventory was not performed.

## Acceptance criteria
- Exactly 15 CU26 routes remain registered.
- All 15 require valid tenant JWT plus existing administrator authorization.
- No database/migration/other-CU/frontend changes.
- Tests demonstrate fail-closed security and preserve CU26 business rules.
- CU27 checks show no regression, or are honestly reported as skipped/unavailable.

## Progress
- CU26-1 mapping completed read-only; router lacks explicit `get_tenant_claims`; current test fixture lacks its override.
- CU26-2 and CU26-3 implemented. Test execution initially exposed missing environment packages (`psycopg`, `pwdlib`, Google auth/client); after installing them in the local Python environment, targeted checks passed.

## Verification evidence
- Initial branch: `dev-josias`.
- Initial tracked diff: empty.
- Pre-existing untracked files were preserved.
- Router dependency inspection confirms the same guard is inherited by all 15 registered routes.
- `python -m py_compile app/modules/gestion_inventario_proveedores/api/router.py tests/test_cu26_backend.py`: passed.
- `git diff --check`: passed (only Git's LF/CRLF normalization warning was emitted).
- `python -m pytest -v tests/test_cu26_backend.py`: 9 passed.
- `python -m pytest -v tests/test_auth_tenant.py tests/test_tenant_connection_router.py tests/test_tenant_provisioning.py`: 170 passed, 2 warnings.
- `python -m pytest -v tests/test_cu27_movimientos.py tests/test_cu27_postgres_integracion.py`: 7 passed, 7 skipped because local PostgreSQL integration URL is unavailable.
- RBAC diagnosis: code supports `LECTURA`, `ESCRITURA`, and `AMBAS`; no CU26/catalog function seed or seven-tenant read-only inventory was available in this environment. No RBAC data was changed.

## Next step
Implementation and applicable local checks are complete. No work-unit commit was created because the user explicitly prohibited commits. Remaining gate: authorized read-only inventory of RBAC records across the seven v4 tenants, if separately provisioned.
