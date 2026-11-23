# CONTEXTO_SAAS_7D1_PREPARAR_PRIMER_TENANT.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Bloque:** 7D.1 — Preparación del primer tenant físico  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

Estado previo:

```text
7A ✅ Control Plane SaaS
7B ✅ Tenant Connection Router
7C ✅ Auth/JWT Tenant-Aware preparado
```

Control Plane real:

```text
VISION-CLARA
database_name = tenant_vision_clara
estado_database = PENDIENTE
```

En 7D.1 TODAVÍA NO se crea `tenant_vision_clara`.

Primero se debe auditar exactamente qué debe copiarse de la base principal y dejar listo un proceso reproducible y seguro.

---

# 2. Decisión de migración para el primer tenant

La base actual:

```text
postgres
```

es el sistema single-tenant histórico.

Para la transición SaaS se considerará que los datos clínicos actuales de `public` pertenecen a:

```text
Centro Oftalmológico Visión Clara
codigo = VISION-CLARA
database_name = tenant_vision_clara
```

Por tanto, el primer tenant deberá recibir:

```text
schema clínico de public
+
datos clínicos actuales de public
```

para conservar:

```text
usuarios
roles/permisos
pacientes
oftalmólogos
citas
historial
consultas
diagnósticos
tratamientos
etc.
```

No migrar servicios Supabase administrados.

---

# 3. Qué NO copiar

NO copiar hacia `tenant_vision_clara`:

```text
saas_control
auth
storage
realtime
extensions
vault
graphql
graphql_public
supabase_functions
```

ni cualquier otro schema administrado por Supabase.

Tampoco copiar:

```text
Control Plane SaaS
metadata de tenants
backups SaaS
```

El tenant debe contener solamente la parte de negocio que FastAPI utiliza directamente.

---

# 4. Caveat Supabase

Las bases adicionales dentro del mismo cluster PostgreSQL:

```text
NO aparecen normalmente en Supabase Dashboard
NO reciben automáticamente PostgREST/Auth/Storage/Realtime del proyecto
```

Eso es aceptable para esta arquitectura porque:

```text
Angular/Móvil
     ↓
FastAPI
     ↓
SQLAlchemy
     ↓
PostgreSQL tenant DB
```

FastAPI accede directamente a PostgreSQL.

El proyecto NO debe depender de PostgREST para las tablas clínicas tenant.

---

# 5. Objetivo 7D.1

Realizar PREPARACIÓN y DRY-RUN, no creación.

Entregables:

1. Inventario exacto del schema `public`.
2. Dependencias de objetos `public` hacia schemas externos.
3. Conteos fuente.
4. Herramientas disponibles (`pg_dump`, `pg_restore`, `psql`).
5. Tipo de conexión actual (directa/pooler) saneado.
6. Privilegio para crear databases.
7. Estrategia reproducible de dump/restore.
8. Scripts de preflight/verificación.
9. Provisioner preparado en modo dry-run o plan, pero NO ejecutar CREATE DATABASE.
10. Pruebas unitarias del provisioner si se crea código.

---

# 6. Inventario fuente

Consultar READ-ONLY la base actual.

Inventariar `public`:

```text
tables
views
materialized views
sequences
functions
procedures
triggers
custom types
indexes
constraints
foreign keys
```

Reportar nombres y conteos.

No alterar nada.

---

# 7. Dependencias externas críticas

Buscar específicamente objetos del schema `public` que referencien:

```text
auth.
storage.
saas_control.
realtime.
extensions.
```

Revisar:

```text
FK
views
functions
triggers
defaults
policies
```

Si una tabla clínica depende obligatoriamente de `auth` o `storage`:

```text
DETENER diseño automático
REPORTAR dependencia
```

No copiar a ciegas.

---

# 8. Tablas fuente

El repositorio reportó previamente alrededor de 27 tablas `public`.

Confirmar la lista REAL.

Generar una clasificación:

```text
NEGOCIO_TENANT
NO_TENANT
DUDOSA
```

En principio, las tablas clínicas/seguridad de aplicación son `NEGOCIO_TENANT`.

Ejemplos esperados:

```text
usuario
rol
modulo
funcion
accion
rol_funcion
bitacora
paciente
oftalmologo
cita
historial_clinico
antecedente_clinico
consulta_clinica
diagnostico
tratamiento
receta
detalle_receta
examen_oftalmologico
resultado_examen
control_medico
servicio_oftalmologico
servicio_realizado
...
```

No asumir: obtener inventario real.

---

# 9. Conteos fuente

Crear:

```text
database/tenants/vision_clara/001_source_inventory.sql
```

READ-ONLY.

Debe obtener conteos de cada tabla de negocio.

Guardar un resumen que permita comparar post-restore:

```text
tabla | filas_origen
```

No incluir contenido sensible, solo conteos.

---

# 10. Fingerprint de schema

Crear un mecanismo reproducible para verificar que el tenant tiene la misma estructura esencial.

Como mínimo comparar:

```text
tablas
columnas
tipos
nullable
PK
FK
UNIQUE
índices críticos
funciones/triggers requeridos
```

Crear:

