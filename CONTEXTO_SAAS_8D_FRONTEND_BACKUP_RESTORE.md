# CONTEXTO — PASO 8D · Consola SaaS de Backup, Políticas y Restore

## Repositorios y forma de trabajo

Backend FastAPI: `C:\SI2_Proyecto\clinica-oftalmologica-api`

Frontend Angular: `C:\SI2_Proyecto\clinica-oftalmologica-web`

Trabajar primero en el **backend solamente si faltan contratos para administrar políticas**; revisar y pasar el reporte de backend antes de implementar frontend. No borrar cambios previos. Sin commit, push, merge, deploy o cambios de `.env`.

## Estado demostrado

- 7 empresas/tenants físicos; SaaS Admin separado de la clínica.
- 8A: backup manual MEDICO-OCULAR, `backup_id=2`, COMPLETADO, dump CUSTOM, 77 898 bytes, SHA-256 y `pg_restore --list` verificados.
- 8B: backup automático real MEDICO-OCULAR, `backup_id=3`, COMPLETADO, ventana/idempotencia, bitácora.
- 8C: restauración real de MEDICO-OCULAR `restore_id=1`, `backup_id=3`, `pre_restore_backup_id=4`, `COMPLETADO`, HTTP 201. Confirmar adicionalmente que tenant 7 está ACTIVA y login clínico HTTP 200; si no está comprobado, dejar verificación pendiente, no inventarla.
- Backups: `#1 MANUAL ERROR` (evidencia), `#2 MANUAL COMPLETADO`, `#3 AUTOMATICO COMPLETADO`, `#4 PRE_RESTORE` creado durante restauración (verificar estado con GET).
- `GET /saas/restores?empresa_id=1` y `GET /saas/backups?empresa_id=4` retornaron `[]` por filtrar empresas incorrectas; ambos registros corresponden a `empresa_id=7`. Distinguir `empresa_id` de ID de backup/restore.
- La consola SaaS Angular usa Signals para estados async; evitar regresar a propiedades no reactivas.

## Endpoints backend que ya existen (auditar contratos reales antes de usarlos)

- `POST /saas/backups` con `{ "empresa_id": 7 }` para MANUAL.
- `GET /saas/backups?empresa_id=7` y `GET /saas/backups/{backup_id}`.
- `POST /saas/restores/validate` con `{ "backup_id": 3 }`.
- `POST /saas/restores` con `{ "backup_id": 3, "confirmacion": "MEDICO-OCULAR" }`.
- `GET /saas/restores?empresa_id=7` y `GET /saas/restores/{restore_id}`.
- `GET /saas/empresas` devuelve metadata y nombre/código para mostrar en selectores.
- Autenticación SaaS Admin token dedicado `saas_access_token`; legacy/tenant no autorizados.

## Política automática y carencia de administración web

`saas_control.backup_policy` existe por migración 006; por ahora se administra con SQL y script. Crear solo los endpoints necesarios para visualizar/editar políticas (después de inspeccionar lo que ya está disponible), sin ejecutar un scheduler desde el frontend:

- `GET /saas/backup-policies` lista policies y empresa correspondiente, con datos seguros.
- `PUT /saas/backup-policies/{empresa_id}` crea/actualiza política idempotentemente, validando empresa, `habilitado`, `frecuencia` (`DIARIA`, `SEMANAL`, `MENSUAL`), `hora_local`, timezone IANA, `retencion_cantidad`, y calculando `proximo_backup` en backend según políticas del módulo existente.

No aceptar desde cliente `database_name`, `storage_key`, archivo, `ultimo_backup_automatico`, `ventana` ni `proximo_backup` arbitrario. Verificar y probar que cambios no desencadenen un dump inmediato. Auditar el cambio con acciones sin secretos. Si ya existen endpoints equivalentes reutilizarlos; no duplicarlos.

## Diseño UI SaaS

Rutas orientativas, adaptar a `app.routes.ts` real:

- `/saas/backups`: historial y creación de backup MANUAL.
- `/saas/backup-policies`: políticas automáticas por empresa.
- `/saas/restores`: historial y recuperación controlada.

Agregar navegación al shell SaaS existente (no crear otra consola). Interfaz en español, responsive 360/390/768/1024+, teclado/foco/aria-live, skeleton o loading, errores visibles sin filtrar secretos. Emplear Angular Signals y pruebas con HTTP asíncrono y DOM.

