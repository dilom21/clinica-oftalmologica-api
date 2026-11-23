# MASTER_PROMPT_SAAS_7A2_EJECUCION_SUPABASE.md

Trabaja EXCLUSIVAMENTE con:

```text
Repositorio:
C:\SI2_Proyecto\clinica-oftalmologica-api

Supabase:
Project Ref = ypnkjfsymxrukjdyhyyc
```

Estamos en:

```text
PASO 7A.2 — EJECUCIÓN CONTROLADA CONTROL PLANE SaaS
```

Lee COMPLETOS:

```text
CONTEXTO_SAAS_7A2_EJECUCION_SUPABASE.md
MASTER_PROMPT_SAAS_7A2_EJECUCION_SUPABASE.md
```

Y los scripts:

```text
database/saas_control/001_create_control_plane.sql
database/saas_control/002_seed_demo_control_plane.sql
database/saas_control/003_verify_control_plane.sql
database/saas_control/004_rollback_control_plane.sql
```

IMPORTANTE:

`004_rollback_control_plane.sql` SOLO se lee.
NO se ejecuta.

==================================================
1. PREFLIGHT
==================================================

Antes de DDL, consulta Supabase READ-ONLY.

Confirma Project Ref exacto:

ypnkjfsymxrukjdyhyyc

Consulta si existe `saas_control`.

Si NO existe: continuar.

Si existe: lista sus tablas y conteos.

Si encuentras estructura/datos inesperados:
DETENTE.
NO borres.
NO hagas rollback.

==================================================
2. EJECUTAR 001
==================================================

Ejecuta como DDL/migración:

database/saas_control/001_create_control_plane.sql

Después verifica:

schema: saas_control

tablas esperadas:
- empresa
- plan_saas
- suscripcion
- tenant_database
- saas_usuario
- saas_bitacora
- provisionamiento_tenant

Si falla:
STOP.
No ejecutar 002.
No rollback automático.

==================================================
3. EJECUTAR 002
==================================================

Solo con 001 correcto.

Ejecuta:

database/saas_control/002_seed_demo_control_plane.sql

Comprueba:

planes = 3
empresas = 7
suscripciones ACTIVA = 7
tenant_database PENDIENTE = 7
provisionamiento PENDIENTE = 7

==================================================
4. PROBAR IDEMPOTENCIA REAL
==================================================

Ejecuta 002 UNA SEGUNDA VEZ.

Luego vuelve a contar.

Debe seguir:

planes = 3
empresas = 7
tenant_database = 7

y NO deben aparecer suscripciones/provisionamientos duplicados equivalentes.

Si cambian indebidamente los conteos:
reporta fallo de idempotencia.

==================================================
5. EJECUTAR 003
==================================================

Ejecuta:

database/saas_control/003_verify_control_plane.sql

Debe ser READ-ONLY.

No modifiques 003 para ocultar un fallo.

==================================================
6. EVIDENCIA SAAS
==================================================

Quiero ver en la salida un JOIN con las 7 empresas y:

empresa
codigo
plan
estado_suscripcion
database_name
estado_database
estado_provisionamiento

Los tenant databases todavía deben estar PENDIENTE porque todavía NO existen físicamente.

==================================================
7. SEGURIDAD
==================================================

Comprueba de forma REAL:

anon:
- NO USAGE saas_control

authenticated:
- NO USAGE saas_control

y sin acceso directo a tablas/secuencias.

No cambies RLS de public.

==================================================
8. SECRETOS
==================================================

Confirma por metadatos que saas_control NO almacena:

database password
DATABASE_URL
service_role
JWT secret
API keys
SMTP password
DeepSeek key

password_hash de saas_usuario es válido.

==================================================
9. NO TOCAR
==================================================

NO:
- 004 rollback
- tenant DB físicas
- public
- auth
- storage
- login
- JWT
- connection.py
- session.py
- Angular
- móvil
- backups
- restore
- realtime
- .env
- commit
- push
- merge

==================================================
10. REPORTE FINAL
==================================================

Entrega exactamente:

1. PREFLIGHT
2. EJECUCIÓN 001
3. TABLAS REALES
4. EJECUCIÓN 002
5. CONTEOS PRIMER SEED
6. SEGUNDO SEED / IDEMPOTENCIA
7. EJECUCIÓN 003
8. JOIN 7 EMPRESAS
9. SEGURIDAD anon/authenticated
10. SECRETOS
11. OTROS SCHEMAS
12. ERRORES
13. GIT STATUS

Incluye conteos reales.

Confirma:
- NO rollback
- NO tenant DB físicas
- NO cambios login/JWT
- NO cambios backend funcional
- NO frontend
- NO commit/push/merge
