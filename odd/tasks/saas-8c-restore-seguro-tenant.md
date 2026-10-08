# SaaS 8C — Safe per-tenant restore

## Objective and scope
Implement a REAL, safe restore of the SAME tenant from a COMPLETADO backup: validate backup
-> mandatory PRE_RESTORE -> logical tenant lock (RESTAURANDO) -> dispose tenant engines ->
safe restore -> verify -> tenant ACTIVA, with automatic rollback through the PRE_RESTORE
archive when the restore fails after the final database was modified. FAILED rollback must
never leave the tenant ACTIVA and must never be hidden. Work only in this repository;
preserve all existing dirty work. Do NOT execute a real restore, do NOT delete or modify
backup_id=1/2/3, do NOT build frontend, no public bucket, no secrets, no commit/push/merge.
The client never supplies `database_name`, `storage_key`, or a file path.

## Problem and rationale
8A/8B produce real, verified tenant backups (backup_id=2 MANUAL, backup_id=3 AUTOMATICO,
backup_id=1 kept as ERROR evidence). There is no way to restore one of them. Restoring a
tenant DB without guardrails is destructive: a wrong-tenant backup, a corrupt archive, an
active traffic window, or a half-applied `pg_restore` can silently destroy clinical data.
8C adds the safe, auditable, reversible restore path required to close the backup/restore
lifecycle.

## Route and checks
Delegated direct: implementation spans a new SQL migration (007), ORM metadata, a restore
runner, a restore orchestration service, guarded modifications to the 8A/8B backup engine,
schemas, API endpoints and a focused test file — more than two non-trivial files. Mapping
was done by the orchestrator (4+ files). TDD: not configured or requested for this ODD
feature; use ordinary functional checks. Deterministic checks:
- `.\.venv\Scripts\python.exe -m pytest tests/test_saas_restore.py -q`
- `.\.venv\Scripts\python.exe -m pytest -q`
- `git diff --check`
Advisory forecast: over 700 authored lines across SQL, metadata, runner, service, endpoints
and tests. Delivery: ask-on-risk, no delivery requested. The user explicitly prohibits
commits/push/merge, so all work units remain uncommitted (this overrides the ODD work-unit
commit step).

## Audit findings (pre-implementation, read-only)
- `saas_control.tenant_database.estado` CHECK (001) = PENDIENTE, PROVISIONANDO, ACTIVA,
  SUSPENDIDA, ERROR. `RESTAURANDO` is NOT allowed -> 007 must widen the CHECK explicitly
  (additive; no validation removed).
- `TenantResolver.require_active_database` and `TenantEngineRegistry.get_or_create` already
  reject any `database_estado != 'ACTIVA'`, so `RESTAURANDO` is blocked for clinical traffic
  and tenant login without a resolver code change (verified by tests).
- `TenantEngineRegistry.dispose_tenant(tenant_database_id)` already exists and is reused.
- Provisioning backend (`scripts/saas/provision_tenant.py`) already proves CREATE/DROP
  DATABASE, `pg_restore --exit-on-error --no-owner --no-privileges`, `SELECT 1` health and a
  structural fingerprint. `drop_database` is hardcoded to one tenant and is NOT reused for
  restore.
- Swap-by-rename (`ALTER DATABASE RENAME`) is NOT assumed. 007/restore detect capability at
  runtime; when unavailable the in-place recreation path (PRE_RESTORE + automatic rollback)
  is used.

## Checklist
- [x] 8C-1: Added `database/saas_control/007_create_restore_tenant.sql` (new table
  `saas_control.restore_tenant`, one-active-restore-per-tenant partial unique index
  `uq_restore_tenant_activo`, widen the `tenant_database.estado` CHECK to include
  `RESTAURANDO`, REVOKE) without touching 001-006, plus the ORM `RestoreTenant` model.
- [x] 8C-2: Added `restore_runner.py` (`TenantRestoreRunner`): archive `--list`, capability
  probe, CREATE/DROP/RENAME DATABASE, session termination, `pg_restore` in-place/temporal,
  structural verify and `SELECT 1` health — no `shell=True`, password only in child env,
  argv without secrets, reserved DB names rejected.
- [x] 8C-3: Added `restore_service.py`: backup validation (COMPLETADO, same tenant, storage,
  size, SHA-256, `pg_restore --list`, version), mandatory PRE_RESTORE, per-tenant lock,
  RESTAURANDO transition, engine dispose, temporal-or-in-place restore, final verify,
  activation, automatic rollback, and SaaS bitácora. Swap is never assumed: it needs the
  explicit `SAAS_RESTORE_ALLOW_DATABASE_SWAP` opt-in AND a proven `rolcreatedb` capability;
  otherwise the safe in-place recreation path runs.
