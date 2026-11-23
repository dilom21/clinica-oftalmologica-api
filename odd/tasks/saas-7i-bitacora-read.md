# PASO 7I SaaS audit-log read correction

## Objective and problem
Diagnose the real GET `/saas/bitacora` failure and return a valid, safe collection of existing audit entries. The browser reports CORS, but other SaaS endpoints work; do not assume a global CORS defect.

## Scope and constraints
- Work only in this API repository and the SaaS administration read endpoint plus its tests.
- Preserve existing database data, tenant/clinical code, security, and CORS unless direct evidence identifies a specific CORS misconfiguration.
- No frontend, backup/restore, realtime, commit, push, or merge. Existing unrelated worktree changes must be preserved.
- Direct delegated writer (router/schema/repository/tests span multiple non-trivial files); no project skill matches this backend fix.
- TDD mode: not established by project/session configuration; use ordinary functional checks via `.\.venv\Scripts\python.exe -m pytest`.
- Delivery: unmanaged; user explicitly forbids commits and PR work. Estimated authored changes: under 400 lines.

## Tasks
- [x] T1 Reproduce real HTTP status and sanitized traceback using safe local TestClient or read-only request, inspect DB/ORM/response contracts and distinguish error from CORS. Check: observed status and isolated reproduction.
- [x] T2 Correct only the demonstrated failure; preserve nullable/user/date/IP semantics and prevent secret disclosure. Check: isolated response regressions including login, JWT rejection, and frontend Origin.
- [x] T3 Run targeted pytest, full pytest, `git diff --check`, inspect final status and report limitations honestly.

## Acceptance and progress
- `GET /saas/bitacora` with SaaS JWT yields 200 and valid sanitized collection, including `LOGIN_SAAS` when rows exist.
- Tenant and legacy JWT rejected; IPv4/IPv6/date/nullable user values serialize.
- Required checks: `.\.venv\Scripts\python.exe -m pytest tests/test_saas_admin_backend.py -q`; `.\.venv\Scripts\python.exe -m pytest -q`; `git diff --check`.
- T1 verified in isolated SQLite/TestClient fixture with PostgreSQL-style native `ipaddress` values: HTTP 500 with `raise_server_exceptions=False`; with exception propagation, FastAPI `ResponseValidationError` at `response[0..2].ip` (`string_type`, IPv6Address/IPv4Address supplied where `str` expected). The SQLite fixture ordinarily returns strings, so previous empty-list success was not a native-INET read check. Live PostgreSQL/backend status was not observed. Actual DDL uses nullable `saas_usuario_id`, non-null TIMESTAMPTZ, and INET; repository selects the audit entity without JOIN and orders by descending id. Allowed frontend Origin is already configured; no CORS change justified.
- T2 verified by isolated focused tests (2 passed): convert only native IPv4/IPv6 address objects to text for response validation; keep existing string/None handling. Audit descriptions are displayed with the existing credential/URL redactor rather than raw text. This is best-effort pattern redaction, not a guarantee for arbitrary legacy free text; no database records are rewritten. HTTP collection includes LOGIN_SAAS, descending IDs, nullable user/IP, ISO timestamp and configured Origin header; tenant/legacy JWTs return 401.
- T3 verified: targeted suite 18 passed; full suite 632 passed, 2 pre-existing short-HMAC-key warnings from tenant tests; `git diff --check` exit 0 with line-ending warnings in unrelated already-modified tracked files. No live backend or PostgreSQL read was performed. Worktree contains unrelated changes and remains uncommitted.
