# MASTER_PROMPT_SAAS_7D3_VALIDAR_LOGIN_TENANT.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 7D.3 — VALIDACIÓN E2E DEL LOGIN TENANT

Lee:

- CONTEXTO_SAAS_7D3_VALIDAR_LOGIN_TENANT.md
- implementación actual de /seguridad/tenant/login
- app/core/tenancy/
- app/core/security.py
- app/core/dependencies.py
- tests/test_auth_tenant.py

ESTADO:

VISION-CLARA → tenant_vision_clara → ACTIVA
provisionamiento → COMPLETADO
TenantResolver → OK
TenantEngineRegistry → OK
SELECT 1 → OK

==================================================
OBJETIVO
==================================================

Validar que el login tenant REAL usa tenant_vision_clara.

NO hacer cambios estructurales salvo que se detecte un bug real.

==================================================
PRECHECK
==================================================

READ-ONLY:

- VISION-CLARA sigue ACTIVA
- tenant_vision_clara existe
- check_tenant_connection pasa
- usuario count = 14

NO modificar Control Plane.

==================================================
LOGIN REAL
==================================================

NO inventes ni solicites passwords.

Prepara el endpoint y deja indicado al usuario que realice manualmente:

POST /seguridad/tenant/login

{
  "empresa_codigo": "VISION-CLARA",
  "correo": "<correo conocido>",
  "password": "<password conocido>"
}

El usuario hará la llamada con una cuenta que ya conoce.

No registrar password.
No imprimir token completo.

==================================================
TRAZA SEGURA
==================================================

Verifica mediante código/tests que:

empresa_codigo
→ TenantResolver
→ tenant_database.id
→ TenantEngineRegistry
→ sesión tenant
→ usuario
→ Argon2
→ JWT tenant

Asegura que la búsqueda del usuario NO usa get_db legacy.

No agregar logs permanentes de URL/conexión.

==================================================
JWT
==================================================

Tras login exitoso, validar sin exponer token completo:

- sub presente
- rol_id presente
- tenant_id presente
- empresa_id presente
- empresa_codigo == VISION-CLARA
- token_type == tenant
- exp presente

Confirmar ausencia:

- database_name
- DATABASE_URL
- host
- port
- password
- connection string

==================================================
NEGATIVOS
==================================================

Comprobar:

- empresa inexistente
- password incorrecto → Credenciales inválidas
- database_name extra → 422
- tenant_id extra → 422
- legacy token rechazado por get_tenant_claims
- tenant token aceptado

No hacer intentos repetidos contra una cuenta real.

==================================================
TESTS
==================================================

Ejecutar:

.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
NO HACER
==================================================

NO:
- DROP
- dump
- restore
- crear segundo tenant
- cambiar estados
- migrar routers clínicos
- cutover login legacy
- frontend
- móvil
- backup
- realtime
- .env
- commit
- push
- merge

==================================================
REPORTE
==================================================

Entrega:

1. PRECHECK
2. ENDPOINT
3. FLUJO REAL
4. SESIÓN TENANT
5. LOGIN MANUAL
6. JWT CLAIMS
7. AUSENCIA DE SECRETOS
8. CASOS NEGATIVOS
9. LOGIN LEGACY
10. TESTS AUTH TENANT
11. SUITE COMPLETA
12. GIT STATUS

Si el login manual requiere credenciales, espera que el usuario lo pruebe.
No solicites que comparta contraseña ni token completo.
