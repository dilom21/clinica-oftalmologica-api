# CONTEXTO_SAAS_7A_CONTROL_PLANE.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Bloque:** 7A — Control Plane SaaS  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

Este bloque inicia la característica:

```text
SaaS + Tenant + Multitenant
```

Arquitectura elegida:

```text
Database-per-Tenant
```

En este bloque NO se crean todavía las bases físicas de los tenants.

Primero se crea el **Control Plane SaaS**: el registro central que conoce empresas, planes, suscripciones y qué base corresponde a cada tenant.

---

# 2. Objetivo del 7A

Preparar scripts SQL versionados para crear en PostgreSQL/Supabase:

```text
schema saas_control
```

con las tablas:

```text
empresa
plan_saas
suscripcion
tenant_database
saas_usuario
saas_bitacora
provisionamiento_tenant
```

y cargar datos DEMO para:

```text
7 empresas
3 planes
7 suscripciones
7 registros tenant_database
7 provisionamientos pendientes
```

Esto debe permitir demostrar posteriormente:

```text
más de 5 empresas SaaS
+
cada empresa asociada a su propia database
+
suscripción/plan
+
estado de provisionamiento
```

IMPORTANTE:

En 7A las databases todavía NO existen.

`tenant_database.database_name` será el registro lógico de la base que se creará en 7D/7F.

Su estado inicial será:

```text
PENDIENTE
```

No marcar como `ACTIVA` una base que aún no fue creada/verificada.

---

# 3. Arquitectura general

```text
                    CONTROL PLANE
                schema saas_control
                        │
        ┌───────────────┼─────────────────┐
        │               │                 │
     empresa         plan_saas        suscripcion
        │
        ├── tenant_database
        │
        └── provisionamiento_tenant

                saas_usuario
                     │
                saas_bitacora
```

Más adelante:

```text
tenant_database
       ↓
Tenant Connection Router
       ↓
DB física por empresa
```

---

# 4. Convenciones del proyecto

El backend actual usa:

```text
PostgreSQL
SQLAlchemy 2.x
BIGINT para IDs
TIMESTAMP WITH TIME ZONE para auditoría/fechas importantes
Argon2 para hashes de contraseña
FastAPI → SQLAlchemy → PostgreSQL
```

La conexión actual global está en:

```text
app/database/connection.py
app/database/session.py
```

NO modificarla en 7A.

No tocar todavía login/JWT/get_db.

---

# 5. Estrategia de scripts

Crear directorio:

```text
database/saas_control/
```

y generar:

```text
001_create_control_plane.sql
002_seed_demo_control_plane.sql
003_verify_control_plane.sql
004_rollback_control_plane.sql
```

NO ejecutar automáticamente los scripts contra Supabase en este bloque.

Primero deben quedar revisables/versionados.

No usar Alembic si el proyecto actualmente no lo utiliza.

---

# 6. Seguridad del schema

`saas_control` es backend-only.

No debe quedar disponible directamente al frontend mediante roles `anon` o `authenticated`.

El script debe:

```sql
REVOKE ALL ON SCHEMA saas_control FROM anon, authenticated;
```

y asegurar que las tablas/secuencias creadas no otorguen acceso a esos roles.

No almacenar:

```text
DATABASE_URL completa
password PostgreSQL
service_role
JWT secret
API keys
SMTP password
DeepSeek key
```

en ninguna tabla SaaS.

`tenant_database` almacena metadatos, NO credenciales.

---

# 7. Tabla saas_control.empresa

Propósito:

```text
Representa cada cliente/tenant del SaaS.
```

Diseño esperado:

```text
id                  BIGINT identity PK
codigo              VARCHAR(30) NOT NULL UNIQUE
slug                VARCHAR(80) NOT NULL UNIQUE
razon_social        VARCHAR(160) NOT NULL
nombre_comercial    VARCHAR(160) NOT NULL
nit                 VARCHAR(30) UNIQUE
correo              VARCHAR(150) NOT NULL
telefono            VARCHAR(30)
direccion           VARCHAR(255)
logo_url            VARCHAR(500)
estado              VARCHAR(20) NOT NULL
fecha_registro      TIMESTAMPTZ NOT NULL DEFAULT now()
fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT now()
```

