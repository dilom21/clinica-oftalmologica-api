# CONTEXTO_CU26_BACK — Gestión de categorías, productos y lotes

**Proyecto:** Clínica Oftalmológica — Sistemas de Información II  
**Fecha de revisión:** 2026-11-02  
**Repositorio:** `https://github.com/dilom21/clinica-oftalmologica-api`  
**Rama de trabajo:** `dev-josias`  
**Carpeta local:** `C:\SI2_Proyecto\clinica-oftalmologica-api`  
**Ubicación de este archivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api\CONTEXTO_CU26_BACK.md`

> Documento de contexto para OpenCode. El código del checkout local es la fuente de verdad; verificar que la rama y los archivos coincidan antes de editar. No sustituye una autorización para migrar bases o cambiar datos reales.

## 1. Objetivo de esta etapa

Cerrar **solo el backend de CU26: gestionar categorías, productos y lotes** antes de implementar el frontend. No rehacer las funciones ya existentes. Endurecer el acceso tenant, ampliar la cobertura de pruebas y comprobar que el contrato es estable. Registrar la cuestión pendiente de permisos granulares y resolverla de manera controlada, sin alterar las siete empresas por cuenta propia.

## 2. Lo que ya se verificó

- Backend Python 3.12/FastAPI/SQLAlchemy/PostgreSQL y módulo `gestion_inventario_proveedores`.
- CU26 tiene **15 endpoints** ya registrados en `app/main.py` a través del router del catálogo.
- En el entorno local se ejecutaron 17 pruebas de CU26+CU27: **17 PASSED, 0 FAILED**. CU26 aporta tres pruebas en SQLite con overrides; esas pruebas NO demuestran por sí solas JWT tenant ni aislamiento real de CU26.
- Las siete empresas del plano de control estaban activas y registradas con versión `v4-sprint3-cu32-cu33`.
- Se verificaron 7/7 tablas esperadas y 9/9 relaciones de claves foráneas por tenant. Las siete firmas de definición de columnas coincidieron: `ff94d549755b` en la consulta realizada. Esto NO comprueba todos los índices/constraints ni valida 7 logins reales.
- Empresas y bases: `VISION-CLARA` / `tenant_vision_clara`; `OFTALMO-NORTE` / `tenant_oftalmo_norte`; `VISUAL-ORIENTAL` / `tenant_visual_oriental`; `INSTITUTO-VISION` / `tenant_instituto_vision`; `OFTALMOCARE` / `tenant_oftalmocare`; `VISTA-SUR` / `tenant_vista_sur`; `MEDICO-OCULAR` / `tenant_medico_ocular`.
- El archivo local no versionado `scripts/saas/migrate_sprint3_tenants.py` usa esquema objetivo **v3** y rechaza v4. **No ejecutarlo con `--execute`, no bajar `version_schema` ni ejecutar SQL de migración sobre tenants v4.**
- El repositorio local contenía archivos **untracked** relacionados con migraciones y backups. No borrar, añadir ni sobrescribir esos archivos a ciegas.

## 3. Archivos reales que se deben inspeccionar

### Código principal de CU26 (candidatos para cambios mínimos)

| Archivo | Responsabilidad |
|---|---|
| `app/modules/gestion_inventario_proveedores/api/router.py` | 15 endpoints y dependencias de seguridad |
| `app/modules/gestion_inventario_proveedores/schemas/catalogo.py` | Contratos Pydantic y validaciones |
| `app/modules/gestion_inventario_proveedores/services/catalogo.py` | Reglas de negocio |
| `app/modules/gestion_inventario_proveedores/models/catalogo.py` | ORM de `categoria`, `producto`, `lote` |
| `tests/test_cu26_backend.py` | Test de catálogo con DB SQLite en memoria |

### Seguridad e integración — inspeccionar y cambiar solo si es estrictamente necesario

- `app/core/dependencies.py` — `get_tenant_claims()`, `obtener_usuario_actual()`, `obtener_administrador_actual()`, `requerir_permiso()`.
- `app/database/session.py` — `get_db()` selecciona DB del tenant por JWT; cuando recibe token legacy, todavía puede usar `SessionLocal` central.
- `app/core/tenancy/dependencies.py`, `resolver.py`, `engine_registry.py` — resolución y revalidación de tenant.
- `app/modules/gestion_usuarios_seguridad/models/models.py` — tablas de roles/funciones/acciones.
- `app/modules/gestion_usuarios_seguridad/repositories/repository.py` y `services/menu_service.py` — permisos por rol y menú dinámico.
- `app/modules/gestion_usuarios_seguridad/api/router.py` — autenticación y consulta del menú.
- `app/main.py` — verificar registro del router.
- `tests/test_auth_tenant.py`, `tests/test_tenant_connection_router.py`, `tests/test_tenant_provisioning.py` y `tests/test_cu27_postgres_integracion.py` — patrones de integración y pruebas fail-closed.

**No son implementaciones activas de este CU:** `app/modules/gestion_inventario_proveedores/casos_uso/cu26_registrar_compras_proveedores.py` (archivo vacío en la rama revisada); no rellenarlo para sustituir `services/catalogo.py`.

## 4. Contrato HTTP vigente: 15 operaciones

Prefijo: `/inventario-proveedores`. El código existente es la fuente definitiva.

| Entidad | GET lista | POST crear | GET uno | PUT actualizar | PATCH estado |
|---|---|---|---|---|---|
| Categorías | `/categorias` | `/categorias` | `/categorias/{categoria_id}` | `/categorias/{categoria_id}` | `/categorias/{categoria_id}/estado` |
| Productos | `/productos` | `/productos` | `/productos/{producto_id}` | `/productos/{producto_id}` | `/productos/{producto_id}/estado` |
| Lotes | `/lotes` | `/lotes` | `/lotes/{lote_id}` | `/lotes/{lote_id}` | `/lotes/{lote_id}/estado` |

- `GET /categorias`: `incluir_inactivos=false` por defecto.
- `GET /productos`: `categoria_id` opcional positivo; `incluir_inactivos=false` por defecto.
- `GET /lotes`: `producto_id` opcional positivo; `incluir_inactivos=false` por defecto.
- `PATCH .../estado`: body `{ "estado": true | false }`.
- No existe `DELETE` físico.
- `POST` retorna HTTP 201; listas retornan arreglos; obtener/actualizar/estado retornan entidad.

### Pydantic vigente

**Categoría:** crear/actualizar `{ nombre: str [1..100, trim], descripcion?: str|null }`; respuesta también `id`, `estado`, `fecha_creacion`.

**Producto:** crear/actualizar `{ categoria_id: int>0, codigo: str [1..50, trim], nombre: str [1..160, trim], descripcion?: str|null, unidad_medida?: str [1..30, default 'UNIDAD'], stock_minimo?: int [0..2147483647, default 0] }`; respuesta suma `id`, `estado`, `fecha_creacion`, `fecha_actualizacion`.

**Lote:** crear `{ producto_id: int>0, numero_lote: str [1..80, trim], fecha_fabricacion?: YYYY-MM-DD|null, fecha_vencimiento?: YYYY-MM-DD|null, observaciones?: str|null }`; **actualizar excluye `producto_id`**. Respuesta añade `id`, `estado`, `fecha_creacion`.

## 5. Reglas reales

1. Categoría desactivable solo si no tiene productos activos.
2. Producto requiere categoría activa. No se puede desactivar con lotes activos.
3. `unidad_medida` de producto no cambia cuando el producto ya tiene lotes.
4. Lote se crea/activa solo con producto activo. No se puede desactivar con `stock_actual>0` o `stock_reservado>0`.
5. Vencimiento no anterior a fabricación. El número de lote es único por producto y el código de producto es único según los constraints existentes.
6. Categoría duplicada por nombre normalizado se maneja como conflicto.
7. Respuestas relevantes: 401/403 autenticación/rol, 404 recurso, 409 conflicto de negocio/integridad, 422 entrada inválida; 503 tenant no disponible.
8. No mezclar administración de catálogo con generación de movimientos: CU27 es responsable de stock.

## 6. BRECHA CONFIRMADA: seguridad tenant de CU26

**Hallazgo:** `api/router.py` de CU26 usa `Depends(obtener_administrador_actual)` por ruta, pero NO `Depends(get_tenant_claims)` explícito. `get_db()` admite tokens legacy, que pueden abrir sesión central. CU27 sí exige `get_tenant_claims` en el router.

**Trabajo esperado:** cerrar esta ruta heredada **para los 15 endpoints de CU26**, sin romper el login legacy ni el resto del sistema. Opción a evaluar: aplicar una dependencia `get_tenant_claims` a nivel router conservando la validación de rol administrador. Comprobar que usa sesión asociada al tenant y rechazo de tokens legacy, SaaS, caducados, manipulados, suspendidos y de empresa incorrecta. Los tests CU26 actuales sobreescriben dependencias y deberán ajustarse para reflejar el requisito nuevo; agregar pruebas de seguridad **sin depender de tenants reales**.

**Permisos RBAC de lectura/escritura:** `requerir_permiso()` soporta `LECTURA`, `ESCRITURA`, `AMBAS`, pero CU26 todavía usa control por rol administrador. Primero inspeccionar nombres de `funcion` y asociaciones `rol_funcion` en las siete bases, con consultas de solo lectura; no inventar nombres ni hacer `INSERT/UPDATE` en tenants. Si falta provisionar permisos, entregar el diseño exacto y tests para futura autorización por separado. No activar una dependencia granular que bloquee indiscriminadamente usuarios legítimos en producción por no haber preparado datos.

## 7. Qué NO debe hacer OpenCode

- No crear otro módulo CU26, otro router con las mismas rutas, CRUD duplicado ni tablas nuevas.
- No modificar `get_db()` globalmente para eliminar compatibilidad legacy que necesitan otros CU.
- No tocar `database/`, migraciones v3/v4, `.env`, secretos, backups, `administracion_saas` ni el frontend.
- No conectar tests de creación/edición/desactivación a las siete bases reales.
- No resetear, limpiar, fusionar, hacer commit ni push sin permiso expreso.

## 8. Criterios para declarar listo CU26 backend

- Las 15 rutas y validaciones existentes siguen funcionando.
- Todas requieren **JWT tenant** y rol autorizado, con pruebas que demuestren rechazo fail-closed de legacy/SaaS/no autorizado y acceso a la DB correcta.
- Operaciones de categoría, producto y lote siguen pasando, incluyendo validaciones `404/409/422` y casos límite.
- Hay pruebas de integración local PostgreSQL para aislamiento de **dos tenants** si el entorno lo permite; sin escritura a las siete clínicas.
- Se documentó el estado real de permisos/menú en las siete empresas y si queda un gate de RBAC pendiente.
- Los tests y la importación de `app.main` no presentan regresiones; reporte con comandos, resultados, archivos tocados y estado Git.

**Nota:** La confirmación de las tablas y FK en siete empresas es evidencia estructural, no prueba E2E con autenticación de cada clínica. Eso se realizará con un plan aparte y cuentas autorizadas.
