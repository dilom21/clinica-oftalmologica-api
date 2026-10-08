# ODD — Segundo tenant y aislamiento A/B (7E)

## Objetivo
Crear `tenant_oftalmo_norte` como tenant limpio, con schema v1, catálogos de seguridad, un administrador propio y aislamiento verificable respecto de `tenant_vision_clara`.

## Alcance autorizado
- `scripts/saas/provision_tenant.py`
- `tests/test_tenant_provisioning.py`
- `tests/test_auth_tenant.py`
- `tests/test_tenant_connection_router.py` cuando sea necesario para A/B
- Este documento y su espejo Engram

No modificar tenant_vision_clara, otros tenants, routers clínicos, frontend, móvil, `.env`, ni realizar commit/push/merge.

## Ruta
Delegated direct: el mapeo requiere leer múltiples archivos y la implementación afecta provisioner y pruebas. La exploración confirmó que el provisioner actual está fijado a VISION-CLARA y copia datos; se implementará un flujo separado `--provision-clean`.

## Tareas
- [x] 7E-01 Implementar flujo clean dinámico por Control Plane, schema-only desde `postgres.public`, seed controlado, bootstrap admin con `getpass` y activación fail-closed.
- [x] 7E-02 Agregar pruebas unitarias/integración aisladas para CLI, schema-only, catálogos, password/hash, limpieza y activación.
- [ ] 7E-03 Ejecutar preflight read-only, provisioning real autorizado, validaciones A/B, login manual solicitado y comandos de pruebas.
- [ ] 7E-04 Revisar diff/status y generar reporte 1–19 sin secretos.

## Criterios de aceptación
- Control Plane inicial de OFTALMO-NORTE y DB física inexistente verificados antes de mutar.
- `pg_dump -Fc --schema=public --schema-only --no-owner --no-privileges` desde `postgres.public`; nunca desde `tenant_vision_clara`.
- Solo `accion`, `modulo`, `funcion`, `rol`, `rol_funcion` seedeados salvo dependencia justificada.
- Exactamente un usuario bootstrap; hash real del proyecto; password nunca en argv, logs, Control Plane o reporte.
- Tablas clínicas/transaccionales y seguridad indicada vacías; activación solo después de todas las verificaciones.
- Resolver, registry, engines/pools y `SELECT 1` demuestran separación A/B.
- Tests solicitados, suite completa y `git diff --check` observados.

## Checks
```text
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

## Progreso
- Estado: implementación local completada; no se ejecutó provisioning real ni se mutó Control Plane/DB física.
- Verificación: `tests/test_tenant_provisioning.py`, `tests/test_auth_tenant.py` y `tests/test_tenant_connection_router.py` pasan (142 tests); el cambio 7D conserva su ruta separada.
- Verificación adicional: `git diff --check` ejecutado después de los cambios.
- Dependencia justificada: para los cinco catálogos aprobados se usa un `pg_dump --data-only` explícitamente limitado a `accion`, `modulo`, `funcion`, `rol` y `rol_funcion`, desde `postgres.public`; no se copia `tenant_vision_clara` ni se agrega otra tabla porque la auditoría de dependencias no lo requiere.
- Seguridad: el password se solicita dos veces únicamente con `getpass.getpass()`, se transforma mediante `app.core.security.hash_password`, y el hash se entrega al `psql` por stdin, nunca por argv, logs o Control Plane.
- Clasificación de `servicios_oftalmologicos`: tabla no seedeada, verificada explícitamente como vacía junto con las tablas clínicas/transaccionales; no forma parte de `CLEAN_CATALOG_TABLES`.
- Corrección del verificador: `cleanup_dump()` se intenta una sola vez antes de `writer.complete()`; si falla, el flujo no activa/completa el tenant y conserva el fail-closed hacia `ERROR`.
- Pruebas agregadas para fallo de limpieza y para evitar omitir `servicios_oftalmologicos` en la verificación de vacíos.
- Preflight read-only real: OFTALMO-NORTE existe; `database_name=tenant_oftalmo_norte`; ambos estados `PENDIENTE`; DB física `NO_EXISTE`; `postgres.public` tiene 27 tablas/27 secuencias; `pg_dump`, `pg_restore` y `psql` disponibles; `rolcreatedb` y auditoría de dependencias OK.
- Siguiente paso: ejecutar provisioning real desde una terminal local interactiva para introducir el password sin transmitirlo al agente; después validar A/B y login manual.