```text
database/tenants/vision_clara/003_verify_tenant.sql
```

READ-ONLY para ejecutar DESPUÉS del provisionamiento.

---

# 11. Dump/Restore

Preferencia para migración inicial:

```text
pg_dump / pg_restore
```

No usar:

```text
JSON export
CSV tabla por tabla
INSERT manual masivo
```

Preparar estrategia para:

```text
schema public
+
data public
```

con exclusión explícita de schemas Supabase.

Usar flags seguros cuando apliquen:

```text
--no-owner
--no-privileges
```

No incluir passwords en comandos/documentos.

---

# 12. Herramientas locales

Comprobar:

```powershell
pg_dump --version
pg_restore --version
psql --version
```

No instalar automáticamente nada.

Si faltan, reportarlo.

Registrar versión.

Idealmente usar PostgreSQL client >= versión compatible con servidor PostgreSQL 17.

---

# 13. Conexión actual

Inspeccionar configuración SIN mostrar secretos.

Reportar únicamente:

```text
driver
host clasificado como direct/pooler/otro
port
database actual
username parcialmente saneado si hace falta
```

NO imprimir:

```text
password
DATABASE_URL completa
query secrets
```

Determinar si el `DATABASE_URL` actual puede utilizarse para tenant DB o si para operaciones administrativas se necesita una conexión DIRECTA específica.

NO modificar `.env`.

---

# 14. Privilegios de creación

Hacer consulta READ-ONLY para saber si el rol actual puede crear databases.

Verificar el rol efectivo y capacidad `rolcreatedb`.

No ejecutar:

```sql
CREATE DATABASE
```

en 7D.1.

Si no hay privilegio:
reportarlo antes de diseñar ejecución.

---

# 15. Provisioner

Preparar un script, preferentemente:

```text
scripts/saas/provision_tenant.py
```

pero en 7D.1 solo debe soportar:

```text
--dry-run
```

El dry-run puede:

```text
leer metadata saas_control
validar empresa
validar database_name
mostrar pasos saneados
comprobar herramientas
comprobar fuente
```

NO puede:

```text
CREATE DATABASE
DROP DATABASE
pg_restore real
cambiar estado Control Plane
```

sin un flag explícito que NO se usará todavía.

Preferencia: en 7D.1 NO implementar el modo destructivo/real todavía si no es necesario.

---

# 16. Plan futuro 7D.2

El proceso real posterior será aproximadamente:

```text
1. Revalidar Control Plane
2. Marcar PROVISIONANDO
3. Crear tenant_vision_clara
4. Restaurar schema public
5. Restaurar datos public
6. Verificar estructura
7. Verificar conteos
8. Probar SELECT 1
9. Probar login tenant
10. Marcar tenant_database ACTIVA
11. Marcar provisionamiento COMPLETADO
```

Si algo falla:

```text
estado ERROR
mensaje saneado
```

NO implementar/correr todavía todos estos pasos en 7D.1.

---

# 17. No clonar database completa

NO usar:

```sql
CREATE DATABASE tenant_vision_clara TEMPLATE postgres;
```

La base `postgres` contiene schemas de Supabase y `saas_control`.

No queremos clonarlos.

---

# 18. No tocar fuente

NO:

```text
DELETE
UPDATE
ALTER
DROP
TRUNCATE
```

en `public` actual.

Este bloque es read-only respecto de Supabase.

---

# 19. Tests

Si se crea `provision_tenant.py`, crear:

```text
tests/test_tenant_provisioning.py
```

Cubrir:

1. dry-run no crea database;
2. dry-run no cambia Control Plane;
3. rechaza database_name inválido;
4. solo permite tenant PENDIENTE para provisionar;
5. no acepta database_name desde CLI arbitrario si no coincide con Control Plane;
6. no imprime DATABASE_URL;
7. no imprime password;
8. no usa TEMPLATE postgres;
9. genera/selecciona únicamente public;
10. excluye saas_control/auth/storage;
11. herramientas faltantes dan error controlado;
12. conteos/fingerprint son read-only.

Ejecutar suite completa.

---

# 20. Archivos esperados

Preferencia:

```text
database/tenants/vision_clara/
  001_source_inventory.sql
  002_dependency_audit.sql
  003_verify_tenant.sql
  README.md

scripts/saas/
  provision_tenant.py

tests/
  test_tenant_provisioning.py
```

Adaptar si existe una convención mejor.

---

# 21. No hacer

NO:

- CREATE DATABASE
- DROP DATABASE
- CREATE SCHEMA remoto
- dump de datos a un archivo con información sensible que se commitee
- restore
- cambiar tenant_database a PROVISIONANDO/ACTIVA
- cambiar provisionamiento
- modificar login/JWT
- migrar routers
- tocar frontend/móvil
- backup/restore de usuario
- realtime
- .env
- commit/push/merge

---

# 22. Validación

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

También ejecutar el dry-run si quedó implementado.

---

# 23. Reporte final

Entregar:

```text
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
```

Confirmar:

```text
NO DB tenant creada
NO restore
NO estados cambiados
NO datos fuente modificados
NO secretos impresos
NO commit/push/merge
```
