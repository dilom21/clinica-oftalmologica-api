# PASO 7B — Tenant Connection Router

## Objective

Add internal, read-only tenant resolution and lazy tenant connection infrastructure over the existing SaaS Control Plane, without changing clinical database behavior.

## Authorized scope

- Repository: `C:\SI2_Proyecto\clinica-oftalmologica-api`
- Create `app/core/tenancy/` modules and `tests/test_tenant_connection_router.py`.
- Reuse the current control-plane engine/session and `app/core/time.py`.
- Do not create databases, migrations, startup tenant engines, or modify `.env`.
- Do not modify login, JWT, clinical routers, existing `get_db`, frontend, mobile, or remote data.
- Do not commit, push, or merge.

## Route and checks

- Task T7B-01 — delegated direct writer: implement Control Plane mappings, context, repository/resolver, exceptions, URL derivation, registry, dependencies, health check, and focused tests. Route evidence: understanding and implementation span more than four files and multiple non-trivial modules.
  - Acceptance: all requested fail-closed validations, lazy/thread-safe cache/disposal, safe URL derivation, explicit control DB dependency, and no clinical integration.
  - Checks: focused pytest, full pytest, `git diff --check`, read-only real VISION-CLARA probe if credentials are available.

## Progress

- [x] T7B-01 implementation and focused tests
- [x] T7B-01 verification evidence recorded
- [x] T7B-01 correction: reject duplicate `ACTIVA` subscriptions before validity selection

## Current verification

- Added `app/core/tenancy/` with read-only Control Plane mappings, immutable context,
  repository/resolver, fail-closed exceptions, safe URL derivation, lazy registry,
  explicit dependencies, and SELECT 1 health check.
- Added `tests/test_tenant_connection_router.py`; all tenant engines are fakes.
- Corrected subscription resolution to count all rows with `estado == "ACTIVA"`
  before filtering by date validity; more than one active row now raises
  `ControlPlaneConsistencyError`, including an expired active row alongside a
  current active row.
- Added focused regression coverage for one expired and one currently valid
  active subscription.
- Real Control Plane remains documented as seven active companies/subscriptions with
  seven tenant databases in `PENDIENTE`; no tenant physical database was contacted.

## Next step

Verification evidence (2026-10-06):

- Focused tests: `33 passed in 0.07s` with
  `.\\venv\\Scripts\\python.exe -m pytest tests/test_tenant_connection_router.py -q`.
- Final full suite: `477 passed in 5.98s`.
- `git diff --check`: passed; Git reported only existing LF/CRLF warnings.
- Real VISION-CLARA read-only probe: metadata resolved as `ACTIVA` company,
  `ACTIVA` subscription, and `PENDIENTE` tenant database; session creation
  rejected with `TenantDatabaseUnavailableError`. No tenant database was
  contacted and no state was changed.