Estados permitidos:

```text
ACTIVA
SUSPENDIDA
PENDIENTE
```

Código/slug no deben quedar vacíos.

Preferencia:
- `codigo` legible para login futuro;
- `slug` URL-safe y lowercase.

---

# 8. Tabla saas_control.plan_saas

Propósito:

```text
Planes comerciales disponibles.
```

Diseño:

```text
id                         BIGINT identity PK
codigo                     VARCHAR(30) NOT NULL UNIQUE
nombre                     VARCHAR(80) NOT NULL UNIQUE
descripcion                VARCHAR(255)
precio_mensual             NUMERIC(12,2) NOT NULL
moneda                     VARCHAR(3) NOT NULL DEFAULT 'BOB'
limite_usuarios            INTEGER NOT NULL
limite_almacenamiento_mb   INTEGER NOT NULL
estado                     BOOLEAN NOT NULL DEFAULT TRUE
fecha_creacion             TIMESTAMPTZ NOT NULL DEFAULT now()
```

Checks:

```text
precio_mensual >= 0
limite_usuarios > 0
limite_almacenamiento_mb > 0
```

Seed:

```text
BASICO
PROFESIONAL
EMPRESARIAL
```

Los precios son DEMO académicos y deben quedar claramente identificados como datos de demostración.

---

# 9. Tabla saas_control.suscripcion

Propósito:

```text
Historial de contratación de planes por empresa.
```

Diseño:

```text
id                 BIGINT identity PK
empresa_id         BIGINT NOT NULL FK empresa
plan_id            BIGINT NOT NULL FK plan_saas
fecha_inicio       DATE NOT NULL
fecha_fin          DATE NOT NULL
estado             VARCHAR(20) NOT NULL
renovacion_auto    BOOLEAN NOT NULL DEFAULT FALSE
fecha_creacion     TIMESTAMPTZ NOT NULL DEFAULT now()
```

Estados:

```text
PENDIENTE
ACTIVA
SUSPENDIDA
VENCIDA
CANCELADA
```

Check:

```text
fecha_fin >= fecha_inicio
```

NO poner UNIQUE(empresa_id), porque debe poder existir historial de suscripciones.

Para los datos demo puede existir una suscripción ACTIVA por empresa.

Crear índice útil por:

```text
empresa_id
estado
fecha_fin
```

---

# 10. Tabla saas_control.tenant_database

Propósito:

```text
Mapea una empresa a su base PostgreSQL física.
```

Diseño:

```text
id                       BIGINT identity PK
empresa_id               BIGINT NOT NULL UNIQUE FK empresa
database_name            VARCHAR(63) NOT NULL UNIQUE
host_alias               VARCHAR(100)
estado                   VARCHAR(20) NOT NULL
version_schema           VARCHAR(30)
fecha_provisionamiento   TIMESTAMPTZ
ultima_verificacion      TIMESTAMPTZ
fecha_creacion           TIMESTAMPTZ NOT NULL DEFAULT now()
```

Estados:

```text
PENDIENTE
PROVISIONANDO
ACTIVA
SUSPENDIDA
ERROR
```

En 7A:

```text
estado = PENDIENTE
fecha_provisionamiento = NULL
ultima_verificacion = NULL
```

IMPORTANTE:

`database_name` debe cumplir identificador PostgreSQL seguro.

Solo:

```text
a-z
0-9
_
```

y longitud <= 63.

No guardar hostname real si no es necesario.

`host_alias` puede quedar NULL inicialmente.

---

# 11. Tabla saas_control.saas_usuario

Propósito:

```text
Usuarios del CONTROL PLANE.
No son usuarios de una clínica.
```

Diseño:

