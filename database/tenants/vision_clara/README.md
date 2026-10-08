# VISION-CLARA tenant preparation (Paso 7D.1)

This directory contains a read-only preflight and a dry-run plan. The tenant
database `tenant_vision_clara` has not been created by this work.

## Evidence provenance

The facts below were obtained by the parent Paso 7D.1 read-only probe and are
retained as historical execution evidence. This worker did not reconnect to the
database or rerun that probe.

## Known preflight facts

- The historical Paso 7D.1 probe found **27 public base tables** (and 27
  sequences). The integrated application model now requires **28 tenant
  tables/sequences**: the obsolete plural `servicios_oftalmologicos` is not
  provisioned, while `pago` and `pago_detalle` are required together with the
  canonical singular `servicio_oftalmologico`. A fresh read-only inventory
  must confirm this 28-table contract before a future real execution.
- The read-only probe found no public views, materialized views, routines, or
  triggers.
- Source row counts are intentionally generated at execution time by
  `001_source_inventory.sql`; no row contents are stored here.
- The current URL is a Supabase pooler connection to database `postgres`.
  Future `CREATE DATABASE` administration therefore requires an administrative
  direct connection, not this pooler URL.
- The Control Plane mapping for `VISION-CLARA` reports
  `tenant_vision_clara`, with tenant and provisioning states `PENDIENTE`.
- `pg_dump`, `pg_restore`, and `psql` are absent from PATH. No installation is
  attempted by the provisioner.

## Checks by this worker

The worker reviewed the SQL, provisioner, tests, and documentation locally. It
did not execute the database probe, connect to PostgreSQL, or run the future
migration commands. Automated unit tests use fake database and subprocess
collaborators; they must not be read as evidence of a real tenant operation.

## Read-only scripts

Run the scripts with an approved PostgreSQL client and a connection context
that does not put passwords in commands or files. `001` emits only
`table | filas_origen`. `002` reports catalog dependencies from public objects
to external/non-public schemas. `003` requires both `source_database` and
`target_database`; it fails clearly before a target exists and emits paired
`source`/`target` catalogs for tables, columns/types/nullability, PK/FK/UNIQUE,
indexes, routines/triggers, and counts.

## Future 7D.2 migration plan (not executable here)

The future controlled operation is a `public`-only dump/restore, excluding
`saas_control`, `auth`, `storage`, `realtime`, `extensions`, and other managed
schemas. Documented command shape (run only after a separately authorized
database-creation step) is:

```text
pg_dump --format=custom --schema=public --no-owner --no-privileges SOURCE_DB > public.dump
pg_restore --dbname=TARGET_DB --schema=public --no-owner --no-privileges public.dump
```

Do not add passwords, use `TEMPLATE postgres`, or run these commands as part
of Paso 7D.1. `saas_control` remains exclusively in the control-plane
database, never tenant content.

## Provisioner

`scripts/saas/provision_tenant.py --dry-run` reads the Control Plane mapping
for `VISION-CLARA`, requires database `tenant_vision_clara`, and requires both
tenant and provisioning states `PENDIENTE`. It does not accept an arbitrary
database mapping, create/drop databases, restore, or mutate Control Plane
state. Missing client tools produce a controlled status, not installation.
The mutating execution path additionally rejects pooler/Supavisor endpoints;
`CREATE DATABASE` and recovery require a direct administrative PostgreSQL
connection.
