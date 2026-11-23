# MASTER_PROMPT_SAAS_7E_SEGUNDO_TENANT_AISLAMIENTO.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 7E — SEGUNDO TENANT + AISLAMIENTO A/B

Lee COMPLETOS:

- CONTEXTO_SAAS_7E_SEGUNDO_TENANT_AISLAMIENTO.md
- MASTER_PROMPT_SAAS_7E_SEGUNDO_TENANT_AISLAMIENTO.md
- scripts/saas/provision_tenant.py
- tests/test_tenant_provisioning.py
- tests/test_auth_tenant.py
- app/core/tenancy/
- implementación auth tenant

==================================================
ESTADO PREVIO
==================================================

VISION-CLARA:

tenant_vision_clara
ACTIVA
COMPLETADO
login tenant real = 200

OFTALMO-NORTE:

tenant_oftalmo_norte
esperado PENDIENTE
DB física esperada inexistente

==================================================
OBJETIVO
==================================================

Crear tenant_oftalmo_norte como tenant NUEVO/LIMPIO.

NO copiar datos clínicos ni usuarios de VISION-CLARA.

Resultado:

mismo schema
catálogos base
1 admin propio
0 pacientes
0 citas
0 consultas
0 diagnósticos

y demostrar aislamiento A/B.

==================================================
1. PREFLIGHT
==================================================

READ-ONLY:

- OFTALMO-NORTE existe
- database_name = tenant_oftalmo_norte
- tenant_database = PENDIENTE
- provisionamiento = PENDIENTE
- DB física no existe
- schema v1 fuente válido
- herramientas PostgreSQL 18 disponibles

Si falla:
STOP.

==================================================
2. CLASIFICAR SEED
==================================================

Audita las 27 tablas y define explícitamente:

CATALOGO_BASE
TENANT_VACIA
BOOTSTRAP

Esperado CATALOGO_BASE:

- accion
- modulo
- funcion
- rol
- rol_funcion

BOOTSTRAP:

- usuario

TENANT_VACIA:

tablas clínicas/transaccionales.

No asumas IDs sin verificar.

Si necesitas otra tabla base:
justifica dependencia antes de incluirla.

==================================================
3. PROVISION CLEAN
==================================================

Extiende el provisioner con un modo explícito:

--provision-clean

Debe requerir:

--empresa OFTALMO-NORTE
--confirm tenant_oftalmo_norte
--bootstrap-admin-email <correo>

Password:

NO aceptarlo como argumento.

Solicitarlo de forma interactiva con getpass.

Usar hashing real del proyecto.

No imprimirlo.

==================================================
4. SCHEMA ONLY
==================================================

Para OFTALMO-NORTE usar:

pg_dump
-Fc
--schema=public
--schema-only
--no-owner
--no-privileges

NO data dump completo.

NO clonar desde tenant_vision_clara.

Fuente canónica:

postgres.public

Restore a:

tenant_oftalmo_norte

==================================================
5. SEED CATÁLOGOS
==================================================

Después del schema restore:

seedear únicamente catálogos base aprobados.

Preservar integridad:

modulo
funcion
accion
rol
rol_funcion

Verificar counts y FKs.

NO copiar:

paciente
cita
historial
consulta
diagnostico
receta
bitacora
token_recuperacion
usuarios existentes

==================================================
6. BOOTSTRAP ADMIN
==================================================

Crear un único administrador clínico propio.

El correo viene de:

--bootstrap-admin-email

La contraseña se pide localmente con getpass.

Confirmar contraseña sin mostrarla.

Usar mismo algoritmo de hash del login real.

Asociar al rol Administrador real por búsqueda semántica,
NO por ID hardcodeado.

usuario count esperado:

1

==================================================
7. VERIFY CLEAN TENANT
==================================================

Verificar:

estructura equivalente a schema v1.

Además:

paciente = 0
cita = 0
historial_clinico = 0
consulta_clinica = 0
diagnostico = 0
receta = 0
bitacora = 0
token_recuperacion = 0

usuario = 1

Si alguna tabla transaccional contiene datos inesperados:
NO activar.

==================================================
8. ACTIVACIÓN
==================================================

Solo si todo pasa:

tenant_database:
ACTIVA
version_schema = v1

provisionamiento:
COMPLETADO

Después:

TenantResolver("OFTALMO-NORTE")
TenantEngineRegistry
SELECT 1

==================================================
9. LOGIN REAL
==================================================

Con backend local levantado, el usuario realizará manualmente:

POST /seguridad/tenant/login

empresa_codigo = OFTALMO-NORTE
correo = bootstrap admin
password = ingresado localmente

Esperado:

200

No imprimir token completo.

==================================================
10. AISLAMIENTO A/B
==================================================

Demostrar:

Resolver VISION-CLARA:
database = tenant_vision_clara

Resolver OFTALMO-NORTE:
database = tenant_oftalmo_norte

Deben ser distintas.

Conteos:

VISION-CLARA:
usuario ≈ 14
paciente ≈ 10
cita ≈ 8

OFTALMO-NORTE:
usuario = 1
paciente = 0
cita = 0

Usar valores reales actuales si cambiaron.

==================================================
11. LOGIN CRUZADO
==================================================

Con cuentas EXCLUSIVAS:

Admin OFTALMO-NORTE contra VISION-CLARA:
→ Credenciales inválidas

Cuenta VISION-CLARA contra OFTALMO-NORTE:
→ Credenciales inválidas

No realizar muchos intentos.
No bloquear cuentas.

==================================================
12. ENGINE ISOLATION
==================================================

Comprobar:

- engines distintos
- sessionmakers distintos
- cache key distinta
- dispose de B no elimina A
- database_name no viene del cliente

No imprimir URLs.

==================================================
13. TESTS
==================================================

Agregar tests para:

- provision-clean
- schema-only
- catálogos base
- transaccionales vacías
- bootstrap único
- getpass
- hash
- no password argv
- no secrets logs
- wrong-company login
- resolver A/B
- engines separados
- dispose independiente
- activación fail-closed

Ejecutar:

.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
14. NO HACER
==================================================

NO:

- copiar datos clínicos de VISION-CLARA
- copiar sus 14 usuarios
- modificar tenant_vision_clara
- crear otros tenants
- migrar routers clínicos
- cutover frontend
- backup/restore
- realtime
- .env
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. PREFLIGHT
3. CLASIFICACIÓN DE TABLAS
4. SCHEMA-ONLY
5. CATÁLOGOS SEEDEADOS
6. BOOTSTRAP ADMIN
7. ESTRUCTURA TARGET
8. CONTEOS TENANT LIMPIO
9. CONTROL PLANE
10. TENANT RESOLVER
11. ENGINE REGISTRY
12. LOGIN OFTALMO-NORTE
13. LOGIN CRUZADO A→B
14. LOGIN CRUZADO B→A
15. AISLAMIENTO DE DATOS
16. TESTS BLOQUE
17. SUITE COMPLETA
18. SECRETOS
19. GIT STATUS

Confirma:

- tenant_vision_clara intacto
- datos clínicos NO copiados
- usuarios VISION-CLARA NO copiados
- password bootstrap nunca impreso
- no secrets
- no commit/push/merge
