# PASO 7D.1 — Preparar primer tenant físico

## Objective

Preparar una auditoría reproducible y un dry-run seguro para el primer tenant físico VISION-CLARA, sin crear `tenant_vision_clara`, restaurar datos ni cambiar el Control Plane.

## Authorized scope

- Repository: `C:\SI2_Proyecto\clinica-oftalmologica-api`
- Create the read-only SQL preflight/verification scripts, dry-run provisioner, focused tests, and tenant preparation README.
- Inspect the current PostgreSQL source only with read-only queries and sanitize all connection reporting.
- Use only the `public` business schema for the future migration plan; exclude Supabase-managed schemas and `saas_control`.
- Do not create/drop databases, restore dumps, change Control Plane state, modify source data, `.env`, auth/JWT, routers, frontend, mobile, or remote repositories.
- Do not commit, push, or merge.

## Route and checks

- Task T7D1-01 — delegated direct writer: implement the SQL audit artifacts, dry-run provisioner, focused tests, and README. Route evidence: multiple non-trivial files plus SQL/runtime contract coordination.
  - Acceptance: read-only SQL, Control Plane-derived mapping, `PENDIENTE` guard, no secret output, no `TEMPLATE postgres`, explicit public-only migration plan, controlled missing-tool failures.
  - Checks: focused pytest, full pytest, `git diff --check`, dry-run, and read-only source inventory evidence.

## Progress

- [x] T7D1-00 read complete context/master prompt and inspect local repository
- [x] T7D1-00 perform sanitized read-only source probe and tool availability check
- [x] T7D1-01 implement preparation artifacts and dry-run tests
- [x] T7D1-02 verify focused/full checks and reconcile final report

## Parent-probe evidence retained

- The parent probe reached the source through the current Supabase pooler URL using a read-only transaction and printed no credentials.
- Real public inventory contains 27 tables and 27 sequences, no public views/materialized views/routines/triggers in the probe.
- Source counts and `VISION-CLARA` metadata were read without mutation; Control Plane reports `tenant_vision_clara` and both tenant/provisioning states `PENDIENTE`.
- Local `pg_dump`, `pg_restore`, and `psql` executables are absent from PATH.

This worker did not reconnect to PostgreSQL or rerun the parent probe; the
items above are retained as prior execution evidence, not new checks here.

## Prior implementation evidence

- Added the three read-only SQL artifacts, tenant README, injectable dry-run
  provisioner, and focused pytest coverage.
- Provisioner reads the VISION-CLARA mapping from the Control Plane, validates
  the exact database name and both `PENDIENTE` states, and supports no mode
  other than `--dry-run`.
- Earlier implementation checks: focused 9 passed; full suite 513 passed; 2 existing JWT key-length warnings.
- `git diff --check`: passed for the current worktree changes.
- No database creation, restore, Control Plane mutation, source-data mutation,
  `.env` change, or secret output was performed.

These results belong to the earlier implementation run and were not executed
by this worker unless separately reported by the caller.

## Latest verification

- Fixed direct filesystem execution of `scripts/saas/provision_tenant.py`: the
  entrypoint derives the repository root from `__file__` and prepends it to
  `sys.path` before importing `app.database.session`.
- Added a regression test for the path bootstrap. The existing reader remains
  injectable and SELECT-only.
- Focused pytest: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_tenant_provisioning.py -q` — 11 passed.
- CLI dry-run: `.\\.venv\\Scripts\\python.exe scripts/saas/provision_tenant.py --dry-run` — passed; mapping validated for `VISION-CLARA`, planned database `tenant_vision_clara`, and missing local PostgreSQL client tools reported without failing.
- `git diff --check`: passed; Git emitted only existing line-ending normalization warnings for unrelated modified files.
- No database probe, database connection outside the existing dry-run read,
  mutation, restore, `.env` change, or secret output was performed.

## Final verification

- Focused pytest after corrections: 11 passed.
- Full suite after corrections: 515 passed, 2 existing JWT key-length warnings.
- Direct CLI dry-run passed and reported the Control Plane mapping, public-only scope, exclusions, and missing PostgreSQL client tools without secrets.
- `git diff --check` passed.

## Next step

7D.1 is prepared. Any database creation or restore belongs to a separately
authorized 7D.2 operation and must not be inferred from this dry-run.
