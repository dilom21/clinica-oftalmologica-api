# CONTEXTO_CU27_BACK — Movimientos de inventario y consulta por lote

**Proyecto:** Clínica Oftalmológica — Sistemas de Información II  
**Fecha de revisión:** 2026-11-02  
**Repositorio:** `https://github.com/dilom21/clinica-oftalmologica-api`  
**Rama:** `dev-josias`  
**Carpeta local:** `C:\SI2_Proyecto\clinica-oftalmologica-api`  
**Ubicación de este archivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api\CONTEXTO_CU27_BACK.md`

> Este documento corresponde al CU27 implementado como **gestión de movimientos de inventario** en el Sprint 3. Los nombres antiguos de archivos de casos de uso no definen por sí solos el alcance. Revisar siempre historias de usuario oficiales si la asignación CU27 de «consultar inventario y alertas» fuera diferente.

## 1. Situación verificada

- CU27 tiene **tres endpoints reales** en el router de movimientos. Implementa alta de movimientos manuales, consulta del historial y existencias de **un lote**.
- `get_tenant_claims` y `obtener_administrador_actual` son dependencias explícitas del router; `get_db()` resuelve la sesión tenant.
- Las pruebas ejecutadas por el usuario para CU26+CU27 fueron **17 PASS, 0 FAIL**. Siete pruebas PostgreSQL integraron transacciones, dos entornos aislados, JWT incorrecto/legacy, usuario/rol revocado y suspensión de empresa. Son pruebas locales; NO prueban login real de las siete clínicas.
- Las siete DB tenant registradas como activas son: `tenant_vision_clara`, `tenant_oftalmo_norte`, `tenant_visual_oriental`, `tenant_instituto_vision`, `tenant_oftalmocare`, `tenant_vista_sur`, `tenant_medico_ocular`.
- Cada una mostró las siete tablas del conjunto Sprint 3 y las nueve FK revisadas; las columnas coincidieron. `version_schema='v4-sprint3-cu32-cu33'` en todas. No rehacer migraciones v3.
- El frontend aún no forma parte de este trabajo. Se desarrollará después de cerrar backend CU26+CU27.

## 2. Archivos backend que se deben leer

| Archivo | Función |
|---|---|
| `app/modules/gestion_inventario_proveedores/api/movimientos.py` | Router CU27 y sus 3 rutas |
| `app/modules/gestion_inventario_proveedores/schemas/movimientos.py` | Entradas y respuestas, tipos permitidos |
| `app/modules/gestion_inventario_proveedores/services/movimientos.py` | Transacciones, saldos, bloqueos, historial |
| `app/modules/gestion_inventario_proveedores/models/movimientos.py` | Tablas `inventario`, `movimiento_inventario` |
| `app/modules/gestion_inventario_proveedores/models/catalogo.py` | Categoría/producto/lote que condicionan stock |
| `app/modules/gestion_inventario_proveedores/api/router.py` | GET lotes/productos de CU26 para futura UI |
| `app/core/dependencies.py`, `app/database/session.py` | Permisos, token y sesiones tenant |
| `app/core/tenancy/resolver.py`, `engine_registry.py` | Aislamiento de conexiones |
| `tests/test_cu27_movimientos.py` | Pruebas aisladas SQLite |
| `tests/test_cu27_postgres_integracion.py` | PostgreSQL local, JWT y aislamiento |
| `tests/test_cu26_backend.py` | Compatibilidad de catálogo |
| `app/main.py` | Routers incluidos |

El archivo `app/modules/gestion_inventario_proveedores/casos_uso/cu27_consultar_inventario_alertas.py` estaba vacío en la rama revisada; no duplicar allí una segunda implementación sin necesidad.

## 3. API CU27 publicada actualmente

Prefijo `/inventario-proveedores`:

1. `POST /movimientos` — `201 MovimientoRespuesta`; solo movimientos **manuales**.
2. `GET /movimientos?lote_id={id}&skip=0&limit=50` — `200 list[MovimientoRespuesta]`; `lote_id` opcional y positivo; `skip>=0`; `1<=limit<=100`. Respuesta array, sin `total`.
3. `GET /existencias/{lote_id}` — `200 ExistenciaRespuesta` del lote; 404 cuando el lote no existe.

**No existen en CU27 actual:** `GET /inventario-proveedores/existencias` (colección de todos los lotes), `/alertas`, `/stock-bajo`, `/vencimientos`, `/inventario` global, editar/borrar movimientos o registrar compras manuales como `ENTRADA_COMPRA`.

### Schemas

**Entrada `MovimientoManualCrear`:**
```json
{
  "lote_id": 1,
  "tipo": "AJUSTE_ENTRADA",
  "cantidad": 3,
  "observaciones": "Corrección de inventario documentada"
}
```

- Tipos manuales: `STOCK_INICIAL`, `SALIDA_USO`, `AJUSTE_ENTRADA`, `AJUSTE_SALIDA`, `SALIDA_VENCIMIENTO`.
- `lote_id`: entero positivo; `cantidad`: entero de 1 a 2147483647; `observaciones`: opcional (máx. 1000 caracteres, con espacios normalizados); obligatorio por regla de servicio en ajustes y salida por vencimiento.
- `extra="forbid"`: **prohibido** enviar `usuario_id`, `stock_anterior`, `stock_posterior`, `referencia_id` o cualquier campo extra.

**Salida `MovimientoRespuesta`:** `id`, `inventario_id`, `lote_id`, `usuario_id` nullable, `tipo`, `cantidad`, `stock_anterior`, `stock_posterior`, `fecha_hora`, `observaciones` nullable, `referencia_tipo` nullable y `referencia_id` nullable.

**Salida `ExistenciaRespuesta`:** `lote_id`, `inventario_id` nullable, `stock_actual`, `stock_reservado`, `stock_disponible`. Lote existente sin fila de inventario => saldos 0; `inventario_id=null`.

**Tipos automáticos reservados para integración de negocio:** `ENTRADA_COMPRA`, `DEVOLUCION_ENTRADA`, `DEVOLUCION_PROVEEDOR`. Solo se generan por la función interna `registrar_movimiento_automatico` bajo la misma transacción del llamador (CU compras/devoluciones). Nunca exponer una ruta para falsificar orígenes.

## 4. Reglas de negocio actuales

- Lote, producto y categoría deben existir y estar activos.
- `STOCK_INICIAL`: solo una vez por lote cuando aún no se inicializó inventario; nunca reescribir movimientos históricos.
- Entradas aumentan, salidas reducen stock; impedir stock negativo, stock por debajo de lo reservado y overflow int32.
- `SALIDA_USO` prohibida para lotes vencidos; `SALIDA_VENCIMIENTO` exige lote ya vencido y motivo.
- Ajustes requieren motivo no vacío.
- Todas las escrituras pasan por bloqueo transaccional (`FOR UPDATE`) para serializar stock; no separarlas en commits parciales.
- El servicio interno de automáticos bloquea duplicación de referencia/origen y deja commit/rollback a su llamador.
- Listado de movimientos ordenado por ID descendente, paginado con `skip`/`limit`; sin edit/delete.
- Códigos comunes: `401`, `403`, `404`, `409`, `422`, `503`.

## 5. Qué falta antes del frontend

### Revisión de permisos
CU27 hoy exige administrador con JWT tenant; no aplica aún `requerir_permiso` por acción específica. Revisar `funcion`, `accion`, `rol_funcion` **en lectura** para las siete empresas; no cambiar ACL ni bases sin autorización. Si se decide activar permisos por función, los GET deben requerir `LECTURA` y POST `ESCRITURA`, pero solo después de confirmar nombres y asignaciones válidos para las siete clínicas.

### Cobertura complementaria de pruebas
Revalidar invariantes transaccionales, concurrencia local (dos operaciones sobre el mismo lote), filtros, errores y permisos. Conservar pruebas del aislamiento JWT, expiración, suspensión, rol/usuario y prohibición de origen automático falsificado. No usar DB de clínicas reales como entorno de escritura.

### Posible diferencia de alcance: alertas
El nombre histórico de archivo `cu27_consultar_inventario_alertas.py` y las necesidades de dashboard pueden sugerir **consultas globales, stock mínimo y vencimientos**, pero las tres rutas actuales NO las proporcionan. Si las historias de usuario vigentes exigen alertas automáticas, eso **sigue pendiente y hay que definir/probar API de solo lectura antes del frontend**. Si CU27 del Sprint 3 se limita a *gestionar movimientos*, estas consultas son una ampliación aparte y no deben inventarse. Revisar HU/casos de uso oficiales antes de cambiar el alcance.

## 6. Restricciones operativas

- No crear ni alterar tablas de las siete empresas; todas muestran `v4-sprint3-cu32-cu33`.
- No ejecutar migrador local `scripts/saas/migrate_sprint3_tenants.py --execute` con objetivo v3; no actualizar `version_schema` a mano.
- No tocar .env, secretos, backups, esquemas, Restore, SaaS ni otros CU.
- No reimplementar CU26 ni CU29. Solo reutilizar contratos para pruebas y acceso a lotes.
- No modificar tokens de clientes, inventario real, movimientos históricos, ni exponer las credenciales.
- No commits, pushes, merges, reseteos destructivos ni despliegue automático.

## 7. Criterios de finalización

1. Se conservan y prueban los **tres endpoints CU27** con sus contratos.
2. Todos los tests PostgreSQL/locales que pueden ejecutarse terminan PASS, sin SKIPPED mal reportados.
3. Se prueban reglas de cantidad, reservas, vencimientos, duplicados, concurrencia y rollback.
4. Se valida rechazo de token legacy/SaaS, claims cruzados, usuario/rol inválido y empresa suspendida.
5. Se documenta si RBAC fino y alertas globales quedan como tareas pendientes por definición de datos/alcance.
6. No hay regresiones en CU26 ni cambios en producción. Reporte explícito de archivos y tests.

**No se considera verificada la funcionalidad real en las siete empresas hasta ejecutar una batería E2E autorizada por empresa, preferentemente con consultas de solo lectura y datos de prueba controlados.**
