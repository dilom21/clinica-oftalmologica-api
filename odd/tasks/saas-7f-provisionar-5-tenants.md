# PASO 7F — Provisionar 5 tenants restantes

## Objetivo
Implementar un modo batch secuencial y fail-closed para provisionar exclusivamente VISUAL-ORIENTAL, INSTITUTO-VISION, OFTALMOCARE, VISTA-SUR y MEDICO-OCULAR.

## Autorización
- Alcance autorizado: `scripts/saas/provision_tenant.py`, `tests/test_tenant_provisioning.py` y este documento.
- No ejecutar el batch real.
- No tocar VISION-CLARA ni OFTALMO-NORTE.
- No incluir datos clínicos, frontend, móvil, backup, realtime, `.env`, commit, push ni merge.

## Checklist
- [ ] Añadir preflight global de los cinco tenants antes del primer `CREATE DATABASE`.
- [ ] Añadir `--provision-pending-batch` con lista cerrada y orden secuencial.
- [ ] Reutilizar el flujo CLEAN parametrizado sin duplicar lógica.
- [ ] Garantizar stop en el primer error, sin recovery ni DROP automático.
- [ ] Solicitar dos veces la contraseña de cada tenant con `getpass`.
- [ ] Añadir pruebas de batch, secretos, emails, aislamiento y engines separados.
- [ ] Ejecutar pruebas específicas, suite completa y `git diff --check`.

## Criterios de aceptación
- El preflight falla cerrado antes de cualquier mutación si uno de los cinco no cumple.
- Un error deja el tenant actual en `ERROR`, conserva anteriores y no toca posteriores.
- El batch no acepta ni procesa los dos tenants ya completados.
- Las credenciales nunca llegan a CLI, logs ni Control Plane.
- Los tests solicitados pasan sin ejecutar infraestructura real.

## Ruta y checks
- Ruta: delegated direct; la exploración confirmó cambios no triviales en script y tests.
- TDD: no resuelto en la configuración disponible; ejecutar checks funcionales existentes.
- Checks: `.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q`, `.\.venv\Scripts\python.exe -m pytest -q`, `git diff --check`.

## Progreso
- Estado: implementado y verificado localmente; el batch real no fue ejecutado.
- [x] Preflight global read-only de los cinco tenants, incluyendo estados, vigencia,
  nombres de base, existencia física, herramientas PostgreSQL y snapshot schema v1.
- [x] Modo explícito `--provision-pending-batch` con lista cerrada, orden inmutable y
  exclusión construida de VISION-CLARA/OFTALMO-NORTE.
- [x] Flujo CLEAN parametrizado, secuencial y fail-closed; el tenant actual queda
  `ERROR`, los anteriores se conservan y los siguientes no se mutan.
- [x] Dos prompts `getpass` por tenant, emails explícitos y sin password en CLI/logs/
  Control Plane.
- [x] Tests unitarios agregados para preflight, orden, STOP, aislamiento, emails,
  prompts, exclusiones y seguridad de argumentos.
- Checks observados:
  - Corrección 7F: `_schema_snapshot()` conserva las 182 filas de columnas
    aunque procese constraints con `rol_id,funcion_id`.
  - Regresiones de snapshot/preflight agregadas: 110 passed en
    `pytest tests/test_tenant_provisioning.py -q`.
  - `pytest -q`: 614 passed, 2 warnings existentes de `InsecureKeyLengthWarning` en pruebas JWT.
  - `git diff --check`: correcto; Git mostró avisos existentes de conversión LF→CRLF.
  - No existe un comando separado de preflight-only del batch; no se ejecutó
    `--provision-pending-batch` ni ningún provisioning real.