- [x] 8C-4: Guarded the 8A/8B backup engine (`backup_service.py`): MANUAL/AUTOMATICO backups
  of a tenant are blocked while a restore is PENDIENTE/EN_PROCESO/ROLLBACK_EN_PROCESO, while
  PRE_RESTORE reuses the same engine through `create_pre_restore_backup` with the dedicated
  `CREAR_BACKUP_PRE_RESTORE` action.
- [x] 8C-5: Added restore schemas and the SaaS Admin endpoints
  (`POST /saas/restores/validate`, `POST /saas/restores`, `GET /saas/restores`,
  `GET /saas/restores/{restore_id}`) with `extra="forbid"` requests.
- [x] 8C-6: Added `tests/test_saas_restore.py` (44 tests) covering the full 8C matrix, ran
  the three required commands and reported results.

## Progress and evidence
Implemented by the orchestrator with full prior-context (backup engine, tenancy, provisioner
and both SQL migrations read in full). Deliverables:
- `database/saas_control/007_create_restore_tenant.sql` — additive; widens exactly the same
  `tenant_database_estado_chk` to include `RESTAURANDO`; creates `restore_tenant` with
  `uq_restore_tenant_activo` (PENDIENTE/EN_PROCESO/ROLLBACK_EN_PROCESO); REVOKE. 001-006
  untouched (asserted by test).
- `app/modules/administracion_saas/models.py` — `RestoreTenant` ORM with the partial unique
  index and sanitized text columns.
- `app/modules/administracion_saas/restore_runner.py` — real client wrapper (pg_restore/psql),
  capability probe, no `shell=True`, secret-free argv.
- `app/modules/administracion_saas/restore_service.py` — orchestration, reservation lock,
  archive validation, PRE_RESTORE, RESTAURANDO, dispose, restore, verify, activation,
  automatic rollback + rollback-failure handling, bitácora, list.
- `app/modules/administracion_saas/backup_service.py` — restore guard + `create_pre_restore_backup`.
- `app/modules/administracion_saas/backup_automatic.py` — treats `restore_in_progress` as omitida.
- `app/modules/administracion_saas/schemas.py` + `api/router.py` — restore request/response
  schemas (`extra="forbid"`) and four SaaS Admin endpoints.
- `tests/test_saas_restore.py` (44) and a minimal fixture update in `tests/test_saas_backup.py`
  (create the new `restore_tenant` table because the guard now queries it).

Observed checks on final bytes:
- `.\.venv\Scripts\python.exe -m pytest tests/test_saas_restore.py -q` => 44 passed.
- `.\.venv\Scripts\python.exe -m pytest -q` => 785 passed, 2 pre-existing short-JWT-key warnings.
- `git diff --check` => exit 0 (only pre-existing LF/CRLF warnings on pre-existing files);
  new files have no trailing whitespace.

No real restore, no `.env` edit, no backup deleted/modified, no frontend, no bucket, no
commit/push/merge. All work remains uncommitted; pre-existing dirty changes preserved.

Real execution remains pending: apply 007 on the control-plane database, then perform a
controlled restore of MEDICO-OCULAR (prefer backup_id=3) after the mandatory PRE_RESTORE.
No real restore was executed from this session.

## Acceptance criteria
- Only COMPLETADO backups of the SAME tenant restore; wrong tenant, ERROR backup, missing
  object, size mismatch, SHA-256 mismatch, invalid `pg_restore --list` and incompatible
  version are rejected before any DB mutation.
- PRE_RESTORE is mandatory and uses the 8A/8B engine (`tipo=PRE_RESTORE`, COMPLETADO with
  size/SHA-256/list/storage); if it fails the tenant stays ACTIVA and the DB is untouched.
- One restore per tenant; backups of the same tenant are blocked while a restore is active.
- TenantResolver/registry block `RESTAURANDO`; tenant engines are disposed before restore.
- The final DB is verified (SELECT 1, structure, critical tables, schema version); only then
  is the tenant ACTIVA.
- A failure after mutating the final DB triggers an automatic rollback from PRE_RESTORE; a
  successful rollback leaves the tenant ACTIVA with restore ERROR + rollback COMPLETADO; a
  FAILED rollback never leaves the tenant ACTIVA and is recorded honestly.
- Bitácora records the restore/rollback actions with no secrets; requests reject
  `database_name`/`storage_key`/file path from the client.
- `pytest tests/test_saas_restore.py`, the full suite, and `git diff --check` pass; no real
  restore is executed.
