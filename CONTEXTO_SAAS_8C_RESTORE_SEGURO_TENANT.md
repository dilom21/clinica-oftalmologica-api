# CONTEXTO_SAAS_8C_RESTORE_SEGURO_TENANT.md

## Proyecto
Clínica Oftalmológica — SI2

## Repositorio objetivo
`C:\SI2_Proyecto\clinica-oftalmologica-api`

## Estado previo confirmado

PASO 8A:
- Backup manual real de MEDICO-OCULAR completado.
- backup_id=2.
- tipo=MANUAL.
- estado=COMPLETADO.
- formato=CUSTOM.
- pg_restore --list válido.
- SHA-256 del manual verificado.

PASO 8B:
- Política automática aplicada para MEDICO-OCULAR.
- dry-run detectó 1 backup debido.
- ejecución automática real completada.
- backup_id=3.
- tipo=AUTOMATICO.
- estado=COMPLETADO.
- ventana=2026-10-08T00:25:35Z.
- size_bytes=77898.
- bitácora CREAR_BACKUP_AUTOMATICO=EXITO.
- pg_restore --list válido.
- backup_id=1 permanece ERROR como evidencia del primer intento fallido.

Antes de cerrar formalmente 8B, si todavía no se mostró el hash local del backup_id=3, debe compararse con `backup_tenant.sha256`.

---

# PASO 8C — RESTORE SEGURO POR TENANT

## 1. Objetivo

Implementar restore REAL y seguro de un tenant a partir de un backup COMPLETADO del MISMO tenant.

El restore debe:

1. validar backup;
2. generar backup PRE_RESTORE del estado actual;
3. bloquear lógicamente el tenant;
4. disponer/cerrar conexiones del pool;
5. restaurar PostgreSQL;
6. verificar estructura y salud;
7. reactivar tenant;
8. registrar bitácora;
9. si falla, intentar rollback usando PRE_RESTORE.

NO debe permitir restaurar el backup de otra empresa.

---

# 2. Regla de oro

Nunca ejecutar `pg_restore` directamente sobre una DB tenant activa sin:

- validar backup;
- PRE_RESTORE;
- lock de restore;
- tenant lógico no disponible;
- conexiones tenant cerradas;
- plan de rollback.

---

# 3. Backups elegibles

Solo pueden restaurarse:

- `estado = COMPLETADO`;
- `tipo IN ('MANUAL','AUTOMATICO','PRE_RESTORE')` según política final;
- backup pertenece a la misma `empresa_id`;
- backup pertenece al mismo `tenant_database_id`;
- storage object existe;
- size_bytes > 0;
- sha256 coincide;
- `pg_restore --list` válido;
- version_schema compatible.

No aceptar:
- storage_key desde cliente;
- database_name desde cliente;
- ruta de archivo desde cliente.

Cliente envía únicamente un `backup_id` y, si se decide, una confirmación segura explícita.

---

# 4. Metadata de restore

Crear migración aditiva nueva, preferentemente:

`database/saas_control/007_create_restore_tenant.sql`

No modificar 005 ni 006.

Tabla sugerida:

`saas_control.restore_tenant`

Campos mínimos:

- id
- empresa_id
- tenant_database_id
- backup_id
- pre_restore_backup_id
- estado
- etapa
- fecha_inicio
- fecha_fin
- creado_por_saas_usuario_id
- mensaje_error saneado
- rollback_estado
- rollback_mensaje saneado

Estados sugeridos:

- PENDIENTE
- EN_PROCESO
- COMPLETADO
- ERROR
- ROLLBACK_EN_PROCESO
- ROLLBACK_COMPLETADO
- ROLLBACK_ERROR

No guardar:
- passwords
- DATABASE_URL
- dump binario
- rutas internas completas
- JWT

---

# 5. Estado lógico del tenant durante restore

Auditar primero los valores permitidos actuales de `saas_control.tenant_database.estado`.

Preferencia funcional:

`ACTIVA -> RESTAURANDO -> ACTIVA`

Si `RESTAURANDO` no está permitido por CHECK actual:
- crear migración explícita y segura para permitirlo;
- NO eliminar validaciones;
- NO usar un valor inventado sin migración.

Durante `RESTAURANDO`:
- TenantResolver debe rechazar nuevos requests/login tenant;
- SaaS Admin debe seguir pudiendo leer Control Plane;
- DB física no se considera disponible para tráfico clínico.

---

# 6. Lock y concurrencia

Un solo restore EN_PROCESO por tenant.

Mientras restore está activo:
- no iniciar otro restore;
- no iniciar backup manual/automático del mismo tenant;
- no permitir provisioning del mismo tenant.

Tenants distintos pueden operar independientemente.

Preferir constraint/lock a memoria solamente.

---

# 7. PRE_RESTORE obligatorio

Antes de cualquier operación destructiva:

Crear un backup usando EXACTAMENTE el motor 8A/8B:

`tipo = PRE_RESTORE`

Debe completar:
- pg_dump -Fc;
- size;
- SHA-256;
- pg_restore --list;
- storage privado;
- metadata COMPLETADO.

Si PRE_RESTORE falla:
- CANCELAR restore;
- tenant permanece ACTIVA;
- no tocar DB física.

---

# 8. Validación del backup objetivo

Antes de tocar la DB:

