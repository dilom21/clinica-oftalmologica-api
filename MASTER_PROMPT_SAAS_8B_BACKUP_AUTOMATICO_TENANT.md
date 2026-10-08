# MASTER_PROMPT_SAAS_8B_BACKUP_AUTOMATICO_TENANT.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 8B — BACKUP AUTOMÁTICO POR TENANT

Lee:
- CONTEXTO_SAAS_8B_BACKUP_AUTOMATICO_TENANT.md
- MASTER_PROMPT_SAAS_8B_BACKUP_AUTOMATICO_TENANT.md
- app/modules/administracion_saas/backup_*
- app/modules/administracion_saas/*
- database/saas_control/005_create_backup_tenant.sql
- scripts/saas/provision_tenant.py
- tests/test_saas_backup.py
- app/core/time.py

PASO 8A está cerrado con backup manual real exitoso.

OBJETIVO:
Implementar backup automático reutilizando exactamente el motor 8A.

1. AUDITORÍA
Revisa backup_service, runner, storage, metadata, bitácora, locking y timezone.
No dupliques pg_dump.

2. POLÍTICA
Si hace falta crear:
database/saas_control/006_create_backup_policy.sql

Preferencia:
saas_control.backup_policy

Campos:
- empresa_id
- habilitado
- frecuencia
- hora_local
- timezone
- retencion_cantidad
- ultimo_backup_automatico
- proximo_backup
- timestamps

No modificar 005.

3. MOTOR AUTOMÁTICO
Reutilizar:
- pg_dump -Fc
- SHA-256
- pg_restore --list
- BackupStorage
- metadata backup_tenant
- cleanup
- locking

Tipo:
AUTOMATICO

Elegibles:
- empresa ACTIVA
- suscripción ACTIVA/vigente
- tenant_database ACTIVA

4. SCRIPT
Crear:
scripts/saas/run_automatic_backups.py

Debe:
- resolver políticas habilitadas
- detectar backups debidos
- evitar duplicados de misma ventana
- ejecutar secuencialmente
- continuar con otros tenants si uno falla
- producir resumen saneado
- exit code documentado

No password CLI.
No secrets.

5. IDEMPOTENCIA
Evitar dos AUTOMATICO de la misma empresa/ventana.
No bloquear MANUAL fuera del lock EN_PROCESO.

6. CONCURRENCIA
Respetar un EN_PROCESO por tenant.
Tenants distintos independientes.

7. RETENCIÓN
Diseñar retención solo para AUTOMATICO.
Nunca borrar MANUAL ni PRE_RESTORE.
Si implementar purga física ahora complica seguridad, dejar selección/política probada y posponer borrado documentadamente.

8. STORAGE PRODUCTIVO
Preparar provider durable PRIVADO para producción.
Preferencia: Supabase Storage privado dedicado o equivalente.
NO product-images.
NO bucket público.
NO secrets en código.
NO service_role en Angular.
No editar .env.

9. BITÁCORA
Agregar:
CREAR_BACKUP_AUTOMATICO
y si aplica PURGAR_BACKUP_AUTOMATICO.

10. TESTS
Cubrir:
- política habilitada
- tenant elegible
- suspendido excluido
- suscripción inactiva excluida
- tenant inactivo excluido
- tipo AUTOMATICO
- idempotencia
- coexistencia MANUAL
- lock EN_PROCESO
- fallo A continúa B
- resumen
- retención
- no borrar MANUAL
- no borrar PRE_RESTORE
- storage privado
- script exit code
- no secrets

Ejecutar:
.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

11. NO EJECUTAR REAL
No lanzar automático real desde OpenCode.

Entrega al usuario:
- migración 006 a aplicar
- variables/configuración
- comando exacto de ejecución manual de muestra
- cómo verificar metadata y dump

NO HACER:
- restore
- frontend
- modificar backup_id=1
- borrar backup_id=2
- bucket público
- commit
- push
- merge

REPORTE FINAL:
1. ESTADO
2. REUTILIZACIÓN 8A
3. MIGRACIÓN 006
4. BACKUP POLICY
5. ELEGIBILIDAD
6. SCRIPT AUTOMÁTICO
7. IDEMPOTENCIA
8. CONCURRENCIA
9. RETENCIÓN
10. STORAGE PRODUCTIVO
11. BITÁCORA
12. SEGURIDAD
13. TESTS BLOQUE
14. SUITE COMPLETA
15. GIT DIFF CHECK
16. EJECUCIÓN REAL PENDIENTE
17. COMANDO/PROCEDIMIENTO
18. GIT STATUS

Confirma:
- tipo AUTOMATICO
- reutiliza pg_dump -Fc de 8A
- sin restore
- sin frontend
- sin secrets
- sin commit/push/merge
