# SaaS 8A — Windows ACL inspector definitive fix

## Objective and scope
Fix only the Windows private-ACL inspector in `app/modules/administracion_saas/backup_storage.py` so a genuinely private directory is accepted and `POST /saas/backups` no longer returns 503, without weakening any security rule or the fail-closed behavior. Remove the temporary diagnostic logging added to `backup_service.py` for PASO 8A. Add a real Windows integration test that exercises the actual PowerShell transport. Work exclusively in `C:\SI2_Proyecto\clinica-oftalmologica-api`. Do not change the real directory ACL, skip the inspector, hardcode `PRIVATE_ACL_OK`, touch Supabase/tenants, run `pg_dump`/restore/automatic backup, or commit/push/merge.

## Problem and rationale
Diagnosis confirmed two defects in the inspector:
1. Transport: `powershell.exe -NoProfile -NonInteractive -Command -` with a multi-line `try/catch` script on stdin does not execute the block on this Windows PowerShell 5.1, returning rc 0 with empty stdout, so the guard raises `ValueError: Backup path ACL is not private or cannot be verified` although the ACL is compliant.
2. Logic: `$ace.AceFlags -band [Security.AccessControl.AceFlags]::...` performs `-band` on enum operands and throws `InvalidCastException` on PowerShell 5.1/.NET.

## Route and checks
Delegated direct route: focused security-critical fix across storage, service and test files, already fully understood from direct reading. TDD: not configured or requested for this ODD feature; use ordinary functional checks. Checks: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q`, `.\.venv\Scripts\python.exe -m pytest -q`, `git diff --check`, plus explicit real Windows test run. Delivery: ask-on-risk; user explicitly prohibits commits, so work units remain uncommitted. Approximate forecast: under 400 authored lines; advisory only.

## Checklist
- [x] FIX-1: Replace `-Command -`/stdin transport with a temp `.ps1` file executed via `-File`, always removed in `finally`; no secrets in the script.
- [x] FIX-2: Cast both operands to `[int]` before `-band` for AceFlags (ContainerInherit, ObjectInherit, InheritOnly) and the other bitwise checks; no implicit enum coercion.
- [x] FIX-3: Remove temporary diagnostic logging in `backup_service.py`; keep fail-closed and sanitized public errors.
- [x] FIX-4: Add real Windows integration tests (accepted private ACL -> PRIVATE_ACL_OK; broad ACL -> rejected) with explicit skip off Windows.
- [x] FIX-5: Maintain/expand unit tests: `-File` transport, multiline script executes, empty stdout/rc!=0/timeout fail closed, AceFlags flags, SID not allowed, unprotected inheritance, valid ACL accepted, pg_dump not executed when ACL fails.
- [x] FIX-6: Run targeted suite, full suite, `git diff --check`, and the explicit real Windows test; report evidence.

## Progress and evidence
FIX-1: `_write_windows_acl_script()` materializes `_WINDOWS_ACL_CHECK` into a `saas-acl-*.ps1` temp file (no secrets) via `mkstemp`; `_run_windows_acl_check()` runs `powershell.exe -NoProfile -NonInteractive -File <script>` (no `-Command -`, no stdin, no `shell=True`) and unlinks the temp file in `finally`. Only `SAAS_BACKUP_ACL_TARGET`/`SAAS_BACKUP_ACL_DIRECTORY` travel via the minimal child environment; the path is never in argv.

FIX-2: Script now uses integer casts before `-band`, e.g. `(([int]$ace.AceFlags -band [int][Security.AccessControl.AceFlags]::ContainerInherit) -ne 0)`, equivalent for ObjectInherit/InheritOnly, and `[int]` casts for the ReparsePoint and AccessMask bitwise checks.

FIX-3: Removed `import logging`, module `logger`, `_log_backup_init_failure`, its call, the `stage` tracking, and the now-unused `sanitize_error` import from `backup_service.py`; `build_backup_dependencies` restored to a clean fail-closed form. Public errors stay sanitized (`HTTPException(503, "Manual backup failed")`); no diagnostic logging remains.

FIX-4/FIX-5: Added `test_windows_acl_transport_uses_temp_file_and_multiline_script`, `test_windows_acl_script_uses_integer_aceflags_comparisons`, real Windows `test_real_windows_inspector_accepts_private_acl` (real transport returns `PRIVATE_ACL_OK`, real inspector accepts, provider constructs) and `test_real_windows_inspector_rejects_broad_acl` (Everyone ACE -> rc 7 -> `ValueError`), both skip off Windows. Updated the mocked inspector test for `-File` transport + temp cleanup.

Security preserved (fail closed): protected inheritance, allowed owner, allow-only ACEs, allowed SIDs only, service inheritable full control, no Everyone/Users/Authenticated Users, no unknown/unverifiable ACE, no reparse point, missing tool/rc!=0/empty stdout/timeout all fail closed. No ACL is mutated by application code.

Observed checks:
- `.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q` => 46 passed.
- Explicit real Windows run `-k "real_windows or transport or integer_aceflags"` => 4 passed, 0 skipped (executed on this Windows host, not mocked).
- `.\.venv\Scripts\python.exe -m pytest -q` => 709 passed, 2 pre-existing short-JWT-key warnings.
- `git diff --check` => exit 0 with pre-existing LF/CRLF warnings only.
- No leftover `saas-acl-*.ps1` in `%TEMP%`.

`POST /saas/backups` was not invoked; no `pg_dump`/restore/automatic backup ran (tests use fake runner/storage); no ACL mutation, `.env` edit, Supabase/tenant change, or commit/push/merge.

## Security rules preserved (fail closed on any doubt)
Inheritance protected; owner allowed; allow-only ACEs; allowed SIDs only; service full control per contract; no Everyone / Users / Authenticated Users; no unknown ACE; no unverifiable ACL; no symlink/reparse point.

## Relevant files
- `app/modules/administracion_saas/backup_storage.py` — inspector (fixed)
- `app/modules/administracion_saas/backup_service.py` — temporary logging removed
- `tests/test_saas_backup.py` — unit + real Windows tests
