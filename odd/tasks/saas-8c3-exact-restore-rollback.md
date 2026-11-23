# ODD — PASO 8C.3: Restore in-place exacto y rollback real

## Objetivo
Hacer que el restore in-place garantice que la base queda EXACTAMENTE con los
objetos y datos del backup: eliminar objetos creados después del snapshot, usando
una reconstrucción limpia y guardada de `public`, la MISMA estrategia para el
restore solicitado y para el rollback PRE_RESTORE.

## Problema (causa raíz confirmada)
`pg_restore --clean --if-exists` sólo elimina objetos presentes en el TOC del
archivo. `public.rehearsal_probe_mutation` (creada tras el snapshot) sobrevive al
rollback; el ensayo real falló en `fingerprint_after_rollback` (28 vs 27 tablas).
El fake de tests modelaba mal `--clean` (restauraba el snapshot completo).

## Por qué
Un restore que no garantiza equivalencia exacta deja objetos ajenos y rompe la
confianza del rollback. Sin esto, 8C no es seguro para producción.

## Alcance
- `app/modules/administracion_saas/restore_runner.py` (estrategia exacta + preflight).
- `app/modules/administracion_saas/restore_service.py` (usarla en restore y rollback).
- `app/modules/administracion_saas/restore_rehearsal.py` (usarla en el ensayo).
- `scripts/saas/rehearse_restore.py` (flujo PREPARE/EXECUTE con confirmación real).
- `tests/test_saas_restore.py` (fakes realistas + regresiones).

## Restricciones (seguridad, NO relajar)
- No ejecutar POST /saas/restores, pg_restore real, DROP SCHEMA/DATABASE real, otro ensayo.
- No modificar los 7 tenants, backups 1/2/3, Control Plane ni archivos .env.
- No DROP SCHEMA public CASCADE a ciegas: sólo si el preflight demuestra que no
  afecta extensiones en public ni dependencias externas. Si no, ABORTAR sin mutar.
- Rollback fallido ⇒ tenant nunca ACTIVA.
- No commit/push/merge.
- Conservar `restore_probe_medico_ocular_4b7dbb4d` sin reutilizarla ni dropearla.

## Diseño
1. `TenantRestoreRunner.inspect_public_schema(db) -> PublicSchemaState` (read-only):
   owner, grants, extensiones en public, schemas externos, dependencias externas.
2. `assert_reset_safe(state)`: rechaza si hay extensiones en public o dependencias
   de otros schemas hacia public (`SchemaResetRefused`).
3. `restore_exact(db, archive)`: inspect → assert → `DROP SCHEMA IF EXISTS public CASCADE`
   → `CREATE SCHEMA public` → `restore_into(clean=True)` → re-aplicar owner/grants.
4. `restore_service`: preflight temprano (read-only) antes de sacar al tenant de
   servicio; `_perform_restore` in-place y `_rollback` usan `restore_exact`.
5. `restore_rehearsal`: restore objetivo y rollback usan `restore_exact`.
6. CLI: `--prepare` (id determinista + plan) y `--execute --probe P --confirm P`
   (ambos exactos, DB inexistente, ALLOW=1). `--dry-run` = alias de `--prepare`.

## Tareas
- [x] T1. runner: PublicSchemaState + inspect/assert/restore_exact.
- [x] T2. service: preflight temprano + restore_exact en restore y rollback.
- [x] T3. rehearsal: restore_exact en objetivo y rollback; paso fingerprint_after_mutation.
- [x] T4. CLI PREPARE/EXECUTE con --probe/--confirm obligatorios.
- [x] T5. fakes realistas (modelan --clean) + 10 regresiones.
- [x] T6. Verificación: pytest target, suite completa, git diff --check.

## Estado
- Implementado y verificado (ver sección Verificación).
- Ninguna DB real modificada; ningún ensayo real ejecutado; sin commit/push/merge.

## Verificación (ejecutada)
- `pytest tests/test_saas_restore.py -q` → **77 passed**.
- `pytest -q` → **818 passed, 2 warnings** (warnings JWT preexistentes).
- `git diff --check` → **exit 0** (sólo avisos LF→CRLF de archivos no tocados).
- Verificación independiente read-only: detectó un defecto HIGH preexistente
  (`restore_service` llama `backend.capabilities()`; el runner real sólo tenía
  `probe_capabilities()`). Corregido con `TenantRestoreRunner.capabilities()`
  + regresión `test_restore_runner_exposes_capabilities_contract`.
- Sin operaciones reales de BD; sin commit/push/merge (prohibido por el usuario).
- `restore_probe_medico_ocular_4b7dbb4d`: intacta, no reutilizada, no dropeada.
