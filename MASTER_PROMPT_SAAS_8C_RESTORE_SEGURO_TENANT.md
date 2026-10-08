# MASTER_PROMPT_SAAS_8C_RESTORE_SEGURO_TENANT.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 8C — RESTORE SEGURO POR TENANT

Lee COMPLETOS:

- CONTEXTO_SAAS_8C_RESTORE_SEGURO_TENANT.md
- MASTER_PROMPT_SAAS_8C_RESTORE_SEGURO_TENANT.md
- app/modules/administracion_saas/backup_*
- app/modules/administracion_saas/*
- app/core/tenancy/*
- database/saas_control/005_create_backup_tenant.sql
- database/saas_control/006_create_backup_policy.sql
- scripts/saas/provision_tenant.py
- tests/test_saas_backup.py

8A y 8B están implementados.
No ejecutar restore real.

==================================================
OBJETIVO
==================================================

Implementar restore seguro de un backup COMPLETADO del MISMO tenant con:

- validación previa;
- PRE_RESTORE obligatorio;
- bloqueo lógico tenant;
- cierre/dispose de conexiones;
- restore;
- verificación;
- reactivación;
- rollback automático mediante PRE_RESTORE si falla.

==================================================
FASE 1 — AUDITORÍA
==================================================

Antes de modificar:

1. revisa CHECKs reales de tenant_database.estado;
2. revisa TenantResolver;
3. revisa EngineRegistry/dispose;
4. revisa permisos CREATE/DROP/ALTER DATABASE ya usados en provisioning;
5. revisa utilidades de pg_restore;
6. revisa fingerprint/verificación estructural existente;
7. determina si swap por DB temporal es viable en este entorno.

NO asumir soporte de ALTER DATABASE RENAME.
NO tocar DB real.

==================================================
FASE 2 — METADATA RESTORE
==================================================

Crear migración aditiva 007, preferentemente:

database/saas_control/007_create_restore_tenant.sql

Crear saas_control.restore_tenant.

Si hace falta agregar estado RESTAURANDO a tenant_database:
hacerlo de forma explícita y segura en 007.

NO editar 001-006.

==================================================
FASE 3 — VALIDACIÓN BACKUP
==================================================

Solo backup:
- COMPLETADO;
- mismo tenant;
- storage existe;
- size correcto;
- SHA-256 exacto;
- pg_restore --list válido;
- version_schema compatible.

Request NO acepta:
- database_name
- storage_key
- file path.

==================================================
FASE 4 — PRE_RESTORE
==================================================

Antes de cualquier mutación:

crear backup tipo PRE_RESTORE reutilizando _run_backup del motor 8A/8B.

Si PRE_RESTORE falla:
ABORTAR.
Tenant sigue ACTIVA.
DB no se toca.

==================================================
FASE 5 — LOCK / ESTADO
==================================================

Un restore activo por tenant.

Bloquear backups del mismo tenant mientras restore está EN_PROCESO.

Cambiar tenant a RESTAURANDO solo cuando:
- backup objetivo válido;
- PRE_RESTORE COMPLETADO.

TenantResolver debe rechazar RESTAURANDO.

==================================================
FASE 6 — ENGINE / SESIONES
==================================================

Antes de cutover:

TenantEngineRegistry.dispose(tenant).

Auditar sesiones activas.

Terminar sesiones solo si es necesario y está permitido.

No matar conexiones de otros tenants/control plane.

==================================================
FASE 7 — ESTRATEGIA RESTORE
==================================================

Preferir:

backup objetivo
→ restore DB temporal
→ verify
→ cutover seguro

Si el entorno no permite swap/rename:
usar recreación in-place solo con PRE_RESTORE y rollback automático.

Documentar decisión basada en capacidades reales, no suposiciones.

==================================================
FASE 8 — PG_RESTORE
==================================================

Extender runner reutilizable.

Usar:
- --exit-on-error
- --no-owner
- --no-privileges

No shell=True.
Password solo por entorno hijo.
Timeout.
Errores saneados.

==================================================
FASE 9 — VERIFY
==================================================

Reutilizar verificadores existentes.

Verificar:
- SELECT 1;
- schema esperado;
- tablas/secuencias/constraints/indexes;
- tablas críticas;
- version_schema;
- resolver;
- engine nuevo.

No hardcodear si ya existe fingerprint reutilizable.

==================================================
FASE 10 — ROLLBACK
==================================================

Si fallo después de mutar final:

PRE_RESTORE
→ restore automático
→ verify

Éxito:
tenant ACTIVA
restore ERROR
rollback COMPLETADO

Fallo:
tenant NO ACTIVA
rollback ERROR
intervención manual requerida

No ocultar rollback fallido.

==================================================
FASE 11 — ENDPOINTS
==================================================

Solo SaaS Admin:

POST /saas/restores/validate
POST /saas/restores
GET /saas/restores
GET /saas/restores/{restore_id}

El POST de restore debe requerir confirmación explícita segura.

No database_name cliente.

==================================================
FASE 12 — BITÁCORA
==================================================

Agregar acciones:

VALIDAR_RESTORE
INICIAR_RESTORE
COMPLETAR_RESTORE
ERROR_RESTORE
INICIAR_ROLLBACK_RESTORE
COMPLETAR_ROLLBACK_RESTORE
ERROR_ROLLBACK_RESTORE

Sin secrets.

==================================================
FASE 13 — TESTS
==================================================

Crear tests específicos, por ejemplo:

tests/test_saas_restore.py

Cubrir:

1. SaaS JWT requerido
2. tenant JWT rechazado
3. legacy JWT rechazado
4. backup inexistente
5. backup ERROR
6. backup otro tenant
7. storage missing
8. size mismatch
9. sha mismatch
10. pg_restore list falla
11. PRE_RESTORE obligatorio
12. PRE_RESTORE falla => no mutación
13. lock restore
14. backup mismo tenant bloqueado
15. RESTAURANDO bloquea resolver
16. engine dispose
17. restore temporal
18. verify temporal
19. cutover
20. verify final
21. rollback automático
22. rollback fallido deja tenant no ACTIVA
23. bitácora
24. sin secrets
25. no database_name cliente

Ejecutar:

.\.venv\Scripts\python.exe -m pytest tests/test_saas_restore.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
FASE 14 — NO EJECUTAR REAL
==================================================

NO restore real desde OpenCode.

Entregar al usuario:
- migración 007;
- preflight read-only;
- estrategia elegida;
- procedimiento exacto;
- backup recomendado;
- cómo verificar y cómo abortar.

==================================================
NO HACER
==================================================

NO:
- frontend
- borrar backup_id=1/2/3
- restore real
- bucket público
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. AUDITORÍA
3. ESTRATEGIA RESTORE
4. MIGRACIÓN 007
5. RESTORE MODEL
6. VALIDACIÓN BACKUP
7. PRE_RESTORE
8. LOCK/RESTAURANDO
9. ENGINE DISPOSE
10. PG_RESTORE
11. VERIFY
12. CUTOVER
13. ROLLBACK
14. ENDPOINTS
15. BITÁCORA
16. SEGURIDAD
17. TESTS BLOQUE
18. SUITE COMPLETA
19. GIT DIFF CHECK
20. EJECUCIÓN REAL PENDIENTE
21. PROCEDIMIENTO USUARIO
22. GIT STATUS

Confirma:
- PRE_RESTORE obligatorio
- mismo tenant
- rollback automático
- sin database_name cliente
- sin restore real
- sin frontend
- sin commit/push/merge
