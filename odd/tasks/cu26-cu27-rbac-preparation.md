# ODD — Preparación RBAC CU26/CU27

## Objetivo
Preparar, sin ejecutar contra bases reales, el seed RBAC y la verificación read-only para registrar las funciones de CU26 y CU27 en los siete tenants de `dev-josias`.

## Alcance autorizado
- Crear `database/tenant_rbac/20261102_seed_cu26_cu27.sql` idempotente, transaccional y fail-closed.
- Crear `scripts/saas/preflight_rbac_cu26_cu27.py` con consultas read-only al plano de control y tenants.
- Crear `tests/test_cu26_cu27_rbac.py` para seed, idempotencia, conflictos, rollback y menú dinámico.
- Auditar `requerir_permiso()` y proponer mejora compatible si la resolución por nombre permite homónimos.
- No aplicar el seed a tenants reales, no modificar routers CU26/CU27, esquema, commits, push ni merge.

## Restricciones y checks
- Preservar cambios/untracked preexistentes.
- Pruebas sólo locales/desechables o unitarias sin URLs reales.
- Verificar que el seed no cambia funciones/permisos ajenos.
- Ejecutar `pytest tests/test_cu26_cu27_rbac.py` y checks estáticos/read-only pertinentes.

## Tareas
- [x] T1 — Implementar seed SQL seguro para CU26/CU27.
- [x] T2 — Implementar preflight read-only de siete empresas.
- [x] T3 — Crear pruebas de seed, conflictos, rollback y menú.
- [x] T4 — Revisar homónimos en `requerir_permiso()` y documentar propuesta compatible sin tocar routers.
- [x] T5 — Ejecutar verificaciones y revisar diff; sin aplicar a bases reales.

## Ruta
- T1–T4: delegated direct, porque preparan/escriben múltiples archivos no triviales.
- T5: verificación acotada, sin mutación remota.

## Estado
- Exploración RBAC completada; se confirmó que los IDs son `BIGINT` generados por PostgreSQL/identity y que sólo `funcion(modulo_id,nombre)` y `rol_funcion(rol_id,funcion_id)` tienen unicidad declarada.
- Verificación observada: suite nueva 12 passed; suites relacionadas CU26/CU27/autorización 44 passed; `py_compile` y `git diff --check` correctos. El CLI del preflight rechazó ejecución sin URL explícita con código 2; no se abrió ninguna conexión.
- Resultado: seed, preflight y pruebas preparados; no se ejecutó SQL real ni se modificaron routers/dependencies.
