# CONTEXTO_SAAS_7B_TENANT_CONNECTION_ROUTER.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Bloque:** 7B — Tenant Connection Router  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

El Control Plane SaaS ya existe REALMENTE en Supabase:

```text
schema: saas_control
```

Con:

```text
7 empresas
3 planes
7 suscripciones ACTIVA
7 tenant_database PENDIENTE
7 provisionamientos PENDIENTE
```

Las bases físicas `tenant_*` TODAVÍA NO existen.

Este bloque NO debe crearlas.

---

# 2. Objetivo

Construir en FastAPI la infraestructura interna que permita, más adelante, resolver:

```text
empresa / tenant
        ↓
saas_control
        ↓
tenant_database
        ↓
Engine SQLAlchemy específico
        ↓
Session específica del tenant
```

Sin modificar todavía:

```text
login actual
JWT actual
get_db actual
routers clínicos actuales
```

El objetivo es dejar preparado el núcleo multitenant sin romper los 444+ tests existentes.

---

# 3. Arquitectura esperada

```text
PostgreSQL principal / Control Plane
        │
        │ get_control_db()
        ↓
saas_control.empresa
saas_control.suscripcion
saas_control.tenant_database
        │
        ↓
TenantResolver
        │
        ├── valida empresa
        ├── valida suscripción
        ├── valida tenant_database
        └── devuelve TenantContext
                    │
                    ↓
          TenantEngineRegistry
                    │
                    ├── cache por tenant
                    ├── crea Engine SQLAlchemy
                    └── SessionFactory
                    │
                    ↓
            get_tenant_session(...)
```

En 7B se implementa el núcleo interno.

NO se conecta todavía ningún endpoint clínico real a este flujo.

---

# 4. Principios obligatorios

1. **Fail closed**
   - Si empresa no existe → rechazar.
   - Si empresa está suspendida → rechazar.
   - Si suscripción no está activa/vigente → rechazar.
   - Si tenant_database no está ACTIVA → rechazar.
   - Si `database_name` es inválido → rechazar.
   - Si no hay mapping → rechazar.

2. **Nunca confiar en un database_name del cliente**
   - El cliente nunca manda el nombre físico de la DB.
   - El backend lo resuelve desde `saas_control`.

3. **No guardar secretos en saas_control**
   - La conexión base proviene de configuración segura.
   - Solo se sustituye/deriva el nombre de database destino.

4. **No romper single-tenant actual**
   - `get_db()` sigue funcionando como hasta ahora.
   - 7B solo agrega infraestructura.

---

# 5. Estructura sugerida

Crear un módulo coherente, por ejemplo:

```text
app/core/tenancy/
    __init__.py
    models.py
    schemas.py
    repository.py
    resolver.py
    engine_registry.py
    dependencies.py
    exceptions.py
```

La estructura exacta puede adaptarse al repositorio, pero debe separar claramente:

```text
control-plane access
tenant resolution
engine/session creation
FastAPI dependency futura
```

No mezclarlo con módulos clínicos.

---

# 6. Modelos SQLAlchemy del Control Plane

Mapear como mínimo:

```text
saas_control.empresa
saas_control.plan_saas
saas_control.suscripcion
saas_control.tenant_database
```

Solo las columnas necesarias para resolución.

Usar:

```python
__table_args__ = {"schema": "saas_control"}
```

o equivalente compatible con el estilo actual.

NO crear tablas mediante `Base.metadata.create_all()`.

Las tablas ya existen por SQL.

No mapear password_hash de `saas_usuario` si no es necesario en 7B.

---

# 7. TenantContext

Crear un objeto/schema interno estable, por ejemplo:

```text
TenantContext
```

Con campos mínimos:

```text
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
```

NO incluir:

```text
DATABASE_URL
password
host real
secret
JWT
```

---

# 8. TenantResolver

Debe permitir resolver por identificador lógico seguro.

Para 7B, implementar como mínimo:

```text
resolver_por_codigo_empresa(codigo: str)
```

y opcionalmente:

```text
resolver_por_empresa_id(id)
```

No resolver por `database_name` recibido desde cliente.

Proceso:

