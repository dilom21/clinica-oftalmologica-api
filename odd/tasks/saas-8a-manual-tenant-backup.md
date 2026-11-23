# SaaS 8A — Manual tenant backup

## Objective and scope
Implement on-demand full PostgreSQL tenant backup via `pg_dump -Fc`, with safe metadata, verification, storage, audit, and SaaS Admin APIs. Work only in this API repository; preserve all existing dirty work. Do not execute a real backup, access or mutate tenant clinical data, restore, schedule jobs, edit `.env`, or commit/push/merge. No frontend or public bucket.

## Problem and rationale
The SaaS control plane already resolves active companies to tenant databases but has no traceable verified tenant backup. Select the database only from the control plane; a client-provided database name is never trusted.

## Route and checks
Delegated direct: implementation spans multiple non-trivial SQL, service, API and test files; mapping was delegated because the requested reading covers 4+ files. TDD: not configured or requested for this ODD feature; use ordinary functional checks with `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q`, `.\.venv\Scripts\python.exe -m pytest -q`, and `git diff --check`. Approximate forecast: over 400 authored lines across schema, runner, storage, service and tests; advisory only. Delivery: ask-on-risk, no delivery requested. User explicitly prohibits commits, so work units remain uncommitted.

## Checklist
- [x] 8A-1: Add new idempotent control-plane backup metadata SQL and ORM model with per-tenant in-progress exclusion; verify schema contract without modifying prior migrations.
- [x] 8A-2: Implement private storage abstraction, safe PostgreSQL dump/list runner, and coordinated backup service with hash, error cleanup and SaaS audit; verify with isolated tests.
- [x] 8A-3: Add SaaS Admin-only create/list/detail endpoints, reject client database names and unsafe output, and cover security, failure, concurrent sessions, and listing cases.
- [x] 8A-4: Run requested targeted test, full suite, diff check and report all results and pending manual real-backup procedure.

## Progress and evidence
Implemented without changing earlier SQL or any tenant database. Reservation is committed before dump and protected by a partial unique index, with a separate terminal transaction; failure uses a fixed safe error message and removes staged/stored objects where possible. The local provider requires an explicitly configured private directory and can be replaced through the storage interface. Production configuration is read from `SAAS_BACKUP_PRIVATE_DIR`, optional `SAAS_BACKUP_PG_BIN_DIR`, and optional `SAAS_BACKUP_TIMEOUT_SECONDS` (default 900); no `.env` change was made. PostgreSQL 17/18 client resolution is inherited from the provisioning helper. POST does not accept database_name and never returns a storage key.

Observed checks: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 30 passed; `.\.venv\Scripts\python.exe -m pytest -q` => 692 passed, 2 existing short-JWT-key warnings; `git diff --check` => exit 0 with pre-existing LF/CRLF warnings. Tests use SQLite separate sessions and fake runner/storage; no live PostgreSQL migration or real backup was executed. Before a user-run real backup: apply `database/saas_control/005_create_backup_tenant.sql` on the control-plane database (not a tenant); verify the table and unique partial index read-only; provision a private, access-restricted directory outside any served/static/public path; configure backend process environment; verify pg_dump and pg_restore binaries and administrative read access to the selected tenant; obtain a SaaS Admin JWT; resolve the desired `empresa_id` from `/saas/empresas` (e.g. MEDICO-OCULAR); invoke POST `/saas/backups` with body `{"empresa_id": <id>}`; inspect GET `/saas/backups/{id}` for COMPLETADO, nonzero size and SHA-256. No restore/download/automatic scheduling exists. Operator review is necessary if an unexpected process termination leaves EN_PROCESO or if storage deletion fails. All work remains uncommitted; pre-existing dirty changes were preserved.

## Scoped verifier corrections

The private `storage_key` is now committed with the initial EN_PROCESO reservation, so even an interrupted worker leaves a control-plane pointer to the possible object. On ordinary failure, the service attempts `delete` and checks `exists`. If deletion fails or the object remains, it commits ERROR **with the private key retained** and a fixed cleanup-required message; responses still exclude the key. A successful deletion clears the key. Abnormal process termination deliberately does not release EN_PROCESO: time elapsed alone cannot establish that another worker is not running. The partial unique index continues to exclude overlapping backups until an operator resolves the reservation. Explicit POSIX PostgreSQL binary directories now select executable extensionless clients; Windows lookup still uses `.exe` and existing provisioning defaults.