- recuperar objeto desde BackupStorage;
- verificar exists;
- copiar a staging privado;
- verificar size;
- recalcular SHA-256;
- comparar exactamente con metadata;
- `pg_restore --list`;
- verificar que metadata corresponde al tenant;
- version_schema compatible.

Cualquier fallo:
- restore ERROR;
- no tocar DB física.

---

# 9. Estrategia de restore

Auditar capacidades reales del entorno PostgreSQL/Supabase antes de decidir.

Estrategia preferida por seguridad:

A) restaurar primero en una DB temporal de validación;
B) verificarla;
C) realizar cutover controlado.

Si el entorno no permite un swap seguro, usar estrategia de recreación in-place SOLO con PRE_RESTORE + rollback automático.

No asumir que `ALTER DATABASE RENAME` o swap está permitido: verificar capacidades con tests/preflight.

---

# 10. Estrategia temporal / validación

Nombre temporal generado internamente, nunca desde cliente.

Debe:
- validar identificador;
- no colisionar;
- usar CREATE DATABASE solo si permisos lo permiten;
- `pg_restore --exit-on-error --no-owner --no-privileges`;
- verificar estructura;
- SELECT 1;
- verificar tablas críticas;
- verificar schema version esperada.

No exponer nombre temporal al frontend salvo metadata genérica.

---

# 11. Cutover

Antes del cutover:

- tenant_database.estado = RESTAURANDO;
- TenantEngineRegistry.dispose(tenant);
- impedir nuevas sesiones;
- terminar conexiones activas del tenant si es necesario y está permitido.

Después:
- aplicar estrategia elegida;
- verificar DB final;
- reconstrucción lazy del engine;
- health SELECT 1;
- estado ACTIVA.

No reactivar si la verificación final falla.

---

# 12. Rollback automático

Si restore falla DESPUÉS de modificar la DB final:

- registrar rollback EN_PROCESO;
- restaurar PRE_RESTORE;
- verificar estructura + health;
- si éxito:
  - tenant ACTIVA;
  - restore ERROR;
  - rollback COMPLETADO;
- si rollback falla:
  - tenant NO debe volver a ACTIVA;
  - estado operativo de error/mantenimiento;
  - rollback ERROR;
  - intervención manual requerida.

Nunca ocultar un rollback fallido.

---

# 13. Pg restore

Reutilizar TenantBackupRunner o crear extensión reutilizable.

Comando conceptual:

`pg_restore --exit-on-error --no-owner --no-privileges ...`

No usar shell=True.

Password solo por entorno hijo.

No heredar secrets innecesarios.

No loggear comandos con credenciales.

---

# 14. Endpoints

Solo SaaS Admin.

Sugeridos:

`POST /saas/restores/validate`
Body:
`{"backup_id": 3}`

`POST /saas/restores`
Body:
`{"backup_id": 3, "confirmacion": "..."}`

`GET /saas/restores`

`GET /saas/restores/{restore_id}`

La confirmación puede incluir el código de empresa esperado para evitar restauración accidental.

No aceptar database_name.

---

# 15. Bitácora

Acciones sugeridas:

- VALIDAR_RESTORE
- INICIAR_RESTORE
- COMPLETAR_RESTORE
- ERROR_RESTORE
- INICIAR_ROLLBACK_RESTORE
- COMPLETAR_ROLLBACK_RESTORE
- ERROR_ROLLBACK_RESTORE

Sin secrets.

---

# 16. Verificación post-restore

Mínimo:

- database existe;
- SELECT 1;
- 27 tablas esperadas si schema v1 actual;
- 27 secuencias esperadas si sigue vigente;
- constraints/índices coherentes;
- tablas críticas accesibles;
- version_schema compatible;
- TenantResolver resuelve;
- EngineRegistry crea engine nuevo;
- login tenant vuelve a funcionar.

No hardcodear conteos si el schema cambia: reutilizar fingerprint/verify existente si es posible.

---

# 17. Tests obligatorios

Cubrir:

- solo SaaS Admin;
- tenant/legacy JWT rechazados;
- backup inexistente;
- backup ERROR rechazado;
- backup de otra empresa rechazado;
- sha mismatch;
- size mismatch;
- storage object missing;
- pg_restore --list falla;
- PRE_RESTORE obligatorio;
- PRE_RESTORE falla => no mutación;
- lock restore;
- backup bloqueado durante restore;
- TenantResolver bloquea RESTAURANDO;
- dispose engine;
- restore temporal;
- verificación temporal;
- cutover;
- verificación final;
- rollback automático;
- rollback falla => no ACTIVA;
- bitácora;
- no secrets;
- no database_name cliente;
- metadata restore.

---

# 18. Ejecución real

NO ejecutar restore real desde OpenCode.

Primero:
- implementar;
- testear;
- auditar;
- entregar preflight y procedimiento.

Después el usuario hará un restore REAL controlado de MEDICO-OCULAR usando preferentemente backup_id=3, pero solo después de crear PRE_RESTORE.

---

# 19. No hacer

NO:
- frontend todavía;
- borrar backups existentes;
- modificar backup_id=1/2/3;
- restore real desde OpenCode;
- bucket público;
- secrets en código;
- commit/push/merge.

---

# 20. Criterio de cierre 8C

- metadata restore;
- validación backup;
- PRE_RESTORE;
- lock;
- estado RESTAURANDO;
- engine dispose;
- restore seguro;
- verificación;
- rollback automático;
- bitácora;
- tests;
- una ejecución real de restore exitosa;
- login tenant posterior correcto.
