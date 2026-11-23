# ODD — PASO 8C.2: Ensayo real de restore aislado (sin tocar tenants)

## Objetivo
Preparar un procedimiento reutilizable y sus tests para ensayar la estrategia de
restore in-place de la fase 8C sobre una base PostgreSQL **desechable**
(`restore_probe_*`), demostrando que la recuperación restaura datos reales, sin
modificar ninguno de los 7 tenants, sin tocar el Control Plane y sin ejecutar el
endpoint `POST /saas/restores`.

## Problema
La validación 8C (`POST /saas/restores/validate`) sólo prueba el archivo
(size + SHA-256 + `pg_restore --list`). Faltaba un ensayo end-to-end que demuestre
que un `pg_restore` real recrea estructura **y** datos, y que el mecanismo de
PRE_RESTORE + rollback restaura el estado previo. Ese ensayo no debe arriesgar
ningún tenant productivo.

## Por qué
Backup 3 (AUTOMATICO/COMPLETADO, empresa 7 MEDICO-OCULAR) ya está validado
(`valido=true`, size 77898, SHA-256 OK, `version_schema=v1`). Antes de un restore
real sobre `tenant_medico_ocular` se necesita evidencia de que el motor
in-place + rollback funciona sobre una copia desechable.

## Alcance
- Nuevo módulo backend: `app/modules/administracion_saas/restore_rehearsal.py`.
- Nuevo CLI guardado: `scripts/saas/rehearse_restore.py` (dry-run por defecto).
- Tests nuevos en `tests/test_saas_restore.py` (fakes, sin PostgreSQL real).
- NO se modificó: `restore_service.py`, `restore_runner.py`, `backup_service.py`,
  `backup_storage.py`, migración 007, Control Plane, ni datos clínicos.

## Restricciones (seguridad)
- Nunca operar sobre `tenant_*` ni sobre `postgres/template0/template1/saas_control`.
- Nombre de sonda: `restore_probe_<slug>_<hex8>` (prefijo obligatorio).
- Crear/DROP sólo con confirmación explícita del identificador exacto.
- Sin secretos en argv, logs ni mensajes de error.
- Dry-run por defecto; `--execute` requiere opt-in de entorno.
- Cero mutaciones reales en esta fase (sólo procedimiento + tests).

## Diseño
1. Utilidades puras de nombre: `build_probe_database_name`, `assert_disposable_probe`,
   `validate_probe_creation`, `require_probe_confirmation`.
2. `RehearsalDatabase` (protocolo) + `PostgresRehearsalDatabase` (psql real) para
   introspección/mutación; fake en tests.
3. `fingerprint(db, database)` → tablas, secuencias, constraints, índices, filas
   por tabla, total de filas, presencia de tablas críticas.
4. `rehearse_restore(...)`: confirma → guarda → materializa+verifica (size/SHA) →
   `--list` → crea sonda → restore **in-place (clean=True, igual que producción)** →
   SELECT 1 → verify_structure → fingerprint inicial → PRE_RESTORE (dump de la sonda)
   → mutación simulada → restore del PRE_RESTORE (`clean=True`) → fingerprint final
   == inicial. No hace DROP automático.
5. `cleanup_probe(...)`: terminate + DROP sólo con confirmación exacta.
6. CLI `rehearse_restore.py`: `--dry-run` / `--execute` / `--cleanup`.

## Tareas
- [x] T1. Utilidades de nombre + guardas de seguridad.
- [x] T2. `fingerprint` + `rehearse_restore` con PRE_RESTORE + rollback.
- [x] T3. `cleanup_probe` con confirmación exacta.
- [x] T4. CLI `scripts/saas/rehearse_restore.py` (dry-run default).
- [x] T5. 15 tests requeridos en `tests/test_saas_restore.py`.
- [x] T6. Verificación: pytest target, suite completa, git diff --check.

## Decisión de diseño (compatibilidad)
- El restore objetivo usa `clean=True` para reflejar EXACTAMENTE la estrategia
  in-place seleccionada (`restore_service._perform_restore` → `restore_into(..., clean=True)`),
  evitando además el conflicto de `CREATE SCHEMA public` sobre una DB recién creada.

## Verificación (ejecutada)
- `python -m pytest tests/test_saas_restore.py -q` → **59 passed** (44 existentes + 15 nuevos).
- `python -m pytest -q` → **800 passed, 2 warnings** (warnings preexistentes de JWT).
- `git diff --check` → **exit 0** (sólo avisos informativos LF→CRLF).
- Smoke CLI sin BD: `--cleanup` con confirmación distinta → exit 4; sin args → exit 2.

## Entorno detectado
- Cliente PostgreSQL 18.6 en `C:\Program Files\PostgreSQL\18\bin` (no está en PATH;
  el provisioner lo localiza por `PG_BIN_DIRS` por defecto).
- `SAAS_BACKUP_PRIVATE_DIR` y `SAAS_RESTORE_REHEARSAL_ALLOW` NO definidos en el shell
  actual → el operador debe exportarlos en la misma sesión para `--dry-run`/`--execute`.
