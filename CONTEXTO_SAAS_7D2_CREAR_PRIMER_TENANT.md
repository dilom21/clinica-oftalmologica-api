# CONTEXTO_SAAS_7D2_CREAR_PRIMER_TENANT.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica
**Bloque:** 7D.2 — Crear y provisionar el primer tenant físico
**Repositorio:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

Estado previo:

```text
7A ✅ Control Plane SaaS
7B ✅ Tenant Connection Router
7C ✅ Auth/JWT tenant-aware preparado
7D.1 ✅ Inventario y dry-run
```

Tenant objetivo:

```text
empresa_codigo = VISION-CLARA
database_name = tenant_vision_clara
estado_actual = PENDIENTE
```

Fuente:

```text
database = postgres
schema = public
27 tablas de negocio
```

En este bloque SÍ se permite crear físicamente:

```text
tenant_vision_clara
```

pero únicamente si TODOS los prechecks pasan.

---

# 2. Objetivo

Crear una base PostgreSQL limpia para VISION-CLARA y migrar:

```text
schema public
+
datos public
```

desde la base histórica `postgres`.

Después verificar:

```text
27 tablas
27 secuencias
estructura esencial
conteos fuente = conteos tenant
SELECT 1
TenantResolver
TenantEngineRegistry
```

Solo después:

```text
tenant_database.estado = ACTIVA
provisionamiento_tenant.estado = COMPLETADO
```

No realizar todavía cutover del frontend ni migrar routers clínicos.

---

# 3. Precondiciones obligatorias

Antes de cualquier mutación:

1. `VISION-CLARA` existe.
2. `tenant_database.database_name = tenant_vision_clara`.
3. Estado actual `PENDIENTE`.
4. Provisionamiento actual `PENDIENTE`.
5. `tenant_vision_clara` NO existe en `pg_database`.
6. Rol administrativo tiene `rolcreatedb = true`.
7. No existen dependencias críticas de `public` hacia schemas Supabase.
8. Cliente PostgreSQL compatible disponible:
   - pg_dump
   - pg_restore
   - psql opcional
9. No hay uso activo de la aplicación durante la ventana de snapshot.

Si cualquiera falla:

```text
STOP
```

No cambiar estados.

---

# 4. Descubrimiento de herramientas

Antes de declarar ausentes las herramientas, buscar:

```text
PATH
C:\Program Files\PostgreSQL\17\bin
C:\Program Files\PostgreSQL\16\bin
```

Preferir PostgreSQL 17.

No instalar automáticamente.

El provisioner puede aceptar:

```text
--pg-bin-dir
```

para indicar el directorio de binarios sin modificar PATH global.

---

# 5. Conexión

La aplicación actual usa Supabase Session Pooler en puerto 5432.

Para `pg_dump`/`pg_restore`, se puede usar una conexión PostgreSQL compatible siempre que el test real funcione.

Para `CREATE DATABASE`:

- usar conexión administrativa en autocommit;
- no ejecutar dentro de una transacción;
- nunca interpolar un nombre no validado;
- `database_name` proviene exclusivamente de `saas_control`.

No imprimir la cadena completa ni contraseña.

Si el pooler rechaza una operación administrativa:

```text
STOP
```

y reportar que hace falta una conexión directa/admin válida.

No adivinar ni reconstruir secretos.

---

# 6. Creación de la DB

Crear únicamente:

```text
tenant_vision_clara
```

Preferir una DB limpia.

NO usar:

```sql
TEMPLATE postgres
```

Puede usarse:

```sql
TEMPLATE template0
```

si el servidor lo permite y no introduce dependencias no deseadas.

Owner coherente con el rol administrativo.

Antes de crear, confirmar que no existe.

No ejecutar DROP DATABASE automáticamente.

---

# 7. Estados de provisionamiento

Solo cuando todos los prechecks y herramientas hayan pasado:

Antes de crear/restaurar:

```text
tenant_database.estado = PROVISIONANDO
provisionamiento_tenant.estado = EN_PROCESO
provisionamiento_tenant.intentos += 1
provisionamiento_tenant.fecha_inicio = now()
provisionamiento_tenant.paso_actual = ...
```

Si ocurre un error después de iniciar:

