# MASTER_PROMPT_SAAS_7E1_CORREGIR_Y_RECONCILIAR_OFTALMO_NORTE.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 7E.1 — CORREGIR LITERAL SQL Y RECONCILIAR OFTALMO-NORTE

Lee COMPLETOS:
- CONTEXTO_SAAS_7E1_CORREGIR_Y_RECONCILIAR_OFTALMO_NORTE.md
- MASTER_PROMPT_SAAS_7E1_CORREGIR_Y_RECONCILIAR_OFTALMO_NORTE.md
- scripts/saas/provision_tenant.py
- tests/test_tenant_provisioning.py

ESTADO REAL:
tenant_oftalmo_norte existe.
Control Plane: ERROR / ERROR.
intentos=1.
Auditoría real confirmó estructura, catálogos, admin bootstrap, datos clínicos vacíos y SELECT 1 OK.

CAUSA:
`_sql_literal(admin_email)` usa incorrectamente `validate_database_identifier(value)`.

OBJETIVO:
Corregir SOLO el manejo de literales SQL y reconciliar la DB existente.

1. SEGURIDAD SQL
- Separar IDENTIFIER vs LITERAL.
- Identificadores siguen validación estricta.
- Literales NO usan validate_database_identifier.
- Preferir parámetros.
- Si se mantiene helper, escapar `'` como `''` y envolver en comillas simples.
- No hardcodear @ ni . ni casos específicos.

2. TESTS DE LITERALES
Cubrir:
- admin@oftalmonorte.demo
- nombre.apellido+test@dominio.com
- o'hara@example.com
- texto con espacios
- texto-con-guion
Y comprobar que database_name inválido sigue siendo rechazado.

3. NO REPROVISIONAR
NO usar:
- --provision-clean
- --execute
- --recover-error
NO hacer DROP, CREATE DATABASE, dump o restore.

4. RECONCILE EXISTING
Agregar/reutilizar:
--reconcile-clean-existing

Requiere:
--empresa OFTALMO-NORTE
--confirm tenant_oftalmo_norte

Solo si:
- tenant_database=ERROR
- provisionamiento=ERROR
- DB física existe

No pedir password nuevamente.

5. VERIFICACIÓN PRE-ACTIVACIÓN
Verificar:
- tablas
- secuencias
- columnas
- constraints
- índices
- catálogos accion/modulo/funcion/rol/rol_funcion
- usuario count=1
- admin@oftalmonorte.demo existe
- rol Administrador
- activo
- password_hash presente
- paciente=0
- cita=0
- historial_clinico=0
- consulta_clinica=0
- diagnostico=0
- receta=0
- bitacora=0
- token_recuperacion=0
- schemas prohibidos=0
- SELECT 1 OK

6. TESTS
Cubrir:
- reconcile exige ERROR/ERROR
- rechaza ACTIVA
- rechaza PENDIENTE
- confirm exacto
- DB debe existir
- no dump/restore/CREATE/DROP
- catálogos incompletos bloquean
- admin faltante/inactivo/hash faltante bloquean
- tabla clínica poblada bloquea
- schema prohibido bloquea
- health falla bloquea
- éxito → ACTIVA/COMPLETADO
- intentos permanece 1

Ejecutar:
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

7. AUDITORÍA REAL
Después de corregir, ejecutar verificación READ-ONLY real sobre tenant_oftalmo_norte y reportar:
structure_ok
catalogs_ok
admin_ok
clean_data_ok
schemas_ok
health_ok

Todos deben ser TRUE.

8. RECONCILIACIÓN REAL
Solo si todo es TRUE:

.\.venv\Scripts\python.exe scripts\saas\provision_tenant.py `
  --reconcile-clean-existing `
  --empresa OFTALMO-NORTE `
  --confirm tenant_oftalmo_norte

NO usar --provision-clean.

9. POST-ACTIVACIÓN
Debe quedar:
- tenant_database=ACTIVA
- provisionamiento=COMPLETADO
- version_schema=v1
- intentos=1

Validar:
- TenantResolver("OFTALMO-NORTE")
- TenantEngineRegistry
- SELECT 1
- VISION-CLARA → tenant_vision_clara
- OFTALMO-NORTE → tenant_oftalmo_norte
- engines/pools distintos

10. LOGIN
No pedir ni imprimir password.

Dejar lista prueba manual:
POST /seguridad/tenant/login
empresa_codigo=OFTALMO-NORTE
correo=admin@oftalmonorte.demo

Esperado 200.

Luego:
- admin OFTALMO-NORTE + VISION-CLARA → 401
- usuario exclusivo VISION-CLARA + OFTALMO-NORTE → 401

NO HACER:
- modificar tenant_vision_clara
- crear los otros 5 tenants
- frontend
- móvil
- routers clínicos
- backup
- realtime
- .env
- commit
- push
- merge

REPORTE FINAL:
1. CAUSA
2. CORRECCIÓN SQL LITERAL
3. SEGURIDAD IDENTIFIERS
4. TESTS LITERALS
5. TESTS BLOQUE
6. SUITE COMPLETA
7. AUDITORÍA REAL
8. RECONCILE EJECUTADO
9. CONTROL PLANE
10. TENANT RESOLVER
11. ENGINE REGISTRY
12. AISLAMIENTO A/B
13. LOGIN OFTALMO-NORTE
14. LOGIN CRUZADO A→B
15. LOGIN CRUZADO B→A
16. CONTEOS
17. INTENTOS
18. SECRETOS
19. GIT STATUS

Confirma:
- NO DROP
- NO dump
- NO restore
- NO reprovision-clean
- tenant_oftalmo_norte conservado
- tenant_vision_clara intacto
- no secrets
- no commit/push/merge
