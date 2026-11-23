# MASTER_PROMPT_SAAS_7D1_PREPARAR_PRIMER_TENANT.md

Trabaja EXCLUSIVAMENTE en:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Estamos en:

```text
PASO 7D.1 — PREPARAR PRIMER TENANT FÍSICO
```

Lee COMPLETOS:

```text
CONTEXTO_SAAS_7D1_PREPARAR_PRIMER_TENANT.md
MASTER_PROMPT_SAAS_7D1_PREPARAR_PRIMER_TENANT.md
```

IMPORTANTE:

En este bloque NO crees todavía:

```text
tenant_vision_clara
```

Necesitamos primero una auditoría exacta y un proceso reproducible.

==================================================
CONTEXTO
==================================================

El sistema actual single-tenant vive en:

database postgres
schema public

Consideraremos que esos datos actuales pertenecen a:

VISION-CLARA

y posteriormente se migrarán a:

tenant_vision_clara

NO copiar:

saas_control
auth
storage
realtime
extensions
u otros schemas Supabase.

NO usar:

CREATE DATABASE ... TEMPLATE postgres

==================================================
1. INVENTARIO READ-ONLY
==================================================

Conecta a Supabase actual SOLO para consultas read-only.

Inventaria schema public:

- tablas
- vistas
- materialized views
- secuencias
- funciones
- procedimientos
- triggers
- tipos custom
- índices
- constraints
- PK
- FK
- UNIQUE

Obtén lista real.

Clasifica tablas:

NEGOCIO_TENANT
NO_TENANT
DUDOSA

No asumas solamente por nombre.

==================================================
2. DEPENDENCIAS EXTERNAS
==================================================

Busca referencias desde public hacia:

auth
storage
saas_control
realtime
extensions

Revisar:

FK
views
functions
triggers
defaults
policies

Si existe una dependencia crítica:
NO la ocultes.
Repórtala.

==================================================
3. CONTEOS FUENTE
==================================================

Crear:

database/tenants/vision_clara/001_source_inventory.sql

READ-ONLY.

Debe permitir generar:

tabla | filas_origen

solo conteos, sin contenido sensible.

==================================================
4. AUDITORÍA DEPENDENCIAS
==================================================

Crear:

database/tenants/vision_clara/002_dependency_audit.sql

READ-ONLY.

Debe detectar dependencias del negocio hacia schemas externos.

==================================================
5. VERIFY TARGET
==================================================

Crear:

database/tenants/vision_clara/003_verify_tenant.sql

READ-ONLY.

Se utilizará después del restore para comparar:

- tablas
- columnas/tipos
- PK
- FK
- uniques
- funciones/triggers críticos
- conteos

No ejecutarlo todavía contra tenant inexistente.

==================================================
6. HERRAMIENTAS POSTGRES
==================================================

Ejecuta:

pg_dump --version
pg_restore --version
psql --version

NO instales automáticamente nada.

Reporta versiones o ausencia.

==================================================
7. CONEXIÓN SANEADA
==================================================

Inspecciona DATABASE_URL sin imprimirla.

Reporta solo:

- driver
- host clasificado direct/pooler/otro
- port
- database actual

NO mostrar password.

Determina si para CREATE DATABASE futuro será necesaria conexión directa.

NO cambies .env.

==================================================
8. PRIVILEGIO CREATEDB
==================================================

Consulta READ-ONLY:

rol actual
rolcreatedb

NO ejecutes CREATE DATABASE.

==================================================
9. ESTRATEGIA DUMP/RESTORE
==================================================

Diseña proceso con:

pg_dump
pg_restore

para copiar ÚNICAMENTE:

schema public
+
datos public de negocio

No usar:
JSON
CSV manual
INSERT masivo manual
TEMPLATE postgres

Preferir:
--no-owner
--no-privileges

No incluyas passwords en scripts.

==================================================
10. PROVISIONER DRY-RUN
==================================================

Crear preferentemente:

scripts/saas/provision_tenant.py

Por ahora debe soportar:

--dry-run

Debe leer Control Plane y validar:

VISION-CLARA
tenant_vision_clara
estado PENDIENTE

Puede comprobar:
- tools
- nombre seguro
- metadata
- pasos previstos

NO puede en este bloque:

CREATE DATABASE
DROP DATABASE
RESTORE
cambiar estados

NO imprimir secrets.

==================================================
11. TESTS
==================================================

Crear:

tests/test_tenant_provisioning.py

Cubrir:

- dry-run no crea DB
- no cambia Control Plane
- database_name inválido
- mapping debe venir Control Plane
- solo PENDIENTE
- no secrets
- no TEMPLATE postgres
- solo public
- excluye schemas Supabase
- herramientas faltantes controladas

Ejecuta:

.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q

.\.venv\Scripts\python.exe -m pytest -q

git diff --check

==================================================
NO HACER
==================================================

NO:

CREATE DATABASE
DROP DATABASE
restore
cambiar PENDIENTE
cambiar provisionamiento
modificar datos fuente
login/JWT
routers clínicos
frontend
móvil
backup/restore funcional
realtime
.env
commit
push
merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. INVENTARIO PUBLIC
3. TABLAS NEGOCIO_TENANT
4. DEPENDENCIAS EXTERNAS
5. CONTEOS FUENTE
6. FUNCIONES/TRIGGERS
7. PG_DUMP/PG_RESTORE/PSQL
8. TIPO DE CONEXIÓN SANEADO
9. PRIVILEGIO CREATEDB
10. ESTRATEGIA DUMP/RESTORE
11. ARCHIVOS CREADOS
12. PROVISIONER DRY-RUN
13. TESTS BLOQUE
14. SUITE COMPLETA
15. CAMBIOS SUPABASE
16. GIT STATUS

Confirma:

- NO tenant DB creada
- NO restore
- NO estados cambiados
- NO datos fuente modificados
- NO secretos impresos
- NO commit/push/merge