```text
codigo empresa
    ↓
empresa
    ↓
suscripción activa y vigente
    ↓
tenant_database
    ↓
TenantContext
```

---

# 9. Validaciones de empresa

Estados válidos de empresa:

```text
ACTIVA
SUSPENDIDA
PENDIENTE
```

Para obtener una sesión tenant:

```text
solo ACTIVA
```

`SUSPENDIDA` y `PENDIENTE` deben fallar.

---

# 10. Validaciones de suscripción

Para sesión tenant:

```text
estado = ACTIVA
fecha_inicio <= fecha local actual
fecha_fin >= fecha local actual
```

Usar la utilidad de fecha local ya existente:

```text
app/core/time.py
```

y configuración:

```text
APP_TIMEZONE
```

NO volver a usar `date.today()` directamente.

Si hay más de una suscripción ACTIVA equivalente, tratarlo como inconsistencia del Control Plane y fallar de forma controlada.

---

# 11. Validaciones tenant_database

Estados:

```text
PENDIENTE
PROVISIONANDO
ACTIVA
SUSPENDIDA
ERROR
```

Solo:

```text
ACTIVA
```

permite crear/obtener engine tenant.

En el estado ACTUAL real de 7A.2 todos están PENDIENTE.

Por tanto:

```text
resolver metadata → puede encontrarla
solicitar session activa → debe rechazar PENDIENTE
```

Esto es correcto en 7B.

NO cambiar estados en Supabase todavía.

---

# 12. Validación database_name

Validar estrictamente:

```regex
^[a-z][a-z0-9_]{0,62}$
```

y longitud <= 63.

No aceptar:

```text
/
\
@
:
?
#
%
espacios
comillas
;
--
```

Nunca interpolar sin validación.

---

# 13. Derivación segura del URL de conexión

La conexión actual del proyecto proviene de:

```text
DATABASE_URL
```

Para tenant database se debe crear una URL derivada de forma segura usando utilidades de SQLAlchemy:

```text
sqlalchemy.engine.make_url
URL.set(database=...)
```

NO usar:

```python
DATABASE_URL.replace(...)
split("/")
concatenación manual de password/host
```

porque puede romper URLs escapadas.

Conceptualmente:

```python
base_url = make_url(settings.DATABASE_URL)
tenant_url = base_url.set(database=context.database_name)
```

No loggear `tenant_url` completo.

No exponer credenciales en errores.

---

# 14. TenantEngineRegistry

Crear componente responsable de:

```text
database_name → Engine + sessionmaker
```

Requisitos:

```text
cache por tenant
thread-safe
lazy creation
pool_pre_ping=True
config coherente con engine actual
dispose individual
dispose_all
```

No crear los 7 engines al arrancar.

Solo crear cuando una tenant DB ACTIVA sea requerida.

Como ahora todas están PENDIENTE, las pruebas unitarias deben mockear tenants ACTIVA.

---

# 15. Caché

La clave de caché debe ser estable, preferentemente:

```text
tenant_database_id + database_name/version
```

o una estrategia coherente.

Debe existir una forma explícita de invalidar/dispose cuando:

```text
empresa se suspende
tenant DB cambia
restore
provisionamiento
```

Aunque esas operaciones todavía no se implementen.

Métodos sugeridos:

```text
get_or_create(context)
dispose_tenant(tenant_database_id)
dispose_all()
```

---

# 16. Control Plane Session

El Control Plane vive en la DB principal actual.

Reutilizar el engine/session actual como origen.

Puede crearse:

```text
get_control_db()
```

como alias/dependency explícita, pero NO cambiar el comportamiento de:

```text
get_db()
```

todavía.

No duplicar innecesariamente la creación del engine principal.

---

# 17. Dependencia futura

Preparar una dependencia interna del estilo:

```text
get_tenant_db_from_context(...)
```

o equivalente.

Pero NO integrarla aún con JWT.

En 7B los tests pueden suministrar explícitamente un `TenantContext`.

La integración:

```text
JWT → tenant_id → get_tenant_db
```

será del 7C.

---

# 18. Excepciones

Crear excepciones internas claras, por ejemplo:

