# MASTER_PROMPT_SAAS_8A_BACKUP_MANUAL_TENANT.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 8A — BACKUP MANUAL REAL POR TENANT

Lee completos:

- CONTEXTO_SAAS_8A_BACKUP_MANUAL_TENANT.md
- MASTER_PROMPT_SAAS_8A_BACKUP_MANUAL_TENANT.md
- database/saas_control/*
- app/modules/administracion_saas/*
- app/core/tenancy/*
- scripts/saas/provision_tenant.py
- tests/test_saas_admin_backend.py
- tests/test_tenant_provisioning.py

PASO 7 está cerrado.

==================================================
OBJETIVO
==================================================

Implementar backup manual REAL de cada tenant usando:

pg_dump -Fc

No JSON.
No ORM export.
No schema-only.

==================================================
FASE 1 — AUDITORÍA
==================================================

Antes de modificar:

1. inspecciona infraestructura actual;
2. identifica código reutilizable de provision_tenant.py;
3. identifica patrón SQL de saas_control;
4. identifica modelo/repository/service/router SaaS Admin;
5. identifica manejo de subprocess/timeouts;
6. identifica cómo se sanea error y bitácora.

No ejecutar pg_dump real todavía.

==================================================
FASE 2 — METADATA
==================================================

Crear una migración nueva:

database/saas_control/005_create_backup_tenant.sql

NO editar retroactivamente migraciones ya ejecutadas.

Crear `saas_control.backup_tenant` con metadata suficiente para:

MANUAL
AUTOMATICO (futuro)
PRE_RESTORE (futuro)

Estados:

PENDIENTE
EN_PROCESO
COMPLETADO
ERROR

Incluir:
- empresa_id
- tenant_database_id
- tipo
- estado
- storage_key
- nombre_archivo
- formato
- size_bytes
- sha256
- version_schema
- fechas
- creado_por_saas_usuario_id
- mensaje_error saneado

No almacenar secrets.

Crear rollback/verify SQL si ese es el patrón existente.

==================================================
FASE 3 — STORAGE ABSTRACTION
==================================================

Crear abstracción:

BackupStorage

Operaciones mínimas:
- put
- get
- delete
- exists

Provider local seguro permitido para DEV.

No usar product-images.
No crear storage público.

Diseño debe permitir reemplazar provider por storage privado sin cambiar servicio de backup.

==================================================
FASE 4 — PG BACKUP RUNNER
==================================================

Crear componente reutilizable para:

pg_dump
pg_restore --list

Reusar código seguro del provisioning cuando corresponda.

Backup:

pg_dump
--format=custom
--no-owner
--no-privileges

Target database:
EXCLUSIVAMENTE el database_name obtenido del Control Plane.

Validar nombre con helper estricto.

No aceptar database_name en request.

No password en argumentos CLI ni logs.

Timeout configurable por backend.

==================================================
FASE 5 — BACKUP SERVICE
==================================================

Flujo:

SaaS Admin
→ empresa_id
→ Control Plane
→ tenant_database ACTIVA
→ lock por tenant
→ metadata EN_PROCESO
→ temp dump
→ pg_dump -Fc
→ verificar size > 0
→ SHA-256
→ pg_restore --list
→ storage.put
→ COMPLETADO
→ bitácora CREAR_BACKUP_MANUAL

Fallo:
→ cleanup
→ ERROR
→ mensaje saneado
→ bitácora resultado error si aplica

NO cambiar estado tenant.

==================================================
FASE 6 — ENDPOINTS
==================================================

Implementar:

POST /saas/backups
GET /saas/backups
GET /saas/backups/{backup_id}

POST body:
solo empresa_id o identificador seguro equivalente.

NO:
database_name desde cliente.

Responses:
metadata segura.

==================================================
FASE 7 — CONCURRENCIA
==================================================

Evitar dos backups EN_PROCESO del mismo tenant.

No bloquear innecesariamente tenants distintos.

Testear carrera o, como mínimo, lógica transaccional/lock.

==================================================
FASE 8 — TESTS
==================================================

Crear tests específicos, por ejemplo:

tests/test_saas_backup.py

Cubrir:

1. SaaS JWT requerido.
2. Tenant JWT rechazado.
3. Legacy JWT rechazado.
4. Empresa no existe.
5. Empresa suspendida.
6. Tenant inactivo.
7. database_name inválido.
8. pg_dump custom format correcto.
9. no password en CLI/log.
10. timeout.
11. pg_dump falla.
12. dump vacío.
13. SHA-256.
14. pg_restore --list OK.
15. pg_restore --list falla.
16. storage put OK.
17. storage falla.
18. metadata COMPLETADO.
19. metadata ERROR.
20. bitácora.
21. doble backup mismo tenant rechazado.
22. tenants distintos independientes.
23. GET lista.
24. GET detalle.
25. no secrets.

Ejecutar:

.\.venv\Scripts\python.exe -m pytest tests/test_saas_backup.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
FASE 9 — NO EJECUTAR REAL TODAVÍA
==================================================

NO ejecutar pg_dump real desde OpenCode.

Al finalizar, entregar al usuario:

- SQL que debe aplicarse si hace falta;
- comando PowerShell exacto o endpoint a invocar;
- preflight read-only recomendado;
- cómo configurar pg-bin-dir sin hardcodear secretos.

==================================================
NO HACER
==================================================

NO:
- restore
- automático
- frontend
- cron
- DROP
- modificar datos clínicos
- modificar tenant DBs
- `.env`
- commit
- push
- merge
- bucket público

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. AUDITORÍA
3. SQL/MIGRACIÓN
4. MODELO BACKUP
5. STORAGE ABSTRACTION
6. PG BACKUP RUNNER
7. PG_DUMP COMMAND SEGURO
8. VALIDACIÓN PG_RESTORE --LIST
9. SERVICIO
10. ENDPOINTS
11. CONCURRENCIA
12. BITÁCORA
13. SEGURIDAD
14. TESTS BLOQUE
15. SUITE COMPLETA
16. GIT DIFF CHECK
17. EJECUCIÓN REAL PENDIENTE
18. COMANDO/PROCEDIMIENTO PARA USUARIO
19. GIT STATUS

Confirma:
- pg_dump -Fc real
- sin database_name desde cliente
- sin secrets
- sin restore
- sin automático
- sin commit/push/merge
