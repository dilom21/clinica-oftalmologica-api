# Backend static and dynamic reports — Sprint 2, Step 3

## Objective
Implement the administrative backend reporting module defined by `CONTEXTO_REPORTES_BACK.md` and `MASTER_PROMPT_REPORTES_BACK.md`, without frontend, schema migrations, Supabase changes, or remote operations.

## Problem and rationale
The repository's existing report panel is a stub and does not provide the requested safe dataset catalog, dynamic query engine, static reports, or exports. Deliver the requested report APIs using a strict SQLAlchemy-backed dataset/field registry.

## Scope and constraints
- Implement datasets: `pacientes`, `citas`, `consultas_clinicas`, `diagnosticos`, `usuarios`.
- Static reports: `pacientes_activos`, `citas_por_fecha`, `consultas_clinicas`, `diagnosticos_registrados`, `usuarios_por_rol`.
- Catalog, preview, and XLSX/PDF/CSV export endpoints; preview defaults to 50/max 200, exports max 5000.
- Require valid JWT, Administrador role, and `Generar reportes` / `ACCION_LECTURA`.
- Register only successful export events; do not log report rows or sensitive filters/content.
- Never interpolate client-supplied SQL. Exclude hashes, tokens, secrets, bitácora.
- No frontend, voice, report AI, HTML, email, backup, Supabase, migrations, commit, push, or merge.

## Authorized scope
Only `C:\SI2_Proyecto\clinica-oftalmologica-api`.

## Resolved implementation mode
- Route: delegated direct, because this is a multi-file module and reading prepares the implementation.
- Trigger evidence: five logical datasets, five reports, multiple endpoints/exporters/tests and app router registration.
- TDD: project setting not established; use ordinary functional checks and the exact user-specified pytest commands.
- Delivery strategy: unmanaged; do not commit (explicitly prohibited).
- Skill registry: no Engram registry observation or `.atl/skill-registry.md` was found; proceed without project-specific skill paths.

## Tasks and acceptance criteria
- [x] R1 — Implement registry-backed report catalog, validated SQLAlchemy query construction, previews, static reports, export endpoints, and successful-export audit logging; register router and only missing dependencies.
  - Acceptance: requested APIs and formats function; all SQL identifiers/operators are server-whitelisted; data access is authorization-gated; exports enforce 5000 rows; audit contains no rows/filters.
  - Route: delegated direct. Evidence: module spans multiple non-trivial files; implementation requires model/auth/schema integration mapping.
  - Checks: `\.venv\Scripts\python.exe -m pytest tests/test_gestion_reportes.py -q`; `\.venv\Scripts\python.exe -m pytest -q`.
- [x] R2 — Add tests covering registry allowlists, endpoint authorization, data/filter behavior, exports, and audit behavior.
  - Acceptance: expanded isolated-SQLite endpoint tests cover required positive and negative behavior without weakening existing test infrastructure.
  - Route: delegated direct as part of the single-writer R1 work unit.
  - Checks: same two pytest commands above.

## Progress and verification evidence
- Read the two required report documents completely and mapped local models, auth, audit, app routing, and test fixtures. No blocking incompatibility found.
- Key implementation note: logical report field names need safe aliases/joins to local models whose PKs are named `id`; clinical fields are not automatically safe to expose.
- Tests (final, after active-patient preview correction; independently rerun): `\.venv\Scripts\python.exe -m pytest tests/test_gestion_reportes.py -q` — 15 passed, 2 skipped. `\.venv\Scripts\python.exe -m pytest -q` — 401 passed, 2 skipped.
- Dependencies: `openpyxl` and `reportlab` are declared in `requirements.txt` but absent from this virtual environment. Their real XLSX/PDF integration tests skip explicitly; generation bytes/MIME are not verified here. Mocked export endpoint and audit paths are tested for both formats.
- Coverage: SQLite-backed tests verify auth and permission rejection, catalog/sensitive exclusions, dynamic result selection/filter/order, static column restrictions/active-patient filter, preview defaults/max, export cap, CSV BOM/content/MIME, audit action safety and failed-export no-audit. The implementation uses SQLAlchemy expressions from the registry; client strings are bound filter values, not SQL identifiers/operators.
- Static reports: `pacientes_activos` now enforces estado=true server-side. Other static templates preserve fixed columns and accept validated user filters; no base ordering/filter semantics beyond the patient-active template are defined in the specification.
- Commit: prohibited by the user; none will be created.

## Next step
Optional follow-up: when the required libraries are present in an authorized project environment, rerun the skipped XLSX/PDF binary-output checks. Dependency installation was deliberately not performed in this task.
