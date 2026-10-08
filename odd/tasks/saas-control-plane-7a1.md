# ODD Task — SaaS Control Plane 7A.1

## Objective
Create reviewable, non-executed PostgreSQL scripts for the `saas_control` backend-only control plane using the database-per-tenant architecture.

## Authorized scope
- `database/saas_control/001_create_control_plane.sql`
- `database/saas_control/002_seed_demo_control_plane.sql`
- `database/saas_control/003_verify_control_plane.sql`
- `database/saas_control/004_rollback_control_plane.sql`

No backend, frontend, mobile, environment, or Supabase changes.

## Checklist
- [x] T1 — Create schema, seven tables, constraints, indexes, and role security revokes.
- [x] T2 — Add idempotent demo seed for three plans, seven companies, subscriptions, tenant metadata, and provisioning records.
- [x] T3 — Add read-only verification queries and destructive rollback warning/script.
- [x] T4 — Run `git diff --check` and inspect the final local diff/status.

## Acceptance criteria
- All requested states, foreign keys, checks, uniqueness rules, and indexes are present.
- Seed is safe to run twice without duplicate equivalent records and stores no secrets.
- Verification is read-only and reports the requested counts, join, and role privileges.
- Rollback is not executed.

## Checks
- `git diff --check`
- Local structural readback of all four SQL files.

## Route
Delegated direct writer: four non-trivial SQL files require a bounded writer.

## Progress evidence
- Writer read back all four SQL files and reported no remote SQL execution.
- `gentle-ai review mode status`: off by default.
- `git diff --check`: passed; existing unrelated worktree changes remain untouched.