```text
TenantNotFoundError
TenantInactiveError
SubscriptionInactiveError
TenantDatabaseUnavailableError
InvalidTenantDatabaseNameError
TenantConnectionError
ControlPlaneConsistencyError
```

No exponer:

```text
host
port
password
DATABASE_URL
trace SQL
```

---

# 19. Health Check de tenant

Crear capacidad interna:

```text
check_tenant_connection(context)
```

que conceptualmente haga:

```sql
SELECT 1
```

contra tenant ACTIVA.

En 7B:
- NO debe ejecutarse contra las PENDIENTE reales;
- debe ser testeable con mock/fake engine.

Más adelante 7D la usará para marcar tenant como ACTIVA.

---

# 20. Seguridad / logs

Logs permitidos:

```text
tenant_database_id
empresa_id
empresa_codigo
database_name
evento
resultado
```

No loggear:

```text
password
DATABASE_URL
connection string completa
JWT
```

Si el proyecto no tiene logging estructurado, no agregar una librería nueva.

---

# 21. Tests obligatorios

Crear, por ejemplo:

```text
tests/test_tenant_connection_router.py
```

Usar mocks/fakes.

NO intentar abrir `tenant_*` reales.

Cubrir mínimo:

1. resolver empresa válida;
2. empresa inexistente;
3. empresa SUSPENDIDA;
4. empresa PENDIENTE;
5. suscripción vencida;
6. suscripción futura;
7. suscripción no ACTIVA;
8. duplicidad de suscripción ACTIVA;
9. tenant_database inexistente;
10. tenant_database PENDIENTE rechazada para session;
11. tenant_database SUSPENDIDA;
12. tenant_database ERROR;
13. database_name válido;
14. database_name inválido;
15. no acepta database_name desde cliente;
16. `make_url().set(database=...)` preserva credenciales/host sin concatenación manual;
17. engine lazy;
18. cache reutiliza engine;
19. dos tenants distintos → engines distintos;
20. dispose_tenant;
21. dispose_all;
22. pool_pre_ping habilitado;
23. control DB sigue usando session actual;
24. health check SELECT 1 mock;
25. error conexión no filtra URL/secreto;
26. fecha suscripción usa APP_TIMEZONE/utilidad local;
27. tenant PENDIENTE real del Control Plane no se trata como ACTIVA.

---

# 22. Prueba read-only real contra Control Plane

Se permite una prueba READ-ONLY real contra Supabase para confirmar que el resolver puede leer:

```text
VISION-CLARA
```

y obtener metadata:

```text
empresa
suscripción
tenant_database
```

Pero al intentar obtener sesión tenant debe terminar en:

```text
TenantDatabaseUnavailableError
```

porque:

```text
estado_database = PENDIENTE
```

Esto es el comportamiento esperado.

NO cambiar el estado.

NO conectarse a `tenant_vision_clara`.

---

# 23. Suite

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_connection_router.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

La suite existente debe permanecer verde.

---

# 24. No hacer

NO:

- crear databases físicas tenant
- cambiar estado PENDIENTE en Supabase
- modificar seed 7A salvo bug crítico demostrado
- modificar login
- modificar JWT
- agregar tenant al token
- cambiar get_db de routers clínicos
- migrar pacientes/citas
- tocar Angular
- tocar móvil
- backup
- restore
- tiempo real
- crear administrador SaaS
- modificar .env
- commit
- push
- merge

---

# 25. Criterio de terminado 7B

```text
[ ] modelos read-only/control-plane
[ ] TenantContext
[ ] TenantResolver
[ ] validación empresa
[ ] validación suscripción
[ ] APP_TIMEZONE reutilizado
[ ] validación tenant_database
[ ] database_name seguro
[ ] URL derivada con make_url/set
[ ] TenantEngineRegistry
[ ] lazy engines
[ ] cache
[ ] dispose tenant/all
[ ] dependency interna preparada
[ ] health check preparado
[ ] excepciones
[ ] tests unitarios
[ ] suite completa verde
[ ] prueba read-only Control Plane
[ ] tenant PENDIENTE bloqueado
[ ] ningún tenant DB físico creado
[ ] login/JWT intactos
```
