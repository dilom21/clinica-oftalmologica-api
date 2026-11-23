# CONTEXTO_SAAS_7A2_EJECUCION_SUPABASE.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica
**Bloque:** 7A.2 — Ejecución controlada del Control Plane SaaS
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`
**Proyecto Supabase:** `clinica-oftalmologica`
**Project Ref:** `ypnkjfsymxrukjdyhyyc`

Este bloque toma los scripts aprobados en 7A.1:

```text
database/saas_control/001_create_control_plane.sql
database/saas_control/002_seed_demo_control_plane.sql
database/saas_control/003_verify_control_plane.sql
database/saas_control/004_rollback_control_plane.sql
```

y ejecuta de forma CONTROLADA solo:

```text
001
002
003
```

`004_rollback_control_plane.sql` NO se ejecuta.

## 2. Objetivo

Dejar creado realmente en Supabase:

```text
schema saas_control
```

con:

```text
7 tablas
3 planes
7 empresas
7 suscripciones activas
7 tenant_database PENDIENTE
7 provisionamientos PENDIENTE
```

Todavía NO se crean las bases físicas `tenant_*`.

## 3. Regla de seguridad

Antes de ejecutar cualquier DDL:

1. Confirmar que el proyecto Supabase objetivo es `ypnkjfsymxrukjdyhyyc`.
2. Hacer preflight READ-ONLY.
3. Si `saas_control` ya existe con tablas/datos inesperados: DETENERSE. No borrar. No ejecutar rollback.
4. No modificar `public`, `auth`, `storage`, tablas clínicas actuales, funciones actuales ni RLS existente.
5. No ejecutar `004_rollback_control_plane.sql`.

## 4. Preflight obligatorio

Ejecutar consultas READ-ONLY para comprobar si existe `saas_control` y listar sus tablas si existe.

Si no existe: continuar.

Si existe pero está vacío: reportar antes de continuar y solo continuar si corresponde claramente a un intento previo de este mismo bloque.

Si contiene estructura inesperada: STOP.

## 5. Ejecución 001

Ejecutar:

```text
database/saas_control/001_create_control_plane.sql
```

como migración/DDL.

Después verificar inmediatamente que exista el schema y estas 7 tablas:

```text
empresa
plan_saas
suscripcion
tenant_database
saas_usuario
saas_bitacora
provisionamiento_tenant
```

Si falla:
- no ejecutar 002;
- reportar error exacto saneado;
- no hacer rollback destructivo automáticamente.

## 6. Ejecución 002

Solo si 001 fue correcto:

Ejecutar:

```text
database/saas_control/002_seed_demo_control_plane.sql
```

Después verificar:

```text
planes = 3
empresas = 7
suscripciones ACTIVA = 7
tenant_database PENDIENTE = 7
provisionamiento PENDIENTE = 7
```

## 7. Prueba de idempotencia real

Volver a ejecutar `002_seed_demo_control_plane.sql` una SEGUNDA vez.

Debe seguir existiendo exactamente:

```text
3 planes
7 empresas
7 tenant_database
7 suscripciones equivalentes
7 provisionamientos equivalentes
```

Si aumenta algún conteo: idempotencia fallida.

## 8. Ejecución 003

Ejecutar:

```text
database/saas_control/003_verify_control_plane.sql
```

Debe ser READ-ONLY.

Guardar/reportar sus resultados.

## 9. JOIN de defensa

La salida final debe mostrar al menos:

```text
empresa
codigo
plan
estado_suscripcion
database_name
estado_database
estado_provisionamiento
```

Debe haber 7 empresas.

## 10. Seguridad real

Verificar realmente:

```text
anon no tiene USAGE en schema saas_control
authenticated no tiene USAGE en schema saas_control
```

Verificar también que no haya grants directos sobre tablas/secuencias.

No habilitar RLS como sustituto. Este schema es backend-only.

## 11. Verificación de secretos

Confirmar que NO existan columnas como:

```text
database_password
password_db
database_url
service_role
jwt_secret
api_key
smtp_password
deepseek_api_key
```

`saas_usuario.password_hash` sí es válido.

## 12. No hacer

NO:
- ejecutar rollback
- crear tenant databases físicas
- cambiar DATABASE_URL
- cambiar login
- cambiar JWT
- crear usuario SaaS
- modificar frontend
- modificar móvil
- implementar backup/restore
- implementar Tenant Router
- alterar public/auth/storage
- habilitar/deshabilitar RLS en tablas existentes
- commit/push/merge

## 13. Evidencia final requerida

Entregar:

```text
1. PREFLIGHT
2. EJECUCIÓN 001
3. TABLAS REALES
4. EJECUCIÓN 002
5. CONTEOS TRAS PRIMER SEED
6. SEGUNDO SEED / IDEMPOTENCIA
7. EJECUCIÓN 003
8. JOIN DE 7 EMPRESAS
9. PRIVILEGIOS anon/authenticated
10. SECRETOS/COLUMNAS SENSIBLES
11. CAMBIOS EN OTROS SCHEMAS
12. ERRORES
13. GIT STATUS
```

## 14. Criterio de aprobación

7A.2 queda aprobado solo si:

```text
[ ] schema real existe
[ ] 7 tablas reales
[ ] 7 empresas reales
[ ] >5 empresas
[ ] 3 planes
[ ] 7 suscripciones activas
[ ] 7 tenant_database PENDIENTE
[ ] 7 provisionamientos PENDIENTE
[ ] seed idempotente real
[ ] anon sin acceso
[ ] authenticated sin acceso
[ ] sin secretos DB
[ ] public/auth/storage intactos
[ ] rollback no ejecutado
```
