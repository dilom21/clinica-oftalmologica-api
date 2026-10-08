# MASTER_PROMPT_SAAS_7G_BACKEND_ADMIN_SAAS.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 7G — BACKEND ADMINISTRADOR SAAS

Lee completos:
- CONTEXTO_SAAS_7G_BACKEND_ADMIN_SAAS.md
- MASTER_PROMPT_SAAS_7G_BACKEND_ADMIN_SAAS.md
- database/saas_control/*
- app/core/tenancy/*
- app/core/security.py
- app/core/dependencies.py
- app/modules/gestion_usuarios_seguridad/
- scripts/saas/provision_tenant.py

Estado:
Los 7 tenants están ACTIVA / COMPLETADO.

OBJETIVO:
Crear backend del Administrador SaaS separado de los administradores clínicos.

1. Crear preferentemente:
app/modules/administracion_saas/

2. Auth SaaS:
POST /saas/auth/login

Request:
{
  "correo": "...",
  "password": "..."
}

JWT:
- sub
- saas_usuario_id
- token_type = "saas_admin"
- exp

No incluir secrets.

3. Bootstrap SaaS Admin:
Crear:
scripts/saas/bootstrap_saas_admin.py

Debe:
- pedir correo
- password con getpass dos veces
- usar hash real del proyecto
- insertar en saas_control.saas_usuario
- evitar duplicados
- no aceptar password CLI
- no imprimir password

NO ejecutar bootstrap real sin instrucción explícita del usuario.

4. Dependencia SaaS Admin:
Crear dependencia separada.
Debe aceptar solo token_type == "saas_admin".
Rechazar:
- tenant JWT
- legacy JWT

5. Endpoints mínimos:
POST /saas/auth/login
GET /saas/empresas
GET /saas/empresas/{empresa_id}
GET /saas/planes
GET /saas/suscripciones
GET /saas/tenants
GET /saas/provisionamientos
GET /saas/bitacora

Opcional:
PATCH /saas/empresas/{empresa_id}/estado
PATCH /saas/suscripciones/{suscripcion_id}/estado

6. Empresas:
Listar 7 empresas con:
- id
- codigo
- slug
- nombre
- estado
- plan
- suscripción
- database_name
- estado tenant
- version_schema
- fecha_provisionamiento

No connection strings.

7. Tenants:
GET /saas/tenants:
- empresa
- database_name
- estado
- version_schema
- fecha_provisionamiento
- ultima_verificacion

No abrir las 7 DBs para el listado.

8. Provisionamientos:
GET /saas/provisionamientos:
- empresa
- estado
- paso_actual
- intentos
- fecha_inicio
- fecha_fin
- mensaje_error saneado

9. Bitácora:
Usar saas_control.saas_bitacora.
Registrar:
- LOGIN_SAAS
- CAMBIAR_ESTADO_EMPRESA
- CAMBIAR_ESTADO_SUSCRIPCION

No guardar tokens/password/URLs.

10. Suspensión:
Si implementas PATCH de estado:
- suspender empresa debe provocar rechazo del TenantResolver
- reactivar debe permitir resolver de nuevo
- no apagar/drop DB física

11. Tests:
Crear:
tests/test_saas_admin_backend.py

Cubrir:
- login válido
- login inválido
- SaaS usuario inactivo
- JWT saas_admin
- tenant JWT rechazado
- legacy JWT rechazado
- 7 empresas
- 7 tenants
- planes
- suscripciones
- provisionamientos
- bitácora
- no secrets
- suspensión empresa
- reactivación
- no acceso clínico directo

Ejecutar:
.\.venv\Scripts\python.exe -m pytest tests/test_saas_admin_backend.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

NO HACER:
- frontend SaaS
- backup
- restore
- realtime
- migrar routers clínicos
- modificar datos clínicos
- .env
- commit
- push
- merge

REPORTE FINAL:
1. ESTADO
2. ARCHIVOS CREADOS
3. ARCHIVOS MODIFICADOS
4. BOOTSTRAP SAAS ADMIN
5. LOGIN SAAS
6. JWT CLAIMS
7. DEPENDENCIA SAAS ADMIN
8. ENDPOINTS
9. EMPRESAS
10. PLANES
11. SUSCRIPCIONES
12. TENANTS
13. PROVISIONAMIENTOS
14. BITÁCORA
15. SUSPENSIÓN/REACTIVACIÓN
16. SEGURIDAD
17. TESTS BLOQUE
18. SUITE COMPLETA
19. GIT STATUS

Confirma:
- tenant JWT rechazado
- legacy JWT rechazado
- sin secrets
- no frontend
- no backup/restore
- no commit/push/merge