```text
id              BIGINT identity PK
correo          VARCHAR(150) NOT NULL UNIQUE
password_hash   VARCHAR(255) NOT NULL
nombres         VARCHAR(100) NOT NULL
apellidos       VARCHAR(120) NOT NULL
rol             VARCHAR(30) NOT NULL
estado          BOOLEAN NOT NULL DEFAULT TRUE
fecha_creacion  TIMESTAMPTZ NOT NULL DEFAULT now()
ultimo_acceso   TIMESTAMPTZ
```

Roles iniciales:

```text
SUPERADMIN
OPERADOR
```

IMPORTANTE:

NO insertar contraseña plaintext.

En 7A preferimos NO seedear un usuario con contraseña.

El usuario SaaS inicial se creará posteriormente desde backend/script seguro usando Argon2.

---

# 12. Tabla saas_control.saas_bitacora

Propósito:

```text
Auditoría exclusiva del Control Plane.
```

Diseño:

```text
id                     BIGINT identity PK
saas_usuario_id        BIGINT NULL FK saas_usuario ON DELETE SET NULL
fecha_hora             TIMESTAMPTZ NOT NULL DEFAULT now()
ip                     INET
accion                 VARCHAR(100) NOT NULL
entidad_afectada       VARCHAR(100)
id_registro_afectado   BIGINT
descripcion            TEXT
resultado              VARCHAR(20)
```

Resultados:

```text
EXITO
ERROR
```

No registrar secretos.

---

# 13. Tabla saas_control.provisionamiento_tenant

Propósito:

```text
Rastrear la creación/preparación de las DB de tenants.
```

Diseño:

```text
id                    BIGINT identity PK
empresa_id            BIGINT NOT NULL FK empresa
tenant_database_id    BIGINT NOT NULL FK tenant_database
estado                VARCHAR(20) NOT NULL
paso_actual           VARCHAR(100)
intentos              INTEGER NOT NULL DEFAULT 0
fecha_inicio          TIMESTAMPTZ
fecha_fin             TIMESTAMPTZ
mensaje_error         TEXT
fecha_creacion        TIMESTAMPTZ NOT NULL DEFAULT now()
```

Estados:

```text
PENDIENTE
EN_PROCESO
COMPLETADO
ERROR
```

Check:

```text
intentos >= 0
```

En seed:

```text
estado = PENDIENTE
intentos = 0
```

---

# 14. Empresas demo

Crear EXACTAMENTE 7 empresas de demostración.

Son datos ficticios/académicos.

Sugerencia:

```text
1. Centro Oftalmológico Visión Clara
   codigo: VISION-CLARA
   slug: vision-clara
   db: tenant_vision_clara

2. Clínica Oftalmológica Norte
   codigo: OFTALMO-NORTE
   slug: oftalmo-norte
   db: tenant_oftalmo_norte

3. Centro Visual Oriental
   codigo: VISUAL-ORIENTAL
   slug: visual-oriental
   db: tenant_visual_oriental

4. Instituto de la Visión
   codigo: INSTITUTO-VISION
   slug: instituto-vision
   db: tenant_instituto_vision

5. OftalmoCare
   codigo: OFTALMOCARE
   slug: oftalmocare
   db: tenant_oftalmocare

6. Clínica Vista Sur
   codigo: VISTA-SUR
   slug: vista-sur
   db: tenant_vista_sur

7. Centro Médico Ocular
   codigo: MEDICO-OCULAR
   slug: medico-ocular
   db: tenant_medico_ocular
```

Usar:
- correos `.example` o dominios claramente demo;
- teléfonos/direcciones ficticios;
- NIT ficticios claramente documentados como demo.

NO usar datos personales reales.

---

# 15. Seed de planes

Crear:

```text
BASICO
PROFESIONAL
EMPRESARIAL
```

Distribuir las 7 empresas entre los tres planes.

Ejemplo:

```text
Visión Clara          → EMPRESARIAL
Oftalmo Norte         → PROFESIONAL
Visual Oriental       → PROFESIONAL
Instituto Visión      → EMPRESARIAL
OftalmoCare           → BASICO
Vista Sur             → BASICO
Centro Médico Ocular  → PROFESIONAL
```

