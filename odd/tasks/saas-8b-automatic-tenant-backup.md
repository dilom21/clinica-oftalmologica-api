# SaaS 8B — Automatic per-tenant backup

## Objective and scope
Implement policy-driven automatic tenant backups that reuse the exact validated 8A engine (pg_dump -Fc, SHA-256, pg_restore --list, private storage, metadata, audit, locking). Work only in this repository; preserve all existing dirty work. Do not execute a real automatic backup, restore, touch tenant clinical data, run any scheduler, edit `.env`, expose a public bucket, use `service_role` in Angular, or commit/push/merge. No frontend.

## Problem and rationale
8A provides a real, verified manual backup of MEDICO-OCULAR (backup_id=2, MANUAL, COMPLETADO, CUSTOM, 77898 bytes, SHA-256 verified, `pg_restore --list` OK). Companies now need scheduled, policy-driven fully-automatic backups without a second dump implementation, without duplicating a backup for the same window, without one tenant's failure stopping the rest, and with automatic-only retention that never deletes MANUAL or PRE_RESTORE archives.

## Route and checks
Delegated direct: implementation spans a new SQL migration, ORM metadata, a policy engine, an automatic service, a storage provider factory, a CLI script and the focused test file — more than two non-trivial files. Mapping was done by the orchestrator (4+ files). TDD: not configured or requested for this ODD feature; use ordinary functional checks. Deterministic checks:
- `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q`
- `.\.venv\Scripts\python.exe -m pytest -q`
- `git diff --check`
Approximate forecast: over 600 authored lines across SQL, metadata, engine, script and tests; advisory only, not a cap. Delivery: ask-on-risk, no delivery requested. The user explicitly prohibits commits/push/merge, so all work units remain uncommitted (this overrides the ODD work-unit commit step).

## Checklist
- [x] 8B-1: Add `database/saas_control/006_create_backup_policy.sql` (new table `saas_control.backup_policy`, additive nullable `ventana` anchor on `backup_tenant`, automatic-window partial unique index, REVOKE) without modifying 005, plus the ORM `BackupPolicy` model and `BackupTenant.ventana`.
- [x] 8B-2: Add policy engine (`backup_policy.py`) for frequency/hour/timezone occurrence and window computation, and refactor `backup_service.py` to share one dump engine between MANUAL and AUTOMATICO with typed reservation outcomes.
- [x] 8B-3: Add automatic engine (`backup_automatic.py`): eligibility (empresa/suscripción/tenant), due detection, per-window idempotency, per-tenant EN_PROCESO lock, fail-and-continue, automatic-only retention selection/purge, SaaS audit actions.
- [x] 8B-4: Add private production storage factory with a config-gated dedicated private Supabase provider (never product-images/public) and `scripts/saas/run_automatic_backups.py` with dry-run/execute and documented exit codes.
- [x] 8B-5: Extend `tests/test_saas_backup.py` with the full automatic coverage list, run the three required commands and report results.

## Progress and evidence
Implemented by one delegated writer and verified by the orchestrator. Deliverables: `database/saas_control/006_create_backup_policy.sql` (backup_policy table + additive `backup_tenant.ventana` + `uq_backup_tenant_automatico_ventana` partial unique index keyed on `tipo='AUTOMATICO' AND ventana IS NOT NULL AND estado <> 'ERROR'` + REVOKE; 005 untouched); `app/modules/administracion_saas/backup_policy.py` (DIARIA/SEMANAL/MENSUAL occurrence + window engine); `app/modules/administracion_saas/backup_automatic.py` (eligibility = empresa ACTIVA + suscripción ACTIVA vigente + tenant ACTIVA; due = `proximo_backup <= now`; per-window idempotency; fail-and-continue; automatic-only retention); `app/modules/administracion_saas/backup_storage_factory.py` (local default + fail-closed dedicated-private Supabase provider, inactive by default); `scripts/saas/run_automatic_backups.py` (dry-run/execute, exit codes 0/1/2/3, no password arg); `backup_service.py` refactored so MANUAL and AUTOMATICO share one `_run_backup` (single pg_dump -Fc / SHA-256 / pg_restore --list / storage / cleanup / locking body). Bitácora actions `CREAR_BACKUP_AUTOMATICO` and `PURGAR_BACKUP_AUTOMATICO`. `tests/test_saas_backup.py` extended by 20 tests.

Observed checks on final bytes: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 78 passed; `.\.venv\Scripts\python.exe -m pytest -q` => 741 passed, 2 pre-existing short-JWT-key warnings; `git diff --check` => exit 0 with only pre-existing LF/CRLF warnings. No real automatic backup, no restore, no `.env` edit, no bucket, no commit/push/merge. All work remains uncommitted; pre-existing dirty changes preserved.

Real execution remains pending: apply 006 on the control-plane database, insert a `backup_policy` row per company, provision the private root/provider and `SAAS_BACKUP_*` env, then run the CLI manually. No scheduler was created.

## Acceptance criteria
- tipo `AUTOMATICO`; reuses the 8A `pg_dump -Fc` / SHA-256 / `pg_restore --list` / `BackupStorage` / metadata / cleanup / locking engine with no duplicated dump logic.
- Eligibility excludes suspended companies, inactive or non-vigente subscriptions, and inactive tenants; disabled policies never run.
- One automatic backup per empresa/window; a MANUAL backup never blocks nor is blocked by automatic retention.
- One EN_PROCESO per tenant; distinct tenants are independent; a failing tenant does not stop the others.
- Retention selects only AUTOMATICO COMPLETADO; MANUAL and PRE_RESTORE are never selected or deleted.
- Storage is a dedicated private provider; no public bucket; no secrets in code, argv, logs, or the summary.
- `pytest tests/test_saas_backup.py`, the full suite, and `git diff --check` pass.
