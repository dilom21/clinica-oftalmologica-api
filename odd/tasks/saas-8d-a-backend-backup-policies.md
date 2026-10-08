# ODD — PASO 8D FASE A: Contratos backend para políticas de backup

## Objetivo
Auditar los endpoints SaaS existentes de backups/restores/políticas y crear
únicamente los contratos faltantes para administrar `saas_control.backup_policy`
desde la consola SaaS, sin ejecutar backups/restores reales.

## Problema / Por qué
`backup_policy` existe por migración 006 y se administra solo con SQL/script.
La consola web necesita `GET /saas/backup-policies` y
`PUT /saas/backup-policies/{empresa_id}` con auth SaaS Admin, validaciones,
cálculo backend de `proximo_backup` y bitácora sana.

## Alcance autorizado
- Solo `C:\SI2_Proyecto\clinica-oftalmologica-api`.
- Solo endpoints nuevos de políticas; NO duplicar backups/restores.
- NO ejecutar backup/restore real; NO tocar DB reales; NO editar `.env`.
- NO editar migraciones 001–007 ni backups/restore existentes.
- NO commit/push/merge.

## Restricciones de contrato
- Rechazar del cliente: `database_name`, `storage_key`, archivo,
  `ultimo_backup_automatico`, `ventana`, `proximo_backup`.
- `proximo_backup` se calcula en backend con el motor existente
  (`backup_policy.next_occurrence`); guardar NUNCA dispara un dump.
- Deshabilitar no borra historial ni dumps ni `ultimo_backup_automatico`.
- No exponer secretos/rutas/`database_name` en respuestas.

## Registro de ruta (delegación)
| Tarea | Ruta | Evidencia del trigger |
|-------|------|-----------------------|
| Auditoría de módulo | inline | lectura dirigida de <4 archivos decisores por paso |
| Schemas + service + router | inline | edición mecánica acotada en módulo ya comprendido |
| Tests focalizados | inline | archivo de test nuevo en `tests/` |

## Checklist
- [x] 8D-A1 Auditar endpoints SaaS existentes (backups/restores) y confirmar
      que NO existe CRUD/read API para `backup_policy`.
- [x] 8D-A2 Schemas `BackupPolicyResponse` y `BackupPolicyUpdateRequest`
      (`extra="forbid"`, retención 1..365, hora local sin tz).
- [x] 8D-A3 `policy_service.py`: listar empresas+policy y upsert idempotente con
      validación de empresa/frecuencia/timezone/retención, cálculo de
      `proximo_backup` en backend y bitácora.
- [x] 8D-A4 Endpoints `GET /saas/backup-policies` y
      `PUT /saas/backup-policies/{empresa_id}` con `get_saas_admin`.
- [x] 8D-A5 Tests focalizados (auth, upsert, timezone, frecuencia, retención,
      bitácora, no-secretos, no-scheduler).
- [x] 8D-A6 Suite completa + `git diff --check`.

## Evidencia de verificación
- `pytest -q tests/test_saas_backup_policies.py` → 29 passed.
- `pytest -q` → 860 passed, 2 warnings.
- `git diff --check` → exit 0 (solo avisos LF→CRLF preexistentes).
- OpenAPI: `/saas/backup-policies` [get] y `/saas/backup-policies/{empresa_id}` [put],
  registrados una sola vez, sin duplicar backups/restores.

## Decisiones
- `GET` devuelve una fila por empresa (incluye no configuradas con `configurada=false`)
  para que la consola muestre las 7 empresas sin una segunda llamada.
- Guardar una política nunca toca el scheduler ni crea `BackupTenant`.
- `proximo_backup` se recalcula solo al crear, al cambiar la agenda
  (frecuencia/hora/timezone) o al re-habilitar; un cambio solo de retención
  preserva la ventana pendiente. `ultimo_backup_automatico` nunca se modifica.

## Criterios de aceptación
- Guardar una política NO crea `BackupTenant` ni invoca el scheduler/dump.
- `PUT` idempotente; `proximo_backup` en el futuro salvo cambio de agenda.
- Cambio solo de retención preserva un `proximo_backup` pendiente.
- Respuestas 4xx para empresa inexistente, frecuencia/timezone/retención
  inválidas y campos no confiables.

## Verificación
- `.\.venv\Scripts\python.exe -m pytest -q tests/test_saas_backup_policies.py`
- `.\.venv\Scripts\python.exe -m pytest -q`
- `git diff --check`
