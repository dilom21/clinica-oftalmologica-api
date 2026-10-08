# ODD: SaaS 7E.1 — Reconcile OFTALMO-NORTE

## Objective
Correct SQL literal handling and reconcile the existing `tenant_oftalmo_norte` only after read-only validation passes.

## Authorized scope
- `scripts/saas/provision_tenant.py`
- `tests/test_tenant_provisioning.py`
- This feature task document and its Engram mirror
- No other application area, tenant, environment file, or remote operation

## Constraints
- Preserve strict PostgreSQL identifier validation.
- No `--provision-clean`, `--execute`, `--recover-error`, DROP, CREATE DATABASE, dump, restore, or password prompt.
- Do not modify `tenant_vision_clara`.
- Do not commit, push, or merge.

## Tasks
- [x] 7E1-T1: Separate safe SQL literal quoting from identifier validation and add literal/identifier security tests.
- [x] 7E1-T2: Add `--reconcile-clean-existing` for the existing OFTALMO-NORTE ERROR/ERROR mapping and expand all pre-activation gates/tests.
- [x] 7E1-T3: Run focused tests, full suite, diff check, read-only audit, and only then execute the exact reconcile command; verify post-activation routing, registry, isolation, and login test preparation.

## Acceptance criteria
- Literals escape apostrophes as doubled apostrophes and accept email punctuation/spaces/hyphens without weakening identifier validation.
- Reconcile requires exact OFTALMO-NORTE/database confirmation, ERROR/ERROR state, and an existing database; it performs no provisioning operations and preserves `intentos=1`.
- Catalog, bootstrap, clean-data, schema, structure, and health failures block activation.
- Required focused/full tests and `git diff --check` pass before real reconciliation.
- Real audit reports all requested booleans true before reconciliation.
- Post-reconcile Control Plane is ACTIVA/COMPLETADO/v1 with tenant isolation intact and no secret output.

## Verification
- `\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q`
- `\.venv\Scripts\python.exe -m pytest -q`
- `git diff --check`
- Read-only audit against `tenant_oftalmo_norte`
- Exact reconcile command from the user prompt, only after all audit booleans are true

## Progress
- Route: delegated direct writer, because implementation touches two non-trivial files and requires preparation from a multi-file map.
- Current step: 7E1 complete; post-activation login remains a manual user-supplied-password check.
- Completed locally: focused tests (97 passed), full suite (601 passed, 2 warnings), and `git diff --check`.
- Read-only audit: `structure_ok`, `catalogs_ok`, `admin_ok`, `clean_data_ok`, `schemas_ok`, and `health_ok` were all true; direct counts confirmed 27 tables, 27 sequences, 182 columns, 77 constraints, 67 indexes, catalog counts 3/6/19/4/39, bootstrap checks 1/1/1/1, clinical counts all zero, and prohibited schemas 0.
- Reconciliation executed only after the all-true audit with `--reconcile-clean-existing --empresa OFTALMO-NORTE --confirm tenant_oftalmo_norte`; result ACTIVA/COMPLETADO.
- Post-activation: version_schema=v1, intentos=1, resolver and registry checks passed, engines are distinct, and SELECT 1 passed for both tenants.
- Login: no password was requested or printed; manual OFTALMO-NORTE and cross-tenant checks remain prepared for the user's existing password.
- Next step: user performs the manual login matrix.
