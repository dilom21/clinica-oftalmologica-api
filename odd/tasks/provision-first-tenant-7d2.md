# PASO 7D.2 — Provision the first physical tenant

## Reconcile-existing correction — real evidence complete

### Checklist

- [x] Refactor the structural snapshot to use semantic PK, UNIQUE, FK, CHECK,
  column-nullability, and index fingerprints; constraint names and NOT NULL
  OIDs are excluded from business identity.
- [x] Add exclusive `--reconcile-existing --empresa VISION-CLARA --confirm
  tenant_vision_clara`; the path verifies only ERROR/ERROR and never starts,
  creates, dumps, restores, drops, or retries a tenant.
- [x] Keep activation behind six explicit read-only verification stages,
  dispose the existing tenant engine, run SELECT 1, and compare only
  `usuario`, `paciente`, and `cita` row counts without PII.
- [x] Add deterministic fake-based regressions for semantic constraints,
  nullable/schema/count/health/index gates, exact confirmation, state guards,
  physical absence, no-provisioning calls, activation, and attempt invariance.
- [x] Run `--reconcile-existing` against the Control Plane only after the
  focused suite, full suite, and read-only structural audit passed.
- [x] Capture real source/target snapshots and activation evidence under the
  separately authorized operational run; all verification flags were TRUE.

### Local evidence for this correction

- Focused tests and static checks are safe local evidence only; no real
  reconciliation, Control Plane mutation, dump, restore, create, or drop was
  executed before the separately authorized operational run.
- The existing historical recovery evidence below is preserved as history and
  is not evidence that this new reconciliation path has been run.

### Real reconciliation evidence — PASO 7D.2V

