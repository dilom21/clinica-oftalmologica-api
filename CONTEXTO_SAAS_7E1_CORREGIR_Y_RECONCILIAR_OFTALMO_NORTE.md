# CONTEXTO_SAAS_7E1_CORREGIR_Y_RECONCILIAR_OFTALMO_NORTE.md

Repositorio:
`C:\SI2_Proyecto\clinica-oftalmologica-api`

Estado actual:
- VISION-CLARA → tenant_vision_clara → ACTIVA / COMPLETADO.
- OFTALMO-NORTE → tenant_oftalmo_norte → ERROR / ERROR.
- tenant_oftalmo_norte existe físicamente y está estructuralmente correcto.
- 27 tablas, 27 secuencias, 182 columnas, 67 índices, constraints correctas.
- Catálogos: accion=3, modulo=6, funcion=19, rol=4, rol_funcion=39.
- Bootstrap: usuario=1, correo admin@oftalmonorte.demo, rol Administrador, activo=true, password_hash presente.
- Tablas clínicas/transaccionales: 0 filas.
- Schemas prohibidos: 0.
- SELECT 1: OK.

Causa exacta:
`scripts/saas/provision_tenant.py` usa `_sql_literal(admin_email)`, pero `_sql_literal()` llama por error a `validate_database_identifier(value)`. El correo se intenta validar como nombre de DB y falla.

Objetivo:
1. Corregir exclusivamente el tratamiento seguro de literales SQL.
2. Mantener intacta la validación estricta de identificadores PostgreSQL.
3. Reconciliar el tenant existente SIN DROP, dump, restore ni reprovision-clean.
4. Si la verificación completa pasa, marcar tenant_database=ACTIVA y provisionamiento=COMPLETADO, version_schema=v1.
5. Validar TenantResolver, TenantEngineRegistry y aislamiento A/B.
6. Dejar lista la prueba manual de login OFTALMO-NORTE.

Reglas de seguridad:
- IDENTIFICADORES SQL: database/schema/table/column dinámicos siguen validación estricta.
- LITERALES SQL: correo/textos/estados NO usan validate_database_identifier.
- Preferir queries parametrizadas.
- Si se mantiene helper `_sql_literal`, debe escapar `'` como `''`, envolver en comillas simples y no hardcodear casos concretos.
- No imprimir password, hash completo, DATABASE_URL ni tokens.

Tests mínimos:
- admin@oftalmonorte.demo
- nombre.apellido+test@dominio.com
- o'hara@example.com
- texto con espacios
- texto-con-guion
- identifier inválido sigue siendo rechazado.

Reconcile:
Agregar/reutilizar `--reconcile-clean-existing`, requiriendo:
- `--empresa OFTALMO-NORTE`
- `--confirm tenant_oftalmo_norte`

Solo permitido si:
- tenant_database=ERROR
- provisionamiento=ERROR
- DB física existe

No pedir password otra vez.

Verificar antes de activar:
- estructura schema v1
- catálogos correctos
- usuario=1
- admin bootstrap correcto
- tablas clínicas en 0
- schemas prohibidos=0
- SELECT 1 OK

Activación:
- tenant_database.estado=ACTIVA
- tenant_database.version_schema=v1
- fechas de provisionamiento/verificación
- provisionamiento.estado=COMPLETADO
- paso_actual=COMPLETADO
- fecha_fin=now()
- mensaje_error=NULL
- intentos se conserva en 1

No hacer:
DROP, dump, restore, --provision-clean, crear otros tenants, modificar tenant_vision_clara, frontend, móvil, routers clínicos, backup, realtime, .env, commit/push/merge.
