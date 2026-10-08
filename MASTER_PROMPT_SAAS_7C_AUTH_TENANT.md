# MASTER_PROMPT_SAAS_7C_AUTH_TENANT.md

Trabaja EXCLUSIVAMENTE en:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Estamos en:

```text
PASO 7C — AUTH/JWT TENANT-AWARE
```

IMPORTANTE:

Este bloque PREPARA la autenticación multitenant.

NO hacemos todavía el cutover del login principal porque las bases físicas tenant_* aún no existen.

Lee COMPLETOS:

```text
CONTEXTO_SAAS_7C_AUTH_TENANT.md
MASTER_PROMPT_SAAS_7C_AUTH_TENANT.md
```

Inspecciona:

```text
app/modules/gestion_usuarios_seguridad/
app/core/security.py
app/core/dependencies.py
app/core/tenancy/
```

y adapta los nombres al código real.

==================================================
OBJETIVO
==================================================

Agregar:

1. schema de login tenant;
2. servicio auth tenant;
3. JWT tenant-aware;
4. dependencia/helper de claims tenant;
5. endpoint preparatorio separado.

SIN romper:

```text
/auth/login actual
tokens legacy
routers clínicos
get_db actual
```

==================================================
LOGIN TENANT
==================================================

Crear endpoint separado, preferentemente:

POST /auth/tenant/login

Request:

{
  "empresa_codigo": "VISION-CLARA",
  "correo": "...",
  "password": "..."
}

NO aceptar:

database_name
tenant_id
empresa_id

extra="forbid".

==================================================
FLUJO
==================================================

empresa_codigo
→ TenantResolver
→ empresa/suscripción válidas
→ tenant DB debe estar ACTIVA
→ TenantEngineRegistry
→ usuario del tenant
→ Argon2
→ JWT tenant-aware

Como los tenants reales siguen PENDIENTE:

NO intentes E2E válido todavía.

Mocks para caso ACTIVA.

==================================================
JWT
==================================================

Token tenant debe incluir:

sub
rol_id
tenant_id
empresa_id
empresa_codigo
token_type="tenant"
exp

NO incluir:

database_name
DATABASE_URL
host
port
password
connection string
secrets

Documenta exactamente qué representa tenant_id.

==================================================
LEGACY
==================================================

NO rompas:

/auth/login

NO obligues a tokens actuales a tener claims tenant.

La dependencia actual sigue funcionando para endpoints actuales.

Crea una dependencia/helper SEPARADA para tokens tenant.

==================================================
DEPENDENCIA TENANT
==================================================

Preparar:

get_tenant_claims(...)

o equivalente.

Debe exigir:

token_type == "tenant"

tenant_id válido
empresa_id válido
empresa_codigo válido

Después preparar revalidación:

claims
→ Control Plane
→ confirmar empresa/suscripción/DB siguen activas

NO conectar todavía routers clínicos.

==================================================
SEGURIDAD
==================================================

No loggear password.

Credenciales inválidas deben devolver un mensaje genérico.

No revelar si correo existe.

No aceptar selección de DB desde cliente.

No aceptar X-Tenant-ID o X-Database como fuente de verdad.

Tenant viene del JWT firmado.

==================================================
TESTS
==================================================

Crear:

tests/test_auth_tenant.py

Cubrir:

- empresa_codigo requerido
- extra forbid
- database_name rechazado
- tenant_id body rechazado
- empresa inexistente
- suspendida
- suscripción inválida
- DB pendiente
- usuario inexistente
- password incorrecto
- usuario inactivo
- login válido mock
- claims JWT
- sin database_name JWT
- sin secrets JWT
- token tenant aceptado
- legacy rechazado por dependencia tenant
- claims faltantes
- firma manipulada
- tenant_id manipulado
- empresa_id manipulado
- revalidación Control Plane
- login legacy sigue pasando
- endpoints clínicos intactos
- no conexión tenant real pendiente

Ejecutar:

.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q

.\.venv\Scripts\python.exe -m pytest -q

git diff --check

==================================================
PRUEBA REAL
==================================================

Se permite una llamada controlada a:

POST /auth/tenant/login

con:

empresa_codigo = VISION-CLARA

y credenciales de prueba no sensibles.

Esperado:

rechazo controlado porque:

tenant_database = PENDIENTE

Debe fallar ANTES de intentar abrir tenant_vision_clara.

==================================================
NO HACER
==================================================

NO:

- reemplazar /auth/login
- cambiar frontend
- crear tenant DB física
- cambiar PENDIENTE
- migrar datos
- migrar routers clínicos
- modificar get_db actual
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

Entrega:

1. ESTADO
2. ARCHIVOS CREADOS
3. ARCHIVOS MODIFICADOS
4. SCHEMA LOGIN TENANT
5. ENDPOINT
6. FLUJO AUTH TENANT
7. JWT CLAIMS
8. TENANT_ID SEMÁNTICA
9. COMPATIBILIDAD LEGACY
10. DEPENDENCIA TENANT
11. REVALIDACIÓN CONTROL PLANE
12. SEGURIDAD
13. ERRORES
14. TESTS BLOQUE
15. SUITE COMPLETA
16. PRUEBA REAL VISION-CLARA PENDIENTE
17. LOGIN LEGACY
18. GIT STATUS

Confirma:

- NO cutover del login
- NO tenant DB físicas
- NO estados cambiados
- NO routers clínicos migrados
- NO frontend/móvil
- NO commit/push/merge
