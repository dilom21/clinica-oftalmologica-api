# MASTER PROMPT — PASO 8D · Frontend SaaS y contratos de políticas

Repositorios:

- Backend: `C:\SI2_Proyecto\clinica-oftalmologica-api`
- Frontend: `C:\SI2_Proyecto\clinica-oftalmologica-web`

Leer **COMPLETO** `CONTEXTO_SAAS_8D_FRONTEND_BACKUP_RESTORE.md` antes de editar. Usar contratos reales del código, no asumir nombres o respuestas. **Trabajar en dos fases y entregar reporte tras cada una**. No hacer commit, push o merge.

## Fase A — backend, SOLO si faltan endpoints para políticas automáticas

Desde `C:\SI2_Proyecto\clinica-oftalmologica-api`:

1. Inspeccionar `app/modules/administracion_saas`, `backup_policy.py`, `backup_automatic.py`, repositorios, schemas, routes y migración 006. Detectar si ya hay CRUD/read API para `backup_policy`.
2. Si no hay, agregar endpoints seguros `GET /saas/backup-policies` y `PUT /saas/backup-policies/{empresa_id}`, con sesión Control Plane y autorización SaaS Admin.
3. Validar frecuencia, hora, TZ IANA, retención y estado. Calcular `proximo_backup` en backend coherentemente con las reglas ya existentes. No cambiar ventanas históricas ni ejecutar dump al guardar. Upsert transaccional, bitácora sana y errores claros.
4. Tests dirigidos y suite completa; `git diff --check`.
5. **Reportar Fase A; no pasar a frontend hasta revisar el reporte**, salvo instrucción explícita del usuario.

No modificar datos reales de Supabase ni políticas reales al desarrollar. No editar migraciones 001–007.

## Fase B — frontend

Desde `C:\SI2_Proyecto\clinica-oftalmologica-web`:

1. Auditar `src/app/features/saas/**`, rutas, shell, SaasApiService, SaaS auth guard/interceptor, interfaces y uso de Angular Signals. Confirmar contratos reales del backend.
2. Implementar secciones `/saas/backups`, `/saas/backup-policies`, `/saas/restores` dentro de layout SaaS existente. Navegación, filtros, estados, error y loading reactivos con Signals.
3. Backups: historial real, filtro `empresa_id` correcto, detalle, crear MANUAL con confirmación. Renderizar `MANUAL`, `AUTOMATICO`, `PRE_RESTORE`, `ERROR`. No inventar botón para descargar dumps si no existe endpoint seguro.
4. Políticas: listar siete empresas y configuración efectiva; formulario seguro para habilitar/deshabilitar, frecuencia/hora/zona/retención; mostrar último/próximo. Distinguir configuración de ejecución real de Cron en producción.
5. Restore: elegir backup COMPLETADO, validar en backend, confirmar con código EXACTO escrito manualmente, advertir indisponibilidad y PRE_RESTORE, ejecutar una sola petición, consultar historial/detalle, enseñar estado de rollback y fallo sin secrets. Prohibido reintento automático en operaciones destructivas. Prohibido disparar restore real en tests.
6. Mantener la separación `saas_access_token` ≠ `access_token`. Manejar 401 y errors sin afectar token clínico.
7. Tests DOM asíncronos reales con Angular (detectando regresión zoneless), auth/guard/API, flujos de creación simulada, políticas simuladas, confirmación/timeout de restore y filtros correctos por empresa. Build/typecheck/git diff.
8. Reporte Fase B, rutas y navegación, contratos, número de tests, build, Git status y E2E manual pendiente.

## Comandos

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Frontend:

```powershell
npm test -- --watch=false
npx.cmd tsc --noEmit -p tsconfig.app.json
npm run build
git diff --check
```

## Restricciones

- No crear otro backup ni restore REAL desde OpenCode.
- No cambiar bases tenant ni Control Plane real.
- No editar `.env` ni credenciales.
- No cambiar migraciones 001–007.
- No modificar backup_id 1, 2, 3 o 4 ni restore_id 1.
- No implementar programación automática dentro de Angular ni FastAPI web worker.
- No representar local storage como storage productivo durable.
- No commit/push/merge/deploy.

## Reportes

Fase A: endpoints, schemas, auth, validaciones, tests, riesgos pendientes, git status.

Fase B: rutas, componentes, señales, llamadas reales, UI backups/policies/restores, guardias y confirmaciones, tests, build, E2E manual pendiente, git status.