The local provider is Windows-capable and performs a read-only, fail-closed DACL inspection through the absolute Windows PowerShell 5.1 executable and .NET security descriptor APIs. The checked path is passed through a minimal child environment, never as a command-line argument; application/database secrets are not forwarded. The raw DACL (including inherited ACEs) may contain access-allowed ACEs only for the current service SID, Local System (`S-1-5-18`), and built-in Administrators (`S-1-5-32-544`). The owner must also be one of those SIDs, and the current service SID must have inheritable full control on directories so the process can create and remove private artifacts. Broad principals, deny/object/unknown ACEs, unparseable descriptors, reparse points, unexpected output, timeout, and unavailable inspection all fail closed. The application does not mutate ACLs; an operator must provision the root with the required restrictive DACL. POSIX continues to require a mode with no group/other permission bits.

Staging now uses a random directory **inside the validated private root**, not the generic system temporary directory. On Windows the root, staging directory, staged dump, provider partial file, and final archive are inspected at their security boundaries; the staged file must resolve inside that root. The temporary directory is removed by its context manager on success or handled failure. This implementation is runnable on a correctly provisioned Windows host, but the isolated suite mocks Windows ACL outcomes and therefore does not claim that the target host's future configured directory has already passed a live ACL inspection.

### Operator-only interrupted reservation procedure (not automated; do not run without evidence)

1. Put **every** API instance/worker able to start a backup for this control plane into a maintenance stop; prove all worker processes have stopped on every host. Check PostgreSQL client processes and active database sessions for the specific tenant on every relevant host. `pg_stat_activity` alone is not proof: a dump process can exist outside an observed session. If you cannot prove the worker is gone, **do not change EN_PROCESO**. Record approval and the reviewed backup ID, tenant ID, timestamp, and process inventory in a private incident record.
2. In the control-plane database, read only `SELECT id, empresa_id, tenant_database_id, estado, fecha_inicio, storage_key FROM saas_control.backup_tenant WHERE id = 123;` (replace 123 with the reviewed ID). Inspect the private provider for that exact key; never serve/download it publicly. If it exists, treat it as unverified and preserve it for explicit operator cleanup. If the provider cannot be checked, assume it may exist. Inspect the tenant's mapping and confirm the exact reservation ID; do not run a dump or restore as part of this check.
3. Only after the all-host stop and absence proof, while writes remain disabled, run the following **control-plane-only** transaction, replacing 123 with the reviewed ID. Inspect the `SELECT ... FOR UPDATE` result before executing UPDATE; abort if identity or state differs. The UPDATE deliberately retains the storage key for later private cleanup and releases the unique reservation only for this specifically reviewed row:

```sql
BEGIN;
SELECT id, empresa_id, tenant_database_id, estado, storage_key
FROM saas_control.backup_tenant WHERE id = 123 FOR UPDATE;
UPDATE saas_control.backup_tenant
SET estado = 'ERROR', fecha_fin = now(),
    mensaje_error = 'Interrupted backup; private storage cleanup requires operator review'
WHERE id = 123 AND estado = 'EN_PROCESO'
RETURNING id, empresa_id, tenant_database_id, estado, storage_key;
-- Commit only if the selected and returned row is exactly the approved reservation.
COMMIT;
```

4. If an object remains, delete it **only** after separately verifying the approved key and provider location, and verify absence. Record the disposition privately; clear `storage_key` only after confirmed removal, never because the dump is old. Resume workers only after the metadata and private storage state are reconciled. If the operation completed while you inspected it or any proof is ambiguous, `ROLLBACK` and investigate. This procedure is not an automatic stale-unlock or a restore.

