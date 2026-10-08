# ODD — PASO 8C.4: Diagnóstico del 2º ensayo fallido + instrumentación segura

## Objetivo
Determinar, con evidencia read-only, en qué etapa exacta falló el 2º ensayo real de
restore, auditar `restore_exact` frente a PostgreSQL real, y preparar instrumentación
segura para que el próximo ensayo reporte la etapa, clase de excepción, SQLSTATE,
returncode y un mensaje saneado — sin repetir el ensayo.

## Resultado real observado
- PREPARE OK → `restore_probe_medico_ocular_9928f6de`.
- EXECUTE (`--backup-id 3 --probe ... --confirm ...`) → `Restore rehearsal failed`.

## Diagnóstico read-only (evidencia)
- Server PostgreSQL 17.6 (Supabase). Usuario `postgres` (NO superuser,
  `member_of_pg_database_owner=True`, `rolcreatedb=True`). Conexión forzada a
  `default_transaction_read_only=on`.
- Sondas existentes: `restore_probe_medico_ocular_4b7dbb4d` (28 tablas, ACL default,
  incluye `rehearsal_probe_mutation`) y `restore_probe_medico_ocular_9928f6de`
  (27 tablas / 27 seq / 77 constraints / 67 índices, idéntico a `tenant_medico_ocular`,
  con datos; **`public_acl = (null)`**).
- Workdir `%TEMP%\saas-restore-rehearsal-cdcfxqi0`: sólo `target.dump` (77898 B); NO hay
  `probe_pre_restore.dump`.
- TOC del dump: TABLE/SEQUENCE/TABLE DATA/SEQUENCE SET/CONSTRAINT/INDEX/FK CONSTRAINT/
  COMMENT; NO contiene entrada `SCHEMA public`; sí una entrada DATABASE informativa.
- `tenant_medico_ocular`: public owner `pg_database_owner`, ACL default, sólo extensión
  `plpgsql` (en `pg_catalog`), 0 dependencias cross-schema → preflight seguro.
- Control Plane: 7/7 tenants `ACTIVA`; `restore_tenant` vacío; backups 1 (ERROR), 2 y 3
  (COMPLETADO) intactos.

### Etapa demostrable
Última etapa completada: `create_probe_database`. Etapa fallida: **`restore_target`**
(`restore_exact` del backup objetivo). `pre_restore` y `rollback` NO se ejecutaron
(no existe `probe_pre_restore.dump`). Evidencia dura de que `restore_exact` no
completó: la sonda quedó con estructura+dato completos pero `apply_public_schema_state`
no llegó a reaplicar grants (`public_acl` NULL; `reset_public_schema` con
`CREATE SCHEMA public;` borra el ACL default y nada lo restauró).

### No recuperable
La excepción original/SQLSTATE/returncode del 2º ensayo NO son recuperables: el CLI
`_run_execute` capturaba `RehearsalExecutionError` (que sí tenía el detalle saneado) y
sólo imprimía la cadena fija `Restore rehearsal failed`, descartando el `__str__`. No
quedó registro persistido (el ensayo no escribe en Control Plane).

## Por qué
Sin la etapa exacta, cada corrección es una hipótesis. Y una divergencia fake/PG real
(ACL/owner de `public`) pasó desapercibida porque los tests no modelan ACL.

## Alcance
- `app/modules/administracion_saas/restore_rehearsal.py` (diagnóstico + etapas).
- `scripts/saas/rehearse_restore.py` (imprimir diagnóstico saneado).
- `tests/test_saas_restore.py` (fake restore_exact realista + 11 tests de diagnóstico).

## Restricciones (seguridad, NO relajar)
- No repetir el ensayo; no ejecutar pg_restore/DROP SCHEMA/DROP DATABASE reales.
- No modificar Supabase, tenants, backups, `.env` ni las dos sondas.
- Saneado obligatorio: sin passwords, hashes, JWT, DATABASE_URL, SQL con parámetros,
  rutas privadas ni traceback con secretos.
- No convertir fallos en éxitos. No commit/push/merge.

## Diseño
1. `RehearsalDiagnostic(stage, exception_class, sqlstate, returncode, message)` con
   `to_dict()` JSON-serializable y `sanitize_diagnostic()` (redacta connection strings,
   `password=`/`DATABASE_URL=`, JWT, hashes hex ≥32 y rutas absolutas).
2. `build_rehearsal_diagnostic(exc, stage, message=None)` recorre la cadena
   `__cause__/__context__` para clase raíz, `sqlstate`/`pgcode`/`orig.sqlstate` y
   `returncode` (p. ej. `CalledProcessError` encadenado por `SubprocessRunner`).
3. `RehearsalExecutionError` ahora lleva `stage` y `diagnostic`.
4. `rehearse_restore` marca `stage` en cada paso y adjunta diagnóstico a todo fallo.
5. CLI `--execute`/`--cleanup`: imprime la línea fija + una línea JSON de diagnóstico
   saneado a stderr; conserva los códigos de salida.

## Tareas
- [x] T1. `RehearsalDiagnostic` + `sanitize_diagnostic` + `build_rehearsal_diagnostic`.
- [x] T2. `RehearsalExecutionError` con `stage`/`diagnostic`.
- [x] T3. `rehearse_restore` con seguimiento de etapa en cada paso.
- [x] T4. CLI: `_report_failure` imprime JSON saneado (execute/cleanup).
- [x] T5. Fake `restore_exact` refleja inspect→assert→reset→restore + 11 tests.
- [x] T6. Verificación: pytest target, suite completa, git diff --check.

## Verificación (ejecutada)
- `pytest tests/test_saas_restore.py -q` → **88 passed**.
- `pytest -q` → **829 passed, 2 warnings** (warnings JWT preexistentes).
- `git diff --check` → exit 0 (sólo avisos LF→CRLF de archivos no tocados). Barrido de
  whitespace de los archivos editados (untracked): limpio.
- Sin operaciones reales de BD; sin ensayo real repetido; sin commit/push/merge.

## Estado
- Instrumentación implementada y verificada. Ningún ensayo real ejecutado.
- Sondas `restore_probe_medico_ocular_4b7dbb4d` y `..._9928f6de` intactas.
- 7/7 tenants ACTIVA; backups 1/2/3 intactos; `restore_tenant` vacío.

## Próximo paso (una sola decisión humana)
Ejecutar el próximo ensayo `--execute` con la instrumentación para obtener la etapa y
el mensaje saneado exactos del fallo de `restore_exact`; luego corregir la causa real
(hoy: `apply_public_schema_state` no completa en PG 17 real) sin hipótesis.
