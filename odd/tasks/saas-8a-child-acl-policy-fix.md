# SaaS 8A — Child ACL policy fix (private storage root vs. inherited children)

## Objective and scope
Fix only the Windows private-ACL model in `app/modules/administracion_saas/backup_storage.py` so that objects created *inside* an already verified private root are validated with an explicit **CHILD** policy (inherited/unprotected DACL allowed as long as the effective ACL stays exclusively private), while the configured root (`SAAS_BACKUP_PRIVATE_DIR`) keeps the strict **ROOT** policy (protected DACL mandatory). Add real, non-mocked Windows tests for root/child/partial/final, insecure children, boundary/reparse escape, the full local storage cycle, and the exact regression of backup_id=1. Remove the temporary per-stage diagnostic logging from `backup_service.py` again. Work exclusively in `C:\SI2_Proyecto\clinica-oftalmologica-api`. Do NOT change any real directory ACL, relax root security, trust children blindly, apply `icacls` from the app, touch Supabase/tenants, modify backup_id=1, restore, run a real backup/`pg_dump`, or commit/push/merge.

## Problem and rationale
Real incident: first `POST /saas/backups` created `backup_id=1` with `estado=ERROR`, `empresa_id=7`, `tenant_database_id=7`. Failure stage is `storage_staging`, not `pg_dump`.

Root cause: the private root `C:\ProgramData\ClinicaOftalmologica\tenant-backups` has a correctly protected DACL, but NTFS stores inherited ACEs in child descriptors, so staging directories, staged dumps, `.partial` files and the final stored object are **unprotected** (`AreAccessRulesProtected == False`) even though their effective ACL is exclusively private (owner + SYSTEM + Administrators, allow-only, full control). The inspector required `AreAccessRulesProtected == True` for **every** object (script line `if (-not $acl.AreAccessRulesProtected) { throw ... }`), so every child was rejected with `ValueError: Backup path ACL is not private or cannot be verified`.

Verified empirically on this host (create + inspect + remove a temp child; no ACL mutated): root protected=True; child dir/file protected=False with the same 3 allowed SIDs, service full control inherited (CI+OI on the dir, mask 0x1f01ff).

## Fix model
- ROOT: `require_protected_dacl=True` (unchanged strict policy) — root, re-validated before every child.
- CHILD (`staging` dir, `tenant.dump`, `.partial`, final stored object, `get`/`exists`): `require_protected_dacl=False`; the child is still fully inspected for owner, allow-only ACEs, allowed SIDs, allowed rights, no Everyone/Users/Authenticated Users/unknown SID, no reparse point. Fail closed on anything unverifiable.
- Boundary: canonical resolution + containment proof inside the verified root before accepting a CHILD; blocks `..` traversal and symlink/junction escape.

The only difference between ROOT and CHILD is the protected-DACL requirement. Children are still inspected, never trusted blindly.