Crear suscripciones demo:

```text
estado = ACTIVA
```

con rango de fecha válido y suficientemente amplio para la demostración.

No depender de IDs hardcodeados si se puede resolver por `codigo`.

El seed debe ser IDEMPOTENTE.

Ejecutarlo dos veces NO debe duplicar:

```text
planes
empresas
tenant_database
suscripciones equivalentes
provisionamientos pendientes
```

Usar `INSERT ... ON CONFLICT` o estrategia segura equivalente.

---

# 16. Integridad

Todas las FK deben indicar `ON DELETE` explícito.

Preferencia:

```text
empresa → tenant_database: RESTRICT/CASCADE según relación analizada
empresa → suscripcion: RESTRICT
empresa → provisionamiento: RESTRICT
plan → suscripcion: RESTRICT
saas_usuario → bitacora: SET NULL
```

No hacer cascadas destructivas sin justificación.

Crear índices para consultas administrativas frecuentes.

---

# 17. Verification script

`003_verify_control_plane.sql` debe ser READ-ONLY.

Debe verificar:

### Schema y tablas

```text
schema saas_control existe
7 tablas esperadas existen
```

### Datos

```text
empresas = 7
empresas > 5
planes = 3
tenant_database = 7
suscripciones activas >= 7
provisionamientos pendientes = 7
```

### Relaciones

Mostrar:

```text
empresa
plan
estado suscripción
database_name
estado DB
estado provisionamiento
```

en una consulta JOIN única.

### Seguridad

Comprobar privilegios:

```text
anon no tiene USAGE en saas_control
authenticated no tiene USAGE en saas_control
```

### Secretos

Inspección del schema debe mostrar que NO existen columnas:

```text
password_db
database_password
service_role
jwt_secret
api_key
```

No hace falta buscar contenido secreto arbitrario.

---

# 18. Rollback

Crear:

```text
004_rollback_control_plane.sql
```

Debe:

```text
DROP SCHEMA saas_control CASCADE
```

pero incluir advertencia visible:

```text
SOLO DESARROLLO / DEMO.
DESTRUCTIVO.
NO EJECUTAR EN PRODUCCIÓN SIN BACKUP.
```

NO ejecutarlo.

---

# 19. No hacer en 7A

NO:

- crear databases físicas tenant_*
- modificar DATABASE_URL
- modificar app/database/connection.py
- modificar app/database/session.py
- modificar login
- modificar JWT
- agregar tenant_id al JWT
- crear Tenant Connection Router
- tocar Angular
- tocar móvil
- implementar backup
- implementar restore
- implementar eventos en tiempo real
- ejecutar SQL en Supabase automáticamente
- modificar .env
- guardar secretos
- commit
- push
- merge

---

# 20. Validaciones locales

Como este bloque solo crea scripts SQL:

```text
git diff --check
```

Debe pasar.

Si existe un PostgreSQL local de pruebas disponible, se puede validar sintaxis en una DB descartable SOLO si no toca Supabase ni datos existentes.

No es obligatorio.

NO ejecutar contra la base productiva todavía.

---

# 21. Criterio de terminado 7A.1

```text
[ ] directorio database/saas_control
[ ] 001_create_control_plane.sql
[ ] 002_seed_demo_control_plane.sql
[ ] 003_verify_control_plane.sql
[ ] 004_rollback_control_plane.sql
[ ] schema con 7 tablas
[ ] seed 7 empresas
[ ] seed 3 planes
[ ] seed suscripciones
[ ] 7 tenant_database PENDIENTE
[ ] 7 provisionamientos PENDIENTE
[ ] seed idempotente
[ ] sin credenciales DB
[ ] anon/authenticated sin acceso
[ ] verification read-only
[ ] rollback no ejecutado
[ ] no cambios backend funcional
[ ] no commit/push/merge
```

Después de revisión se hará el **7A.2: ejecución controlada en Supabase + verificación real**.