```text
tenant_database.estado = ERROR
provisionamiento_tenant.estado = ERROR
provisionamiento_tenant.fecha_fin = now()
mensaje_error = saneado
```

NO guardar:

```text
password
DATABASE_URL
host completo sensible
trace con credenciales
```

NO hacer DROP automático de una DB parcialmente creada.

---

# 8. Dump fuente

Usar:

```text
pg_dump
```

formato custom:

```text
-Fc
```

Fuente:

```text
database postgres
schema public
```

Opciones esperadas:

```text
--schema=public
--no-owner
--no-privileges
```

El dump debe contener schema + datos.

No incluir otros schemas.

El archivo temporal debe quedar FUERA del repositorio, por ejemplo bajo `%TEMP%`.

No commitear dumps.

No registrar el contenido.

---

# 9. Credenciales para subprocess

Evitar password en argumentos de línea de comandos.

Preferir variables de entorno del subproceso:

```text
PGPASSWORD
PGSSLMODE=require
```

y argumentos separados:

```text
-h
-p
-U
-d
```

No imprimir `PGPASSWORD`.

No persistirla en archivos.

---

# 10. Restore

Restaurar al target:

```text
tenant_vision_clara
```

Usar:

```text
pg_restore
```

con:

```text
--no-owner
--no-privileges
--exit-on-error
```

Como la DB es nueva, resolver correctamente el schema `public` preexistente.

Si se necesita:

```text
--clean
--if-exists
```

solo contra la base nueva `tenant_vision_clara`.

Nunca ejecutar clean contra `postgres`.

---

# 11. Verificación estructural

Después del restore ejecutar verificaciones READ-ONLY sobre el target.

Debe haber:

```text
27 tablas
27 secuencias
0 vistas
0 materialized views
0 funciones de negocio
0 triggers
```

y estructura equivalente en:

```text
columnas
tipos
nullable
PK
FK
UNIQUE
índices
```

Usar el script:

```text
database/tenants/vision_clara/003_verify_tenant.sql
```

Mejorarlo solo si detecta una carencia real de verificación.

---

# 12. Verificación de conteos

Comparar cada tabla fuente vs target.

Conteos de referencia de 7D.1:

```text
accion                    3
antecedente_clinico       0
bitacora                436
bloqueo_horario           3
cita                      8
consulta_clinica          2
control_medico            1
detalle_receta            1
diagnostico               1
examen_oftalmologico      3
funcion                  19
historial_clinico        10
horario_oftalmologo      11
indicacion                4
modulo                    6
oftalmologo               1
paciente                 10
receta                    1
resultado_examen          2
rol                       4
rol_funcion              39
servicio_oftalmologico    2
servicio_realizado        2
servicios_oftalmologicos  2
token_recuperacion       17
tratamiento               1
usuario                  14
```

IMPORTANTE:

Antes del dump, volver a obtener conteos reales.
Esos conteos de PRE-DUMP son la fuente de verdad.

Si hubo cambios desde 7D.1:

```text
usar los nuevos conteos
```

y reportar el delta.

Después del restore:

```text
cada tabla target == snapshot pre-dump
```

Si no coincide:

```text
NO activar tenant
```

---

# 13. Verificación de schemas no deseados

En `tenant_vision_clara` NO deben existir schemas SaaS/Supabase clonados como:

```text
saas_control
auth
storage
realtime
vault
graphql
graphql_public
supabase_functions
```

Schemas estándar PostgreSQL son válidos.

---

# 14. Health check

Con target restaurado:

```text
SELECT 1
```

debe pasar.

Luego usar código real:

```text
TenantResolver
TenantEngineRegistry
check_tenant_connection
```

El router debe ser capaz de conectar al target cuando el estado sea ACTIVA.

Para evitar un ciclo lógico, puede hacerse primero una verificación SQL directa y después marcar ACTIVA; inmediatamente después de ACTIVA ejecutar el health check mediante TenantEngineRegistry.

---

# 15. Activación

Solo si:

```text
restore OK
estructura OK
conteos OK
schemas OK
SELECT 1 OK
```

actualizar Control Plane:

