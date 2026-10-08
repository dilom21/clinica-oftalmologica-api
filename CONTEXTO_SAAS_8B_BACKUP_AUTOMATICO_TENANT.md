# CONTEXTO_SAAS_8B_BACKUP_AUTOMATICO_TENANT.md

Repositorio:
C:\SI2_Proyecto\clinica-oftalmologica-api

Estado previo:
PASO 8A completo con backup manual REAL de MEDICO-OCULAR:
- POST /saas/backups -> HTTP 201
- backup_id=2
- tipo=MANUAL
- estado=COMPLETADO
- formato=CUSTOM
- size_bytes=77898
- version_schema=v1
- pg_restore --list válido
- dump de tenant_medico_ocular
- servidor PostgreSQL 17.6
- pg_dump 18.6
- SHA-256 local coincide con metadata
- backup_id=1 permanece ERROR como evidencia del primer intento fallido

Objetivo 8B:
Implementar backups automáticos por tenant reutilizando exactamente el motor validado en 8A.

Requisitos:
- tipo=AUTOMATICO
- no duplicar lógica pg_dump
- política por empresa
- empresa ACTIVA
- suscripción ACTIVA/vigente
- tenant_database ACTIVA
- idempotencia por ventana
- bloqueo si existe EN_PROCESO del mismo tenant
- tenants distintos independientes
- fallo de un tenant no detiene los demás
- script seguro de ejecución automática
- bitácora SaaS
- retención de backups automáticos
- no borrar MANUAL ni PRE_RESTORE
- storage privado durable preparado para producción
- no usar product-images
- no bucket público
- no service_role en Angular

Preferencia de scheduler:
No depender de scheduler in-process del servidor web.
Preferir script CLI invocable por Render Cron u otro scheduler externo.

Script sugerido:
scripts/saas/run_automatic_backups.py

Si hace falta metadata nueva:
crear database/saas_control/006_create_backup_policy.sql
sin modificar 005.

Tabla sugerida:
saas_control.backup_policy
- id
- empresa_id
- habilitado
- frecuencia
- hora_local
- timezone
- retencion_cantidad
- ultimo_backup_automatico
- proximo_backup
- timestamps

No ejecutar automático real todavía.
No restore.
No frontend.
No commit/push/merge.
