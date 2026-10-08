# MASTER_PROMPT_SAAS_7I_E2E_MULTITENANT_COMPLETO.md

Trabaja en este orden y respeta los repositorios:

BACKEND:
C:\SI2_Proyecto\clinica-oftalmologica-api

FRONTEND:
C:\SI2_Proyecto\clinica-oftalmologica-web

Estamos en:

PASO 7I — E2E SAAS MULTITENANT COMPLETO

Lee:
- CONTEXTO_SAAS_7I_E2E_MULTITENANT_COMPLETO.md
- MASTER_PROMPT_SAAS_7I_E2E_MULTITENANT_COMPLETO.md

IMPORTANTE:
El bootstrap SaaS Admin requiere password interactivo.
NO pidas ni imprimas la contraseña.
NO ejecutes esa parte en un entorno sin input interactivo.
El usuario la ejecutará manualmente.

==================================================
FASE 1 — PREFLIGHT READ-ONLY
==================================================

BACKEND.

Confirmar:

- 7 empresas
- 7 tenant_database ACTIVA
- 7 provisionamientos COMPLETADO
- 7 suscripciones ACTIVA
- 3 planes
- version_schema v1

Confirmar que saas_usuario está vacío o reportar usuarios existentes.

NO modificar nada.

==================================================
FASE 2 — BOOTSTRAP SAAS ADMIN
==================================================

No ejecutes tú el password interactivo.

Entrega al usuario el comando EXACTO para ejecutar:

scripts/saas/bootstrap_saas_admin.py

desde el backend.

Usar un correo demo recomendado:

admin@saas.demo

pero permitir que el usuario elija otro.

Después del bootstrap, verificar READ-ONLY:

- exactamente el usuario esperado existe
- activo
- password_hash presente
- NO imprimir hash completo

==================================================
FASE 3 — BACKEND LOCAL
==================================================

Guiar al usuario para levantar:

.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

Verificar /docs.

==================================================
FASE 4 — FRONTEND LOCAL
==================================================

En:

C:\SI2_Proyecto\clinica-oftalmologica-web

Levantar comando real del proyecto.

Verificar entorno development local sin modificar producción.

==================================================
FASE 5 — LOGIN SAAS
==================================================

Usuario realiza login manual en:

/saas/login

Esperado:
200 + redirect /saas.

NO pedir token.
NO pedir password.

==================================================
FASE 6 — DASHBOARD
==================================================

Validar UI real:

- 7 empresas
- 7 tenants activos
- 7 suscripciones activas
- 3 planes
- 0 provisioning errors

Usar valores reales.

==================================================
FASE 7 — VISTAS
==================================================

Validar:

/saas/empresas
/saas/planes
/saas/suscripciones
/saas/tenants
/saas/provisionamientos
/saas/bitacora

Sin secrets.

==================================================
FASE 8 — SUSPENSIÓN REAL
==================================================

Usar exclusivamente:

MEDICO-OCULAR

Antes:
ACTIVA.

Desde frontend SaaS:
Suspender empresa.

Verificar:
empresa = SUSPENDIDA.

NO cambiar suscripción.
NO apagar DB.

Intentar login tenant con:

empresa_codigo = MEDICO-OCULAR
correo = admin@medicoocular.demo

El usuario introduce su password.

Esperado:
rechazo controlado.

NO pedir que comparta password/token.

==================================================
FASE 9 — DB FÍSICA
==================================================

READ-ONLY:

confirmar:

tenant_medico_ocular sigue existiendo.

SELECT 1 puede seguir funcionar administrativamente.

Esto demuestra:
suspensión lógica ≠ borrar/apagar DB.

==================================================
FASE 10 — REACTIVACIÓN
==================================================

Desde frontend SaaS:
reactivar MEDICO-OCULAR.

Verificar:
empresa = ACTIVA.

Repetir login tenant:

MEDICO-OCULAR + admin@medicoocular.demo

Esperado:
200.

==================================================
FASE 11 — AISLAMIENTO
==================================================

Con la misma cuenta MEDICO-OCULAR probar una sola vez:

empresa_codigo = VISION-CLARA

Esperado:
401 Credenciales inválidas.

==================================================
FASE 12 — BITÁCORA
==================================================

Verificar:

LOGIN_SAAS
CAMBIAR_ESTADO_EMPRESA

No secrets.

==================================================
FASE 13 — TOKEN SEPARATION
==================================================

Verificar por tests/UI:

saas_access_token separado de access_token.

Logout SaaS:
NO elimina token clínico.

401 SaaS:
NO elimina token clínico.

No imprimir tokens.

==================================================
FASE 14 — TESTS BACKEND
==================================================

Desde backend:

.\.venv\Scripts\python.exe -m pytest tests/test_saas_admin_backend.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
FASE 15 — TESTS FRONTEND
==================================================

Desde frontend:

npm test -- --watch=false
npm run build

Typecheck si existe.

git diff --check

==================================================
NO HACER
==================================================

NO:
- modificar datos clínicos
- crear tenants
- DROP
- dump
- restore
- backup/restore todavía
- realtime
- migrar routers clínicos
- cambiar .env
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. PREFLIGHT 7 TENANTS
2. SAAS ADMIN BOOTSTRAP
3. LOGIN SAAS
4. DASHBOARD
5. EMPRESAS
6. PLANES
7. SUSCRIPCIONES
8. TENANTS
9. PROVISIONAMIENTOS
10. BITÁCORA
11. MEDICO-OCULAR PRE-SUSPENSIÓN
12. SUSPENSIÓN
13. LOGIN DURANTE SUSPENSIÓN
14. DB FÍSICA DURANTE SUSPENSIÓN
15. REACTIVACIÓN
16. LOGIN POST-REACTIVACIÓN
17. AISLAMIENTO CROSS-TENANT
18. TOKEN SEPARATION
19. TESTS BACKEND
20. TESTS FRONTEND
21. BUILD
22. GIT STATUS

Confirma:

- PASO 7 SaaS completo
- 7 tenants activos al final
- sin secrets
- sin datos clínicos modificados
- no backup/restore
- no commit/push/merge