## Route and checks
Delegated direct route: focused security-critical fix across storage, service and test files, fully understood from direct reading (1 storage file + 1 service file + tests). TDD: not configured/requested for this ODD feature; use ordinary functional checks. Checks: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q`, `.\.venv\Scripts\python.exe -m pytest -q`, `git diff --check`, plus an explicit real Windows test run reporting how many ran without skip. Delivery: ask-on-risk; the user explicitly prohibits commits, so work units remain uncommitted. Approximate forecast: ~120 src + ~300 test authored lines; advisory only.

## Checklist
- [x] T1: Refactor `_inspect_windows_acl` to an explicit `AclPolicy` (ROOT/CHILD) context; script reads `SAAS_BACKUP_ACL_REQUIRE_PROTECTED` (fail-closed: only explicit `0` relaxes protection) and keeps every other check.
- [x] T2: Boundary validation `_resolve_inside_root` (canonical resolve, containment in verified root, no `..` traversal, no symlink/junction escape) wired into children.
- [x] T3: Wire ROOT policy to `__init__`/`_verify_root`; CHILD policy to `staging` dir, `verify_private_archive`, `put` `.partial` + final, `get`, `exists`. Update existing tests for the new signature.
- [x] T4: Real Windows tests: root protected pass; child dir/file/partial/final inherited pass; insecure children fail (Everyone, Users, Authenticated Users, unknown SID); child outside root fail; reparse/junction escape fail; unprotected root fail.
- [x] T5: Real Windows storage E2E: `LocalPrivateBackupStorage(root)` → `staging()` → write → `verify_private_archive` → `put` → `exists` → `get` → `delete`, no `pg_dump`.
- [x] T6: Regression test replicating the incident (root protected=True, child protected=False, private ACL) — rejected before, accepted after.
- [x] T7: Remove the temporary per-stage diagnostic logging from `backup_service.py`; keep sanitized public errors and fail-closed behavior.
- [x] T8: Run targeted suite, full suite, `git diff --check`, and the explicit real Windows run; report evidence.

## Progress and evidence
T1/T2/T3 — `backup_storage.py`: added `AclPolicy.ROOT|CHILD` (`require_protected_dacl=True` only for ROOT); `_WINDOWS_ACL_CHECK` reads `SAAS_BACKUP_ACL_REQUIRE_PROTECTED` (fail-closed: only explicit `0` relaxes protection) and still enforces owner/allow-only/allowed-SIDs/service-inheritable-full-control/no-reparse. Added `_resolve_inside_root` (canonical resolve + normcase containment, blocks `..`/symlink/junction escape). ROOT policy on `__init__`/`_verify_root`; CHILD policy on staging dir, `verify_private_archive`, `.partial`, final, `get`, `exists`.

Critical discovery during real Windows testing: Python's `os.mkdir(path, 0o700)` — what `tempfile.TemporaryDirectory` uses — creates a **protected** child with an `OWNER RIGHTS` ACE (`S-1-3-4`) and **no service SID**, so the old staging dir failed even under CHILD policy. Fixed `staging()` to create the directory with a plain `mkdir` (inherits the private root DACL, `protected=False`) and `shutil.rmtree` cleanup; POSIX keeps 0o700. This matches the task's "mkdir staging → CHILD" model.

T4/T5/T6 — real, non-mocked Windows tests added: root protected pass; inherited child dir/file/partial/final pass; child with Everyone/Users/Authenticated Users/unknown SID fail; child outside root fail; junction escape fail (skips only if junction cannot be created); unprotected root fail; full storage cycle (`staging→verify→put→exists→get→delete`, no `pg_dump`); incident regression (root protected=True, child protected=False, private ACL → ROOT policy rejects, CHILD policy accepts).

T7 — removed `import logging`, module `logger`, `_safe_stage_message`, `_log_stage_failure`, all `stage=` tracking and `import re` from `backup_service.py`; public errors stay sanitized and fail-closed.

Observed checks:
- `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 58 passed.
- Explicit real Windows run `-k "real_windows" -rs` => 14 passed, 0 skipped (real PowerShell 5.1, not mocked).
- `.\.venv\Scripts\python.exe -m pytest -q` => 721 passed, 2 pre-existing short-JWT-key warnings.
- `git diff --check` => exit 0 (pre-existing LF/CRLF warnings only).
- No leftover `saas-acl*.ps1` in `%TEMP%`; real private root has 0 children (untouched).

`POST /saas/backups` was not invoked; no `pg_dump`/restore/automatic backup ran (tests use fakes/SQLite and a fake archive); no ACL was mutated by application code; `backup_id=1` was not touched (no DB write executed) and remains `ERROR`; no Supabase/tenant change; no commit/push/merge.

## Relevant files
- `app/modules/administracion_saas/backup_storage.py` — ROOT/CHILD ACL policy + boundary
- `app/modules/administracion_saas/backup_service.py` — temporary stage logging removed
- `tests/test_saas_backup.py` — updated mocks + real Windows tests