```text
tenant_database.estado = ACTIVA
tenant_database.version_schema = versión definida
tenant_database.fecha_provisionamiento = now()
tenant_database.ultima_verificacion = now()

provisionamiento_tenant.estado = COMPLETADO
provisionamiento_tenant.paso_actual = COMPLETADO
provisionamiento_tenant.fecha_fin = now()
provisionamiento_tenant.mensaje_error = NULL
```

Después:

```text
TenantResolver(VISION-CLARA)
```

debe devolver DB ACTIVA.

---

# 16. Verificación del TenantEngineRegistry real

Después de ACTIVA:

1. Resolver `VISION-CLARA`.
2. Crear engine lazy.
3. Ejecutar `SELECT 1`.
4. Consultar solo conteos sencillos:
   - usuario
   - paciente
   - cita

No modificar datos.

No imprimir PII.

---

# 17. Login tenant

No es obligatorio automatizar login exitoso si no existe una contraseña de prueba conocida de forma segura.

NO pedir ni imprimir passwords.

La validación de login tenant E2E se hará manualmente después con una cuenta conocida por el usuario.

En este bloque debe quedar demostrado:

```text
tenant DB ACTIVA
session real funciona
usuarios fueron migrados
```

---

# 18. Limpieza del dump temporal

Solo después de verificación exitosa:

```text
eliminar dump temporal
```

Si el proceso falla:

```text
conservarlo solo durante diagnóstico local
```

y nunca moverlo al repositorio.

No commitear dumps.

---

# 19. Provisioner

Extender:

```text
scripts/saas/provision_tenant.py
```

Agregar modo explícito de ejecución, por ejemplo:

```text
--execute
```

y una confirmación fuerte:

```text
--confirm tenant_vision_clara
```

Ejemplo conceptual:

```powershell
python scripts/saas/provision_tenant.py ^
  --empresa VISION-CLARA ^
  --execute ^
  --confirm tenant_vision_clara ^
  --pg-bin-dir "C:\Program Files\PostgreSQL\17\bin"
```

Sin ambos flags:

```text
NO mutar nada
```

Mantener `--dry-run`.

---

# 20. Idempotencia/seguridad del provisioner

Si la DB ya existe:

```text
NO volver a crear
```

Si Control Plane ya está ACTIVA:

```text
NO reprovisionar
```

Si está ERROR:

```text
NO borrar/reintentar automáticamente
```

Requiere flujo explícito posterior.

---

# 21. Tests

Extender:

```text
tests/test_tenant_provisioning.py
```

Cubrir mínimo:

1. dry-run sigue sin mutar;
2. execute requiere confirmación;
3. confirm incorrecto rechaza;
4. DB existente rechaza;
5. tenant no PENDIENTE rechaza;
6. prechecks antes de estados;
7. estado PROVISIONANDO al iniciar;
8. error → ERROR;
9. success → ACTIVA/COMPLETADO;
10. pg_dump solo public;
11. pg_restore target correcto;
12. password no va en argv;
13. PGPASSWORD no se imprime;
14. dump fuera repo;
15. no TEMPLATE postgres;
16. conteos desiguales impiden ACTIVA;
17. schema no deseado impide ACTIVA;
18. health check falla → no ACTIVA;
19. dispose/registry tras activación;
20. dump temporal se elimina tras éxito.

No llamar al tenant real en tests unitarios.

---

# 22. Ejecución real

Solo después de tests:

```text
1. dry-run
2. preflight
3. execute real VISION-CLARA
4. verify
```

No ejecutar contra las otras 6 empresas.

---

# 23. No hacer

NO:

- crear otros tenant_*
- DROP DATABASE
- TEMPLATE postgres
- modificar datos fuente
- modificar login legacy
- hacer cutover frontend
- migrar routers clínicos
- backup/restore de usuarios finales
- realtime
- frontend
- móvil
- .env
- commit
- push
- merge

---

# 24. Suite

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

---

# 25. Reporte final

Entregar:

```text
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
14. LOGIN TENANT (si se probó manualmente, sin credenciales)
15. ARCHIVOS MODIFICADOS
16. TESTS BLOQUE
17. SUITE COMPLETA
18. DUMP TEMPORAL
19. ERRORES
20. GIT STATUS
```

Confirmar:

```text
solo tenant_vision_clara creado
source intacto
saas_control no copiado al tenant
auth/storage no copiados
sin secretos impresos
sin commit/push/merge
```