- Focused pytest: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_tenant_provisioning.py -q` — **78 passed**.
- Full pytest: `.\\.venv\\Scripts\\python.exe -m pytest -q` — **582 passed, 2 warnings**.
- `git diff --check` — passed; only pre-existing LF/CRLF warnings were shown.
- Read-only audit against `postgres.public` and `tenant_vision_clara.public`:
  `tables_ok=true`, `sequences_ok=true`, `columns_ok=true`,
  `nullable_ok=true`, `constraints_ok=true`, `indexes_ok=true`,
  `counts_ok=true`, `schemas_ok=true`, `health_ok=true`.
- Exact constraint differences before the final generic normalization were
  only redundant `::text::text` casts in three business CHECK definitions;
  no table, column, operator, or allowed-value mismatch was found.
- Authorized command completed with six reconciliation stages and activated
  the existing target; no create, dump, restore, drop, or `--execute` path
  was invoked.
- Post-activation: `tenant_database=ACTIVA`,
  `provisionamiento=COMPLETADO`, `version_schema=v1`,
  `TenantResolver("VISION-CLARA")=OK`, `TenantEngineRegistry=OK`,
  `check_tenant_connection=OK`.
- Post-activation counts (without PII): `usuario=14`, `paciente=10`,
  `cita=8`. Existing `intentos` value was preserved by the reconcile path.

## Recovery record — Paso 7D.2R (controlled implementation)

This document records the controlled recovery implementation after the
authorized attempt left `tenant_vision_clara` physically present but incomplete
and both Control Plane states at `ERROR`. The historical 7D.2 record below is
preserved; this recovery scope adds a separately gated administrative cleanup
path.

### Audit correction — fail-closed session handling and FORCE recovery

The real read-only audit STOP condition found one target session in
`tenant_vision_clara`: an idle Supavisor session. The recovery implementation
now reports the complete activity identity and transaction fields and classifies
that one session as residual only when it is exactly Supavisor, a client
backend, idle, and has no active transaction (`xact_start IS NULL`). Active,
idle-in-transaction, unknown-application, extra, or unclassifiable sessions
remain STOP conditions. The sole Supavisor session is never manually
terminated; PostgreSQL `DROP DATABASE ... WITH (FORCE)` is used instead.

The authorized recovery later observed zero target sessions at the final
pre-drop audit, then completed the FORCE DROP and Control Plane reset. No
provisioning, dump, restore, or manual session termination was executed.

### Authorized recovery scope

- Require the exact `--empresa VISION-CLARA` selector in dry-run, execute, and
  recovery modes; reject every other company code.
- Implement only `--recover-error --confirm tenant_vision_clara` for the
  Control Plane mapping for `VISION-CLARA`.
- Recovery is valid only for the exact mapping database name
  `tenant_vision_clara`, with `tenant_database.estado = ERROR` and
  `provisionamiento_tenant.estado = ERROR`.
- The target name comes only from the Control Plane and is validated as a safe
  PostgreSQL identifier. No CLI database-name input is accepted for recovery.
- The implementation may audit, dispose the local tenant engine, and issue a
  target-only `DROP DATABASE ... WITH (FORCE)` against `postgres` in autocommit;
  it must verify the target is not the current database or a template and must
verify target absence before the prescribed Control Plane reset.

### Recovery checklist (stable)

- [x] Read-only implementation audit confirms exact mapping, states, target
  existence, count, user, application, state, backend, transaction/query
  timestamps, source, and target name without exposing secrets; unknown
  sessions fail closed.
- [x] Recovery requires both explicit mode and exact confirmation; invalid
  invocations perform zero DROP, UPDATE, or other mutations.
- [x] Local tenant engine is disposed before DROP; the sole Supavisor session
  is never manually terminated.
- [x] DROP uses only the Control Plane target, validates the exact tenant,
  rejects reserved/template/current targets, runs against `postgres` in
  autocommit with `WITH (FORCE)`, and verifies absence before reset.
- [x] Failed DROP or failed absence verification leaves `ERROR/ERROR`; a
  successful DROP resets only the prescribed fields to `PENDIENTE` and
  `RECUPERACION_COMPLETADA`, preserving `intentos`.
- [x] Provisioning retains a configurable positive command timeout, visible
  `[1/10]` through `[10/10]` progress, strict 27-table/27-sequence and
  structural gates, and fake-based coverage.

### Evidence status

- The correction's focused tests, full suite, compile check, and
  `git diff --check` are recorded below.
- Final pre-drop audit: `count_total=0`; no target sessions were present.
- Recovery completed successfully with `DROP DATABASE tenant_vision_clara WITH
  (FORCE)` against `postgres`; no manual `pg_terminate_backend` was used.
- Post-drop verification: `tenant_vision_clara exists = False`.
- Post-recovery Control Plane: `tenant_database=PENDIENTE`,
  `provisionamiento=PENDIENTE`, `intentos=1`.

## Objective

Implement a guarded execution path for provisioning only `tenant_vision_clara` from the Control Plane mapping for `VISION-CLARA`. Preserve the existing read-only `--dry-run` path and make every database and Control Plane mutation require explicit `--execute` plus `--confirm tenant_vision_clara`.

## Authorized scope

- Repository: `C:\SI2_Proyecto\clinica-oftalmologica-api`.
- Modify only `scripts/saas/provision_tenant.py`, `tests/test_tenant_provisioning.py`, and this task document.
- Read the Control Plane mapping, validate the exact database name and pending states, and provision only the first tenant when all prechecks pass.
- Use `pg_dump`, `pg_restore`, and optional `psql` through a configurable binary directory without changing global PATH.
- Verify public-only structure, counts, prohibited schemas, SQL health, and structural fingerprints for columns/types, PK/FK/UNIQUE constraints, and indexes through injectable fakes in unit tests.
- Keep temporary dumps outside the repository and remove them in a `finally` path after creation, including post-creation failures.

## Restrictions

- Do not execute `CREATE DATABASE`, restore, Control Plane mutation, or Control Plane provisioning in this task.
- Do not provision any real tenant, run destructive tests, or use real secrets.
- Do not implement `DROP DATABASE` or automatic retry/cleanup of a partially created database.
- Do not modify PATH, `.env`, legacy login, clinical routers, frontend, mobile code, or other tenants.
- Reject missing `--execute`/confirmation, incorrect confirmation, an existing database, non-`PENDIENTE` state, and any database name not supplied by the Control Plane.
- Never place passwords in argv, print `PGPASSWORD`, print full URLs, or persist secrets.
- Do not use `TEMPLATE postgres`; if a template is needed it may only be `template0`.
- Do not commit, push, merge, or mutate the Control Plane in this task.

## Stable checklist

- [x] Read the required 7D.2 context, master prompt, tenant README, provisioner, focused tests, real resolver/registry dependencies, and prior task patterns.
- [x] Preserve read-only `--dry-run` behavior and require exact
  `--empresa VISION-CLARA` input in every CLI mode.
- [x] Add explicit `--execute`, strong confirmation, and `--pg-bin-dir` override.
- [x] Resolve PostgreSQL binaries in order: PATH, PostgreSQL 18, 17, then 16; explicit `--pg-bin-dir` overrides discovery without PATH mutation.
- [x] Run all read-only prechecks before setting `PROVISIONANDO`/`EN_PROCESO`.
- [x] Set `ERROR` states on post-start failure and `ACTIVA`/`COMPLETADO` only after every verification succeeds.
- [x] Dump only `public`, restore only to the Control Plane target, and prevent `TEMPLATE postgres`/automatic DROP.
- [x] Capture a source structural fingerprint before the dump and compare target columns/types, PK/FK/UNIQUE constraints, indexes, and public object counts after restore.
- [x] Audit only external/prohibited dependencies from public objects; do not count normal dependencies merely because their referenced namespace is public.
- [x] Cover all requested safety, subprocess, structural verification, and cleanup-on-failure cases with mocks/fakes only.
- [x] Run only the focused test file if safe; report dependency failures exactly.
- [x] Update this document and its complete Engram mirror after implementation and verification.

## Checks

- Focused: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_tenant_provisioning.py -q`.
- Static: `.\\.venv\\Scripts\\python.exe -m compileall scripts/saas/provision_tenant.py tests/test_tenant_provisioning.py`.
- Hygiene: `git diff --check`.
- Safety review: no real database command, Control Plane mutation, destructive test, secret, dump, or global PATH change.

