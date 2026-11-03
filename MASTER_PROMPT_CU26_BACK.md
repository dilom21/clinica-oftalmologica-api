# MASTER_PROMPT_CU26_BACK — Cierre seguro del backend CU26

## Rol y límites

Actúa como ingeniero senior Python/FastAPI/SQLAlchemy/PostgreSQL para **CU26: gestionar categorías, productos y lotes**. Tu workspace es `C:\SI2_Proyecto\clinica-oftalmologica-api`, rama `dev-josias`. Mantén intactos los otros CU, las siete bases SaaS y los cambios locales no versionados.

### Instrucción obligatoria inicial

1. Lee **completo** `CONTEXTO_CU26_BACK.md` desde la raíz de este repositorio.
2. Antes de escribir, ejecuta `git branch --show-current`, `git status --short`, `git diff` y comprueba la rama `dev-josias`. Si encuentras modificaciones ajenas, no las sobrescribas.
3. Inspecciona el backend vigente, especialmente:
   - `app/modules/gestion_inventario_proveedores/api/router.py`
   - `app/modules/gestion_inventario_proveedores/schemas/catalogo.py`
   - `app/modules/gestion_inventario_proveedores/services/catalogo.py`
   - `app/modules/gestion_inventario_proveedores/models/catalogo.py`
   - `app/core/dependencies.py`, `app/database/session.py`
   - `app/core/tenancy/dependencies.py`, `resolver.py`, `engine_registry.py`
   - `app/modules/gestion_usuarios_seguridad/repositories/repository.py`, `services/menu_service.py`
   - `app/main.py`
   - `tests/test_cu26_backend.py`, `tests/test_auth_tenant.py`, `tests/test_cu27_postgres_integracion.py`.
4. Contrasta lo leído con `CONTEXTO_CU26_BACK.md` y informa cualquier divergencia. No supongas que archivos antiguos vacíos implementan compras o inventario.

## Objetivo técnico exacto

Completar el **backend actual** de CU26 sin reescribirlo: seguridad obligatoria de JWT tenant, cobertura robusta de endpoints y aislamiento, regresión de reglas de negocio, e inventario de requisitos RBAC que aún falten. La API vigente tiene 15 endpoints. Conservar paths, modelos y códigos HTTP salvo bug demostrado.

## Fase A — Diagnóstico, sin efectos

- Presenta la matriz de 15 rutas con método, esquema request/response y dependencias reales.
- Comprueba que `get_db` enruta la sesión al tenant del JWT y que la ruta CU26 actual permite todavía token legacy.
- Examina roles y nombres de funciones/acciones: `requerir_permiso`, `funcion`, `rol_funcion`, `accion`. No inventes identificadores ni supongas que los siete tenants tienen seeds RBAC de CU26.
- Revisa la pertenencia de los cambios no versionados. No borres las carpetas ZIP, backups ni scripts de migraciones locales.

## Fase B — Seguridad CU26, modificación acotada

- **Corregir** `app/modules/gestion_inventario_proveedores/api/router.py` para que sus 15 endpoints exijan `get_tenant_claims` (o mecanismo equivalente revisado) además de administrador autorizado; la sesión debe resolverse siempre al tenant. Preferir dependencia de router que evite repetir lógica por método.
- Rechazar tokens legacy, SaaS, mal formados, caducados, con claims inconsistentes, empresa suspendida, rol/usuario invalidado, y acceso de no administradores, preservando `401/403` correctos.
- No modificar `get_db()` ni login legacy globalmente si el guard específico de CU26 lo resuelve. Si detectas defecto global imprescindible, justifica primero la modificación y su impacto en otros CU.
- Evita introducir `empresa_id` controlado por el cliente en requests de catálogo.
- Mantener rutas y status codes; no crear dependencia de permiso granular hasta verificar sus registros reales. Si está listo el RBAC, proponer aplicación `LECTURA` a GET y `ESCRITURA` a POST/PUT/PATCH con nombre exacto; si falta en empresas, reportarlo como bloqueo para la fase de permisos, sin efectuar cambios de datos.

## Fase C — Pruebas

- Actualiza `tests/test_cu26_backend.py` para que, en pruebas autorizadas, se haga override explícito de la nueva dependencia `get_tenant_claims` sin disminuir seguridad en pruebas negativas.
- Añade tests de `401` por no tenant/legacy/SaaS, `403` por rol insuficiente, estado de usuario y controles de tenant.
- Cubrir creación, lectura, edición, cambio de estado, filtros y reglas de integridad: categoría duplicada, producto con categoría inactiva, lote con fechas inversas, producto con lotes activos, desactivación de lote con existencias y stock reservado, unidad de medida bloqueada si existen lotes.
- Introduce, si es factible, pruebas de integración CU26 sobre PostgreSQL **local de prueba** y dos esquemas/bases aislados siguiendo el patrón existente de CU27 (sin credenciales hardcodeadas ni uso de Supabase).
- Nunca desactivar validaciones para «hacer pasar» pytest; conserva mocks claros y pruebas negativas con dependencias no sobreescritas.

## Fase D — Pruebas del proyecto y verificación multitenant

1. Ejecuta `python -m pytest -v tests/test_cu26_backend.py`.
2. Ejecuta `python -m pytest -v tests/test_cu27_movimientos.py tests/test_cu27_postgres_integracion.py` si la URL local de pruebas existe; si falta, registra SKIPPED, no lo presentes como PASS.
3. Ejecuta pruebas de autenticación y tenant pertinentes; opcional suite completa `python -m pytest -q` si es viable.
4. Para las siete bases reales solo es admisible un inventario **READ-ONLY** de esquemas/constraints/RBAC con credenciales autorizadas. No ejecutar POST/PATCH/PUT ni migraciones sobre clínicas reales.
5. Entrega diferencias entre el estado inicial y final, evidencia de endpoints, regresiones y brechas restantes.

## Archivos permitidos / restringidos

**Editar preferentemente:** `app/modules/gestion_inventario_proveedores/api/router.py`, `tests/test_cu26_backend.py` y nuevos tests `tests/test_cu26_*` que resulten necesarios. `schemas/catalogo.py` o `services/catalogo.py` únicamente ante error reproducible.

**Solo lectura salvo consentimiento excepcional:** `app/core/dependencies.py`, `app/database/session.py`, `app/modules/gestion_usuarios_seguridad/**`, `app/core/tenancy/**`, otros módulos clínicos, `database/**` y cualquier script SaaS.

**Prohibido:** modificar la BD de los siete tenants, `version_schema`, usar el migrador v3 en DB v4, alterar `.env`, imprimir passwords/tokens, Git destructivo, `git add/commit/push`, merge o desplegar.

## Criterios de entrega

- CU26 15/15 endpoints preservados; protección explícita tenant en los 15; administrador autorizado.
- Tests unitarios/integración cubren seguridad negativa y operaciones; sin regresión de CU27.
- Aclaración del estado RBAC real; si no está listo, proponer una migración **idempotente revisable** pero NO ejecutarla.
- Entrega reporte final: lista de archivos tocados, diff funcional, pruebas y salidas, limitaciones, `git status`, sin declarar cobertura E2E siete empresas si no se ha realizado.

**Modo de trabajo:** avanzar en cambios mínimos y tests; no solicitar nuevas features fuera de CU26 ni iniciar frontend.