### Backups

Tabla real con: id, empresa (nombre y código), tipo (MANUAL/AUTOMATICO/PRE_RESTORE), estado, fecha, formato, tamaño, hash abreviado, versión. Filtro por empresa/tipo/estado; opción de ver detalle. En caso ERROR, mostrar texto saneado. Crear MANUAL solo si empresa activa, con confirmación explícita y spinner; un solo POST por clic, sin reintento automático. Al recibir 201, refrescar lista y notificar éxito. No mostrar `storage_key`, rutas privadas, claves, URLs, dump ni credenciales. No permitir descarga pública improvisada.

### Políticas automáticas

Mostrar las siete empresas con estado de política habilitada/no configurada, frecuencia, hora local, zona horaria, retención, último/próximo backup. Permitir crear/editar/habilitar/deshabilitar con confirmación; validación frontend + backend. Deshabilitar política no borra historial ni dumps.

**Importante:** el job externo de ejecución programada aún NO consta como desplegado; el almacenamiento utilizado para pruebas es local. La UI no debe anunciar “copias automáticas en producción activas” si no hay evidencia de Cron real y almacenamiento privado durable. Mostrar configuración programada y aclaración operacional.

### Restore

Historial con id, empresa, backup objetivo, PRE_RESTORE, estado, etapa, fecha, rollback y mensaje saneado. Para iniciar:

1. Usuario selecciona backup COMPLETADO asociado a empresa.
2. `POST /saas/restores/validate` y mostrar resultado, fecha, tipo, empresa, tamaño, SHA-256 abreviado.
3. Mostrar advertencia clara de que el restore reemplaza el estado del tenant y lo dejará temporalmente no disponible.
4. Exigir que el usuario escriba exactamente el `empresa_codigo` de la empresa, sin autocompletar ni aceptar otro texto.
5. Enviar una sola vez `{backup_id, confirmacion}`; deshabilitar botón durante operación; no reintentar automáticamente si hay timeout/500.
6. Tras respuesta, consultar `GET /saas/restores/{id}` y actualizar historial. Si el cliente pierde conexión, consultar historial ANTES de cualquier reintento.
7. Mostrar PRE_RESTORE vinculado y estado de rollback cuando exista. Ante rollback fallido indicar intervención manual sin mostrar datos privados.

Nunca cambiar el estado de tenant desde Angular, ni hacer `DROP`, SQL, `pg_restore`, ni crear PRE_RESTORE en el navegador; todo eso corresponde al backend.

## Seguridad y consistencia

- Proteger rutas y peticiones con SaasAuthGuard + interceptor SaaS existente.
- Rechazar tokens tenant/legacy en rutas SaaS; logout SaaS no borra token clínico.
- No permitir selección cruzada de empresa/backup.
- Mostrar solo empresas/metadata permitida; no `database_name` al público.
- Prohibido cualquier restore real automático durante implementación o tests.
- No cambiar 001–007, backups 1–4, DB tenants, `.env`, storage ACL, políticas existentes sin autorización.

## Pruebas y cierre

Backend si se crean endpoints: tests de auth, lectura/upsert policy, timezone, status, auditoría, no exposición de secretos, no ejecución del scheduler al guardar. Ejecutar suite backend completa.

Frontend: tests HTTP simulados y DOM asíncrono (no solo `of(...)`), navigation/guard/interceptor; filtros correctos por `empresa_id`, render de backups #1–#4, creación manual simulada, edición de políticas simulada, validate y confirmación de restore simuladas; protección contra doble POST, gestión de timeout y errores, accesibilidad. Ejecutar `npm test -- --watch=false`, `npx.cmd tsc --noEmit -p tsconfig.app.json`, `npm run build`, `git diff --check`.

Prueba manual final: listar backups y restores reales de MEDICO-OCULAR filtrando por `empresa_id=7`, visualizar restore #1 y PRE_RESTORE #4; NO iniciar otro backup o restore real desde OpenCode. No afirmar automatización durable/cron productivo sin haberlo instalado y probado.

## Próximo paso operativo después de 8D

8E (si se requiere para la rúbrica/despliegue): almacenamiento privado **durable** y scheduler externo real en producción (p. ej. Render Cron) con registro de ejecuciones, claves backend exclusivamente y E2E automático. Es distinto de tener el script `run_automatic_backups.py` funcionando manualmente.