## Delegated-direct route

- Route: `T7D2-01 — delegated direct writer`.
- Rationale: the change coordinates an executable provisioning state machine, subprocess credential boundaries, database verification gates, resolver/registry integration, and focused safety tests.
- Implementation route: injectable reader, database gateway, command runner, verifier, and tenant health-check collaborators; production wiring remains explicit and gated.
- Verification route: focused pytest with fakes/mocks, static source inspection, and safe CLI dry-run only.

## Gate 0 evidence

- Verified before code modification by the task owner:
  - `C:\Program Files\PostgreSQL\18\bin\pg_dump.exe` — PostgreSQL 18.6.
  - `C:\Program Files\PostgreSQL\18\bin\pg_restore.exe` — PostgreSQL 18.6.
  - `C:\Program Files\PostgreSQL\18\bin\psql.exe` — PostgreSQL 18.6.
- No global PATH modification was made.
- Gate 0 is satisfied for implementation and remains a prerequisite for any separately authorized real execution.

## Progress

- [x] T7D2-00 read the required context/master prompt, tenant README, provisioner, focused tests, and prior task pattern.
- [x] T7D2-00 record Gate 0 evidence and authorized restrictions.
- [x] T7D2-01 inspect real Control Plane, resolver, registry, and database session contracts.
- [x] T7D2-02 implement guarded execute path and binary discovery.
- [x] T7D2-03 extend focused fake-based safety coverage.
- [x] T7D2-04 run safe focused checks and reconcile evidence.
- [x] T7D2-05 correct dump cleanup, structural verification, dependency auditing, and sanitized failure handling.
- [x] T7D2-06 run real read-only Gate 1 and capture the 27-table source snapshot.
- [x] T7D2-07 execute the single authorized provisioning attempt; reconcile timeout as partial target and mark Control Plane ERROR.
- [x] T7D2-08 remove the orphaned temporary dump and correct the target-only `pg_restore` argument construction.
- [x] T7D2R-01 update this record before implementation and preserve the complete 7D.2 history.
- [x] T7D2R-02 implement explicit ERROR-only recovery, target validation, session cleanup, autocommit DROP, and post-DROP reset.
- [x] T7D2R-03 add command timeouts, injectable ten-step progress, strict structure/count gates, and compatible fake coverage.
- [x] T7D2R-04 run focused/full tests, compile check, and whitespace audit without real provisioning or recovery.
- [x] T7D2R-05 correct RecoveryAudit to report pg_stat_activity transaction fields, classify only the exact idle/no-transaction Supavisor session, and use FORCE without manual termination.
- [x] T7D2R-06 execute the authorized recovery after the final session audit and verify physical absence, Control Plane reset, preserved attempts, and post-recovery dry-run.

## Verification evidence

