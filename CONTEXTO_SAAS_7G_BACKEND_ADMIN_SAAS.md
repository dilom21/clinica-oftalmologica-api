# CONTEXTO_SAAS_7G_BACKEND_ADMIN_SAAS.md

Repositorio objetivo:
C:\SI2_Proyecto\clinica-oftalmologica-api

Estado previo:
- 7 tenants físicos provisionados.
- Todos ACTIVA / COMPLETADO.
- Aislamiento real probado entre VISION-CLARA y OFTALMO-NORTE.
- Login tenant real funcionando.

Objetivo:
Implementar el backend del Administrador SaaS separado de los administradores clínicos de cada tenant.

Control Plane:
saas_control

Debe gestionar:
- empresas
- planes
- suscripciones
- tenants
- provisionamientos
- bitácora SaaS

Autenticación:
- POST /saas/auth/login
- usar saas_control.saas_usuario
- JWT separado con token_type = "saas_admin"
- no aceptar tenant JWT ni legacy JWT

Bootstrap SaaS admin:
Crear script local seguro, preferentemente:
scripts/saas/bootstrap_saas_admin.py

Reglas:
- correo
- password con getpass dos veces
- hash real del proyecto
- no password CLI
- no password logs
- evitar duplicados

Endpoints mínimos:
- POST /saas/auth/login
- GET /saas/empresas
- GET /saas/empresas/{empresa_id}
- GET /saas/planes
- GET /saas/suscripciones
- GET /saas/tenants
- GET /saas/provisionamientos
- GET /saas/bitacora

Opcionales en este bloque:
- PATCH /saas/empresas/{empresa_id}/estado
- PATCH /saas/suscripciones/{suscripcion_id}/estado

Seguridad:
Todas las rutas /saas/* excepto login requieren token_type == "saas_admin".
Un rol clínico Administrador NO equivale a SaaS Admin.

Empresas:
GET /saas/empresas debe incluir al menos:
- id
- codigo
- slug
- nombre
- estado
- plan
- estado suscripción
- database_name
- estado tenant
- version_schema
- fecha_provisionamiento

Tenants:
No abrir las 7 DBs en cada listado.
Usar Control Plane para metadata.

Provisionamientos:
Mostrar:
- empresa
- estado
- paso_actual
- intentos
- fecha_inicio
- fecha_fin
- mensaje_error saneado

Bitácora:
Usar saas_control.saas_bitacora.
Acciones sugeridas:
- LOGIN_SAAS
- CAMBIAR_ESTADO_EMPRESA
- CAMBIAR_ESTADO_SUSCRIPCION

No guardar:
- passwords
- tokens
- URLs de conexión
- secretos

Suspensión:
Si se implementa cambio de estado, suspender empresa debe hacer que TenantResolver la rechace por revalidación del Control Plane.
NO apagar ni borrar la DB física.

Tests:
Crear tests/test_saas_admin_backend.py y cubrir:
- login válido
- login inválido
- usuario SaaS inactivo
- JWT saas_admin
- tenant JWT rechazado
- legacy JWT rechazado
- listado 7 empresas
- listado 7 tenants
- planes
- suscripciones
- provisionamientos
- bitácora
- no secrets
- suspensión/reactivación
- no tocar datos clínicos

No hacer:
- frontend SaaS
- backup/restore
- realtime
- migrar routers clínicos
- modificar datos clínicos
- .env
- commit/push/merge