- `SAAS_RESTORE_ALLOW_DATABASE_SWAP` NO definido → se ensaya la estrategia in-place.

## Estado
- **NO se creó ninguna base, NO se ejecutó pg_restore real, NO se ejecutó DROP,
  NO se tocó Supabase, NO hubo commit/push/merge.**
- Esperando autorización del usuario para ejecutar el ensayo real.

---

## PASO 8C.2 — Diagnóstico del ensayo real fallido (read-only)

Autorizado por el usuario. NO se repitió el ensayo, NO hubo pg_restore/DROP nuevos,
NO se modificó Supabase, NO se tocó ningún tenant, NO hubo commit/push/merge.

### Evidencia persistida (real)
- Dry-run: `backup_id=3`, `probe_database=restore_probe_medico_ocular_6b83a819`.
- Execute: `restore_probe_medico_ocular_4b7dbb4d` → `Restore rehearsal failed` (exit 3).
- Nombres distintos porque `build_probe_database_name()` (restore_rehearsal.py:60) usa
  `uuid.uuid4()` en cada proceso; dry-run y execute son procesos separados.
- Execute NO usa `--probe`/`--confirm` (sólo `--cleanup`). Requiere `--backup-id` y el
  entorno `SAAS_RESTORE_REHEARSAL_ALLOW=1`; la confirmación interna es automática
  (`confirmation=probe`, rehearse_restore.py:220).
- La sonda `restore_probe_medico_ocular_4b7dbb4d` **EXISTE** (28 tablas, incluida
  `rehearsal_probe_mutation`). `6b83a819` NO existe (dry-run no crea nada).
- Directorio temporal preservado: `%TEMP%\saas-restore-rehearsal-t5ex0h_e\` con
  `target.dump` (77898 bytes = backup_id=3.size_bytes) y `probe_pre_restore.dump`
  (77962 bytes). Ambos: 27 `TABLE public`, SIN `rehearsal_probe_mutation`.
- Sonda: `rol_funcion=39, funcion=19, modulo=6, rol=4, accion=3, usuario=1`,
  resto 0, `rehearsal_probe_mutation=1` (total 73).
- `INBOUND_FK[rol_funcion] = []` (nada referencia la tabla de mayor cardinalidad).

### Causa exacta
El ensayo **sí** ejecutó `pg_restore` (restore objetivo + rollback). La sonda contiene
los 27 objetos restaurados y `rol_funcion` recuperado a 39 ⇒ el rollback
(`restore_into(probe, pre_path, clean=True)`) **tuvo éxito**.

Falla en la comparación final `fingerprint_after_rollback` (restore_rehearsal.py:358):
`_simulate_mutation()` crea `public.rehearsal_probe_mutation` (líneas 264-270). El
`probe_pre_restore.dump` se toma ANTES de la mutación (líneas 332-335), por lo que ese
dump NO contiene la tabla auxiliar. `pg_restore --clean --if-exists` sólo elimina los
objetos presentes en el archivo ⇒ `rehearsal_probe_mutation` **sobrevive** al rollback.
Entonces `after_rollback != before` (28 tablas vs 27) y se lanza
`RehearsalExecutionError("rollback did not restore the original state")`, que el CLI
convierte en `Restore rehearsal failed`. El defecto es determinista: el ensayo no puede
pasar contra PostgreSQL real; los tests pasan sólo porque el fake
(`tests/test_saas_restore.py`, `restore()`) reemplaza el conjunto de tablas por el
snapshot, modelando mal `--clean`.

### Etapa del fallo
Última etapa completada: `rollback`. Etapa que falla: `fingerprint_after_rollback`.
`PLAN_STEPS` no incluye `fingerprint_after_mutation` (restore_rehearsal.py:340), inconsistencia menor.

### Seguridad (verificado read-only)
- 7 tenants presentes y `estado=ACTIVA`; ninguno fue destino de restore.
- Backups 1 (ERROR/MANUAL, evidencia previa), 2 (COMPLETADO/MANUAL/77898) y 3
  (COMPLETADO/AUTOMATICO/77898) intactos, con `sha256` no nulo.
- `saas_control.restore_tenant` vacío (el ensayo no usa el endpoint ni el Control Plane).
- Integridad de datos dentro de `tenant_medico_ocular` NO inspeccionada (no se tocó).

### Corrección propuesta (NO aplicada)
Hacer que el rollback sea auto-consistente: excluir `rehearsal_probe_mutation` de la
comparación de fingerprint, o eliminar/registrar ese marcador antes del rollback
(p. ej. `TRUNCATE`/`DROP` de la tabla marcador antes de restaurar el snapshot, o crear
el marcador ANTES de capturar `before`). Además, alinear el modelo del fake con
`--clean`. El flujo de dos fases PREPARE/EXECUTE con nombre fijo es una mejora de
seguridad de la CLI, pero NO es la causa de este fallo.