- Focused pytest: `.\\.venv\\Scripts\\python.exe -m pytest tests/test_tenant_provisioning.py -q` — **28 passed**.
- Full pytest: `.\\.venv\\Scripts\\python.exe -m pytest -q` — **532 passed, 2 warnings**.
- Compile check: `.\\.venv\\Scripts\\python.exe -m compileall scripts/saas/provision_tenant.py tests/test_tenant_provisioning.py` — passed.
- `git diff --check` — passed; Git reported only pre-existing line-ending normalization warnings in unrelated files.
- Coverage includes dry-run immutability, explicit execute/confirmation, Control Plane-derived target, pending-state and existing-DB guards, precheck ordering, state transitions, sanitized failure handling, public-only dump, target-only restore, secret boundaries, binary override/order, prohibited template/drop checks, count/schema/health gates, structural mismatch gates, and dump cleanup after dump/restore/verify/health failures.
- Real Gate 1 read-only snapshot: `VISION-CLARA`, `tenant_vision_clara`, both initial states `PENDIENTE`, `rolcreatedb=true`, dependency audit passed, physical target absent before execution, and 27 source tables captured.
- Source snapshot counts: `accion=3`, `antecedente_clinico=0`, `bitacora=436`, `bloqueo_horario=3`, `cita=8`, `consulta_clinica=2`, `control_medico=1`, `detalle_receta=1`, `diagnostico=1`, `examen_oftalmologico=3`, `funcion=19`, `historial_clinico=10`, `horario_oftalmologo=11`, `indicacion=4`, `modulo=6`, `oftalmologo=1`, `paciente=10`, `receta=1`, `resultado_examen=2`, `rol=4`, `rol_funcion=39`, `servicio_oftalmologico=2`, `servicio_realizado=2`, `servicios_oftalmologicos=2`, `token_recuperacion=17`, `tratamiento=1`, `usuario=14`.
- The authorized execution exceeded the shell timeout. Reconciliation found the target physically present but incomplete: counts/tables/sequences, columns and prohibited-schema check passed; constraints and indexes did not. Health check passed, but activation was correctly withheld. Control Plane was marked `ERROR/ERROR` with a sanitized message; no automatic DROP or retry was performed.
- An orphaned temporary dump outside the repository was found and removed. No dump was committed or moved into the repository.

### Paso 7D.2R implementation evidence

- Recovery is exposed only through `--recover-error --confirm tenant_vision_clara`;
  invalid mode, confirmation, target, or states fail before any mutation.
- The CLI requires `--empresa VISION-CLARA` and rejects other company codes;
  Control Plane reads remain hardcoded to the expected code rather than using
  the CLI value as a lookup selector.
- Recovery validates the Control Plane-derived target, audits existence/sessions/source,
  disposes the injected local engine registry, never manually terminates the
  Supavisor session, performs target-only FORCE DROP against `postgres` in a
  fresh autocommit `psql` command, verifies absence, and calls the reset writer
  only after DROP succeeds. Reset does not change `intentos`.
- Provisioning retains `--dry-run` and `--execute`, adds positive
  `--command-timeout-seconds` (default 900), and emits injectable ten-step progress
  without credentials, URLs, or connection strings.
- Snapshot verification now includes CHECK constraints in addition to columns/types/
  nullable, PK/FK/UNIQUE constraints, indexes, public object counts, and exact
  source table/count coverage; activation remains gated on every verification.
- Focused pytest: `.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q` — **40 passed**.
- Full pytest: `.\.venv\Scripts\python.exe -m pytest -q` — **544 passed, 2 warnings**.
- Compile check: `.\.venv\Scripts\python.exe -m compileall scripts/saas/provision_tenant.py tests/test_tenant_provisioning.py` — passed.
- `git diff --check -- scripts/saas/provision_tenant.py tests/test_tenant_provisioning.py odd/tasks/provision-first-tenant-7d2.md` — passed.
- Real recovery evidence: `[1/10] Recovery audit: count_total=0; no target sessions`,
  followed by engine disposal, FORCE DROP, absence verification, and reset.
  No provisioning, dump, restore, manual session termination, commit, push, merge,
  frontend/mobile/.env change, or unrelated file edit was performed.
- CLI coverage verifies that every mode requires exact `--empresa VISION-CLARA`;
  alternate company values are rejected by argparse before component construction.
- Correction evidence: the earlier audit STOP found one idle Supavisor session
  for `tenant_vision_clara`; the final pre-drop audit re-queried
  `pg_stat_activity` and found zero sessions. The implementation reports user,
  application, state, backend, `xact_start`, and `query_start`, accepts one
  residual Supavisor session only when it is client-backend idle with
  `xact_start` NULL, and rejects active, idle-in-transaction, unknown, extra,
  or unclassifiable activity.

## Commit identity

No commit was created. Existing unrelated worktree modifications were preserved and not staged.

## Recovery implementation boundary

Only `scripts/saas/provision_tenant.py`, `tests/test_tenant_provisioning.py`, and
this document were authorized and changed for 7D.2R. The coordinator must perform
any real read-only audit and eventual recovery separately after reviewing these
tests and the resulting evidence; this implementation intentionally does not run
either operation.
