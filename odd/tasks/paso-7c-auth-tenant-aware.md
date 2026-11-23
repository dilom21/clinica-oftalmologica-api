# Paso 7C — Auth/JWT tenant-aware

## Objective

Prepare tenant-aware authentication without cutting over the existing login or connecting to physical tenant databases.

## Problem and rationale

The SaaS control plane and tenant connection router exist, but authentication must issue and validate a separate tenant JWT contract while preserving the legacy authentication surface.

## Authorized scope

- Backend only, inside this repository.
- Tenant login endpoint, tenant JWT helpers, tenant dependency, Control Plane revalidation helper, controlled fakes/mocks, and tests.
- No physical tenant database creation or state changes.
- No frontend/mobile changes, clinical router migration, `get_db` replacement, commit, push, merge, or cutover of the legacy login.

## Tasks

- [x] 7C-01 Add a separate tenant login schema and endpoint using the existing router conventions; reject forbidden body fields and map tenant states safely. Evidence: `TenantLoginRequest` and `POST /seguridad/tenant/login`; the endpoint is separate from `/seguridad/login`.
- [x] 7C-02 Add tenant-aware JWT creation and a separate tenant claims dependency; preserve legacy token behavior and explicitly document `tenant_id = tenant_database.id`. Evidence: `crear_tenant_access_token`, `get_tenant_claims`, and the security docstring/claims tests.
- [x] 7C-03 Add injectable tenant authentication fakes/repositories and Control Plane revalidation without opening a `PENDIENTE` tenant database. Evidence: `tenant_auth_service.py`, `revalidate_tenant_context`, and the pre-registry pending-state test.
- [x] 7C-04 Add the requested tenant-auth regression/security tests and run the focused block, full suite, and `git diff --check`. Evidence: `tests/test_auth_tenant.py` contains the requested focused coverage; command results are recorded below.

## Acceptance criteria and checks

- Legacy `/seguridad/login` behavior, tokens, clinical routers, and `get_db` remain intact.
- Tenant login is separate and does not accept `database_name`, `tenant_id`, or `empresa_id` in its body.
- Tenant tokens contain only the approved claims and no connection or secret material.
- Tenant dependency rejects legacy tokens, missing claims, bad signatures, and manipulated tenant claims.
- Control Plane revalidation checks current company, subscription, and tenant database state.
- `PENDIENTE` fails before tenant engine creation; no physical tenant database is opened or created.
- Required tests pass, or every failure is reported with its actual cause.

## Progress and evidence

- Route: delegated direct implementation, because this task spans multiple non-trivial backend files and tests.
- Exploration: completed read-only inspection of both Paso 7C documents and the relevant auth/tenancy code.
- TDD mode: not established by this organic workflow; use the repository's existing test conventions and the exact requested pytest commands.
- Result: 7C-01 through 7C-04 implemented and verified; no commit identity exists because no commit was created.

## Tenant identity decision

`tenant_id` in the tenant JWT is exactly `saas_control.tenant_database.id`. It is not a database name, URL, host, credential, or client-provided selector.

## Verification evidence

- Commit identity: none; no commit was created in this work unit.
- Focused tests: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_auth_tenant.py -q` → **27 passed, 2 warnings**.
- Full suite: `.\\.venv\\Scripts\\python.exe -m pytest -q` → **504 passed, 2 warnings**.
- `git diff --check` → **passed**; Git emitted only existing LF/CRLF working-copy warnings.
- Post-validation hardening: tenant database-name and Control Plane consistency failures map to the controlled tenant-unavailable response instead of escaping as 500.
