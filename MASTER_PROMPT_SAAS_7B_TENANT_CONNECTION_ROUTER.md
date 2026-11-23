# MASTER_PROMPT_SAAS_7B_TENANT_CONNECTION_ROUTER.md

Trabaja EXCLUSIVAMENTE en:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Estamos en:

```text
PASO 7B — TENANT CONNECTION ROUTER
```

Lee COMPLETOS:

```text
CONTEXTO_SAAS_7B_TENANT_CONNECTION_ROUTER.md
MASTER_PROMPT_SAAS_7B_TENANT_CONNECTION_ROUTER.md
```

Después inspecciona el código LOCAL.

El Control Plane YA existe realmente en Supabase:

```text
schema saas_control
7 empresas
7 tenant_database PENDIENTE
```

Las bases físicas tenant_* NO existen todavía.

==================================================
OBJETIVO
==================================================

Implementar infraestructura interna:

```text
Control Plane
→ TenantResolver
→ TenantContext
→ TenantEngineRegistry
→ Session tenant
```

SIN integrar todavía:

```text
login
JWT
routers clínicos
```

==================================================
ESTRUCTURA
==================================================

Crea un módulo coherente, preferentemente:

```text
app/core/tenancy/
```

con responsabilidades separadas para:

- modelos Control Plane
- repository
- TenantContext
- resolver
- engine registry
- dependencies internas
- excepciones

Adapta nombres al estilo real del repo.

==================================================
CONTROL PLANE
==================================================

Mapea como mínimo:

```text
saas_control.empresa
saas_control.plan_saas
saas_control.suscripcion
saas_control.tenant_database
```

Usa schema explícito.

NO create_all.

NO migrations en este bloque.

Reutiliza la Session actual para consultar la DB principal.

No cambies get_db actual.

==================================================
TENANT RESOLVER
==================================================

Implementa:

```text
resolver_por_codigo_empresa(codigo)
```

Debe validar:

EMPRESA:
solo ACTIVA.

SUSCRIPCIÓN:
estado ACTIVA
fecha_inicio <= fecha local
fecha_fin >= fecha local

Usa:

app/core/time.py
APP_TIMEZONE

NO date.today() directo.

TENANT_DATABASE:
solo ACTIVA permite sesión.

Estados PENDIENTE/PROVISIONANDO/SUSPENDIDA/ERROR:
rechazar para conexión.

Si existen múltiples suscripciones activas inconsistentes:
fail closed.

==================================================
TENANT CONTEXT
==================================================

Crear objeto interno con:

empresa_id
empresa_codigo
empresa_slug
empresa_estado
suscripcion_id
plan_codigo
suscripcion_estado
suscripcion_fecha_inicio
suscripcion_fecha_fin
tenant_database_id
database_name
database_estado
version_schema

NO incluir URLs/password/secrets.

==================================================
DATABASE NAME
==================================================

Validación estricta:

^[a-z][a-z0-9_]{0,62}$

Nunca aceptar database_name enviado desde frontend.

El nombre sale exclusivamente de saas_control.

==================================================
CONNECTION URL
==================================================

MUY IMPORTANTE:

NO hacer string replace.
NO concatenar manualmente.

Usa:

sqlalchemy.engine.make_url

y:

URL.set(database=database_name)

a partir de la configuración segura existente.

No loggear URL completa.

==================================================
ENGINE REGISTRY
==================================================

Implementar:

- lazy creation
- cache por tenant
- thread-safe
- pool_pre_ping=True
- sessionmaker coherente
- dispose_tenant(...)
- dispose_all(...)

No crear engines al startup.

No conectar a tenants PENDIENTE.

==================================================
CONTROL DB
==================================================

Puedes exponer:

get_control_db()

pero debe reutilizar infraestructura actual.

NO duplicar engine principal.

NO cambiar get_db de endpoints existentes.

==================================================
HEALTH CHECK
==================================================

Preparar:

check_tenant_connection(context)

basado en:

SELECT 1

Pero NO lo ejecutes contra los 7 PENDIENTE actuales.

Debe ser testeable con mocks.

==================================================
EXCEPCIONES
==================================================

Usa excepciones internas claras.

Por ejemplo:

TenantNotFoundError
TenantInactiveError
SubscriptionInactiveError
TenantDatabaseUnavailableError
InvalidTenantDatabaseNameError
TenantConnectionError
ControlPlaneConsistencyError

No exponer secretos en mensajes.

==================================================
PRUEBA REAL READ-ONLY
==================================================

Se permite consultar el Control Plane real de Supabase.

Para:

VISION-CLARA

Debes demostrar que el resolver lee:

empresa
suscripción
tenant_database

Pero como la DB está PENDIENTE:

solicitar sesión tenant debe fallar de forma controlada.

Eso es CORRECTO.

NO:
- cambiarla a ACTIVA;
- crear tenant_vision_clara;
- intentar inventar una conexión física inexistente.

==================================================
TESTS
==================================================

Crear:

tests/test_tenant_connection_router.py

Mockear conexiones tenant.

Cubrir mínimo:

1 empresa válida
2 inexistente
3 suspendida
4 pendiente
5 suscripción vencida
6 futura
7 no activa
8 dos suscripciones activas
9 mapping DB inexistente
10 DB pendiente
11 DB suspendida
12 DB error
13 nombre válido
14 nombre inválido
15 nunca acepta nombre físico desde cliente
16 make_url/set seguro
17 lazy engine
18 cache
19 tenants distintos
20 dispose tenant
21 dispose all
22 pool_pre_ping
23 control session actual
24 SELECT 1 mock
25 errores sin secrets
26 APP_TIMEZONE
27 PENDIENTE real no se considera activa

Ejecuta:

.\.venv\Scripts\python.exe -m pytest tests/test_tenant_connection_router.py -q

.\.venv\Scripts\python.exe -m pytest -q

git diff --check

==================================================
NO HACER
==================================================

NO:

- crear tenant databases
- modificar estado PENDIENTE
- login
- JWT
- modificar get_db clínico
- Angular
- móvil
- backup
- restore
- realtime
- admin SaaS
- .env
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrégame:

1. ESTADO
2. ARCHIVOS CREADOS
3. ARCHIVOS MODIFICADOS
4. MODELOS CONTROL PLANE
5. TENANT CONTEXT
6. TENANT RESOLVER
7. VALIDACIONES EMPRESA/SUSCRIPCIÓN/DB
8. APP_TIMEZONE
9. SEGURIDAD DATABASE_NAME
10. DERIVACIÓN URL
11. ENGINE REGISTRY
12. CACHE / DISPOSE
13. CONTROL DB
14. HEALTH CHECK
15. EXCEPCIONES
16. TESTS BLOQUE
17. SUITE COMPLETA
18. PRUEBA REAL READ-ONLY VISION-CLARA
19. SECRETOS/LOGS
20. GIT STATUS

Confirma:

- NO tenant DB físicas
- NO cambio estados PENDIENTE
- NO login/JWT
- NO endpoints clínicos migrados
- NO frontend/móvil
- NO commit/push/merge