Correction checks (superseding the original counts above): `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 36 passed, 1 skipped (POSIX local-provider case skipped on Windows); `.\.venv\Scripts\python.exe -m pytest -q` => 698 passed, 1 skipped, 2 existing short-JWT-key warnings; `git diff --check` => exit 0, existing LF/CRLF warnings only. Isolated tests exercise retained key on deletion exception/no-op, in-progress key visibility, worker interruption, Windows fail-closed provider, and explicit POSIX binary selection; there was no real dump or live PostgreSQL test.

Windows ACL/staging correction checks supersede earlier counts: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 40 passed; `.\.venv\Scripts\python.exe -m pytest -q` => 702 passed, 2 existing short-JWT-key warnings; `git diff --check` => exit 0 with existing LF/CRLF warnings only. The focused suite covers accepted mocked ACL inspection, broad/unknown/unparseable ACL rejection, unavailable/failed inspector rejection, minimal inspector environment, and root-bound staging cleanup. No ACL is changed by application code, no target Windows ACL was inspected live, and no real backup or live database operation was executed.

## Final subprocess environment correction

`pg_dump` and `pg_restore --list` now receive a newly constructed minimal environment rather than a copy of the application environment. Because both executables are resolved to explicit paths, `PATH` is omitted. Windows receives only available runtime essentials (`SystemRoot`, `WINDIR`, `ComSpec`, `TEMP`, `TMP`); POSIX receives only available locale/temp values (`LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TEMP`, `TMP`). Both then receive only the required PostgreSQL values `PGPASSWORD` and `PGSSLMODE`. `DATABASE_URL`, JWT, SMTP, API keys, and all other unrelated application secrets are excluded for both dump and archive verification. Tests inject those unrelated secrets and assert their absence in both captured child environments.

The Windows ACL inspector also requires `AreAccessRulesProtected`; a directory or file whose DACL still permits inheritance is rejected before the raw ACE allowlist is accepted. This is a read-only check and does not change ACLs. Final verification results below supersede earlier counts.

Final observed checks: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 42 passed; `.\.venv\Scripts\python.exe -m pytest -q` => 704 passed, 2 existing short-JWT-key warnings; `git diff --check` => exit 0 with existing LF/CRLF warnings only. No real backup, live database operation, ACL mutation, `.env` edit, commit, or remote operation was performed.

## PASO 8A diagnosis — authenticated POST /saas/backups returns 503

Observed: authenticated `POST /saas/backups` returns `503 {"detail": "Manual backup service is unavailable"}` while all three `SAAS_BACKUP_*` variables are present and `pg_dump.exe`, `pg_restore.exe` and the private directory resolve to True.

503 origin: `app/modules/administracion_saas/api/router.py:103` (`crear_backup`, `except Exception: raise HTTPException(503, ...) from None`), triggered by `except Exception` at line 102.

Hidden internal exception (reproduced): `ValueError: Backup path ACL is not private or cannot be verified` raised at `app/modules/administracion_saas/backup_storage.py:84` inside `_inspect_windows_acl`. Failing component: `LocalPrivateBackupStorage.__init__` -> `_inspect_windows_acl` (stage `storage` in `build_backup_dependencies`).

Runtime facts: `SAAS_BACKUP_PG_BIN_DIR` read at `backup_service.py:59`, and both `pg_dump` and `pg_restore` were localized by `_client_path`; `SAAS_BACKUP_PRIVATE_DIR` read at `backup_service.py:46`; `SAAS_BACKUP_TIMEOUT_SECONDS=900` parsed. `pg_dump` was not executed: construction fails before the reservation commit and before `runner.dump` (`backup_service.py:130`).

Root cause: two defects in the Windows ACL inspector; the inspected ACL itself is compliant (inheritance protected, owner `S-1-5-32-544` allowed, only allow ACEs for the allowed SIDs, service SID holds inheritable FullControl).
1. Transport: `_inspect_windows_acl` invokes `powershell.exe -NoProfile -NonInteractive -Command -` with the script on stdin (`backup_storage.py:76-80`). On this Windows PowerShell 5.1, stdin execution does not run multi-line block constructs (`try { ... } catch { ... }`), so the process emits no `PRIVATE_ACL_OK` and returns rc 0; the guard at line 83 then raises `ValueError`. Reproduced: the same script via `-Command -` prints nothing, while via `-File` it executes.
2. Latent logic: `$ace.AceFlags -band [Security.AccessControl.AceFlags]::...` (`backup_storage.py:41-43`) throws `InvalidCastException: La conversión especificada no es válida` on this PowerShell 5.1/.NET; via `-File` a directory still fails (rc 7), while a compliant file returns `PRIVATE_ACL_OK`.

Temporary instrumentation added (no behavior change): `_log_backup_init_failure` in `backup_service.py` logs only stage, exception class, optional own code and sanitized message. Verified: `stage=storage exc_class=ValueError exc_code=None message=Backup path ACL is not private or cannot be verified`.

Init/config tests run (9 passed): POSIX `pg_bin_dir` selection/rejection, local storage privacy/traversal, Windows ACL accept/reject/unavailable/failed, ACL-failure-before-dump, missing-config fail-closed, SQL/ORM contract. These mock `subprocess.run`/`_inspect_windows_acl`, so they never exercise the real PowerShell transport.

No fix applied. No backup executed, no Supabase or tenant change, no commit/push/merge.

## PASO 8A — first real backup (post-ACL-fix) diagnosis

Observed: authenticated `POST /saas/backups` for empresa_id 7 (`MEDICO-OCULAR`) returns `503 {"detail": "Manual backup failed"}` (not the earlier "service is unavailable"). So `build_backup_dependencies` succeeded: root private ACL accepted, `pg_dump.exe`/`pg_restore.exe` located, PostgreSQL 18 client present.

Metadata of the failed attempt (control plane, read-only): `backup_tenant.id=1`, `estado=ERROR`, `storage_key=None`, `fecha_fin=2026-10-07T23:34:13.899939Z` (~1.99 s after start), `size_bytes=None`, `sha256=None`, `formato=None`, `nombre_archivo=None`, `version_schema=v1`, `mensaje_error='Manual backup failed'`. Audit row `saas_bitacora.id=13` `CREAR_BACKUP_MANUAL backup_id=1 result=ERROR`. Tenant `MEDICO-OCULAR` remains `ACTIVA`; no tenant data touched.

Exact failing stage: `storage_staging`. `LocalPrivateBackupStorage.staging()` passes `_verify_root()` and creates the random staging child directory, then its child ACL inspection (`backup_storage.py:148` → `_inspect_windows_acl`) raises `ValueError("Backup path ACL is not private or cannot be verified")`. `pg_dump` is never reached (`runner.dump` at `backup_service.py:167` is not executed).

Root cause: the inspector requires `$acl.AreAccessRulesProtected` (SE_DACL_PROTECTED) on every inspected object, including artifacts created under the root. A freshly created child directory/file inherits the root's ACEs but its DACL is **not** protected, so every staging/child/file/parking check fails closed. Confirmed empirically on this host: a replica protected root passes (`PASS`) while a child directory created inside it fails (`FAIL`), and so do the staged file and the provider partial/final file. This is not pg_dump, not credentials, not timeout, not storage write: it is the Windows private-storage ACL gate applied to inherited child objects. Classification: F (storage), sub-cause storage ACL gate.

Consequence: with the current code the manual backup cannot succeed on Windows even though the root is compliant; after the staging gate, `verify_private_archive`, `put` partial-file check and destination check would fail identically until the ACL gate is reconciled with inherited child DACLs.

Temporary sanitized instrumentation added (no behavior change): `_log_stage_failure`/`_safe_stage_message` in `backup_service.py` log only `stage=`, `exc_class=`, `returncode=` (from `exc.__cause__`) and a sanitized `message=` via `logger.error` (no traceback, no URLs/credentials/hosts/private paths). Verified: `stage=storage_staging exc_class=ValueError returncode=None message=Backup path ACL is not private or cannot be verified`; secret-containing messages are redacted. Focused suite `tests/test_saas_backup.py` => 46 passed.

No fix applied. No real backup executed (only an isolated SQLite reproduction of the failing stage with a fake runner/storage, and read-only control-plane SELECTs). No Supabase/tenant mutation, no commit/push/merge.

