# MASTER_PROMPT_SAAS_7D2_CREAR_PRIMER_TENANT.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 7D.2 — CREAR Y PROVISIONAR tenant_vision_clara

Lee COMPLETOS:

- CONTEXTO_SAAS_7D2_CREAR_PRIMER_TENANT.md
- MASTER_PROMPT_SAAS_7D2_CREAR_PRIMER_TENANT.md
- database/tenants/vision_clara/README.md
- scripts/saas/provision_tenant.py
- tests/test_tenant_provisioning.py

Este bloque SÍ puede crear físicamente SOLO:

tenant_vision_clara

si todos los prechecks pasan.

==================================================
GATE 0 — HERRAMIENTAS
==================================================

Antes de mutar nada, busca:

pg_dump
pg_restore
psql

en:
1. PATH
2. C:\Program Files\PostgreSQL\17\bin
3. C:\Program Files\PostgreSQL\16\bin

Preferir PostgreSQL 17.

Si pg_dump o pg_restore no existen:

STOP.

NO instales automáticamente.
NO cambies estados.
Reporta qué falta.

El provisioner puede aceptar:

--pg-bin-dir

==================================================
GATE 1 — CONTROL PLANE / FUENTE
==================================================

READ-ONLY:

- VISION-CLARA existe
- database_name = tenant_vision_clara
- tenant_database = PENDIENTE
- provisionamiento = PENDIENTE
- tenant_vision_clara NO existe físicamente
- rolcreatedb = true
- auditoría de dependencias 7D.1 sigue sin bloqueadores

Volver a capturar los 27 conteos fuente inmediatamente antes del dump.

No asumir que siguen iguales a 7D.1.

==================================================
GATE 2 — TESTS
==================================================

Primero extiende de forma segura:

scripts/saas/provision_tenant.py
tests/test_tenant_provisioning.py

El provisioner debe mantener:

--dry-run

y agregar ejecución explícita, preferentemente:

--execute
--confirm tenant_vision_clara

Sin ambos:
NO mutaciones.

Debe rechazar:
- DB existente
- estado no PENDIENTE
- confirm incorrecto
- database_name distinto de Control Plane

No DROP automático.

Ejecuta tests antes de ejecución real.

==================================================
CREATE DATABASE
==================================================

Crear SOLO:

tenant_vision_clara

NO:

CREATE DATABASE ... TEMPLATE postgres

Preferir DB limpia/template0 si aplica.

La operación debe estar fuera de transacción/autocommit.

Usa database_name validado y proveniente de Control Plane.

Si el pooler no permite CREATE DATABASE:

STOP.

NO inventes una URL directa.
NO expongas secretos.
Reporta necesidad de conexión administrativa directa.

==================================================
ESTADOS
==================================================

Solo tras prechecks completos:

tenant_database → PROVISIONANDO
provisionamiento → EN_PROCESO
intentos += 1
fecha_inicio = now()

En error posterior:
tenant_database → ERROR
provisionamiento → ERROR
mensaje saneado

NO DROP automático.

==================================================
DUMP
==================================================

Crear dump temporal fuera del repo:

pg_dump
-Fc
--schema=public
--no-owner
--no-privileges

Fuente:
postgres

No incluir:
saas_control
auth
storage
realtime
extensions

No password en argv.

Usar PGPASSWORD/PGSSLMODE en environment del subprocess.

No imprimir password ni URL completa.

==================================================
RESTORE
==================================================

Restaurar en:

tenant_vision_clara

con:

pg_restore
--no-owner
--no-privileges
--exit-on-error

Resolver de forma segura `public` preexistente.

Si necesitas --clean --if-exists:
SOLO contra tenant_vision_clara.

NUNCA contra postgres.

==================================================
VERIFY
==================================================

Debe verificar:

27 tablas
27 secuencias
estructura equivalente
PK/FK/UNIQUE
índices
conteos exactos snapshot pre-dump

NO activar si existe cualquier diferencia inesperada.

Verificar que NO existan:

saas_control
auth
storage
realtime
vault
graphql
graphql_public
supabase_functions

en tenant_vision_clara.

==================================================
ACTIVACIÓN
==================================================

Solo con TODO correcto:

tenant_database:
estado = ACTIVA
fecha_provisionamiento = now()
ultima_verificacion = now()
version_schema = valor coherente

provisionamiento:
estado = COMPLETADO
paso_actual = COMPLETADO
fecha_fin = now()
mensaje_error = NULL

Después probar con código real:

TenantResolver("VISION-CLARA")
TenantEngineRegistry
SELECT 1

y conteos simples de:
usuario
paciente
cita

sin imprimir PII.

==================================================
LOGIN
==================================================

NO solicites ni imprimas contraseña.

No es obligatorio login E2E automático.

Si el usuario tiene luego una cuenta conocida, se probará manualmente.

==================================================
DUMP TEMPORAL
==================================================

Tras éxito:
eliminar dump temporal.

Nunca ponerlo dentro del repo.
Nunca commit.

==================================================
TESTS
==================================================

Cubrir al menos:

- dry-run sin mutación
- execute requiere confirm
- confirm incorrecto
- DB existente
- tenant no PENDIENTE
- prechecks antes de estado
- PROVISIONANDO
- error → ERROR
- éxito → ACTIVA/COMPLETADO
- pg_dump public
- restore target
- password no argv
- secrets no logs
- dump fuera repo
- no TEMPLATE postgres
- mismatch conteos bloquea
- schema prohibido bloquea
- health check bloquea
- cleanup dump

Ejecuta:

.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q

.\.venv\Scripts\python.exe -m pytest -q

git diff --check

==================================================
NO HACER
==================================================

NO:
- crear otros 6 tenants
- DROP DATABASE
- modificar fuente
- cutover login
- migrar routers
- frontend
- móvil
- realtime
- backup/restore funcional
- .env
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. PRECHECKS
3. HERRAMIENTAS POSTGRES
4. DB CREADA
5. ESTADOS PROVISIONAMIENTO
6. DUMP
7. RESTORE
8. ESTRUCTURA TARGET
9. CONTEOS SOURCE/TARGET
10. SCHEMAS TARGET
11. HEALTH CHECK
12. TENANT RESOLVER REAL
13. ENGINE REGISTRY REAL
14. LOGIN TENANT
15. ARCHIVOS MODIFICADOS
16. TESTS BLOQUE
17. SUITE COMPLETA
18. DUMP TEMPORAL
19. ERRORES
20. GIT STATUS

Confirma:
- solo tenant_vision_clara
- source intacto
- no saas_control/auth/storage clonados
- no secrets
- no commit/push/merge
