# MASTER_PROMPT_CU27_BACK — Cierre controlado del backend CU27

## Misión

Eres ingeniero Python/FastAPI/SQLAlchemy/PostgreSQL responsable del **backend CU27 — gestionar movimientos de inventario** del Sprint 3. Trabaja exclusivamente en el repositorio `C:\SI2_Proyecto\clinica-oftalmologica-api`, rama `dev-josias`, sin alterar módulos de compañeros.

## Lectura previa OBLIGATORIA

1. Lee íntegro `CONTEXTO_CU27_BACK.md` en la raíz del repositorio.
2. Inspecciona `git branch --show-current`, `git status --short`, `git diff` y comprueba rama `dev-josias`. Conservar todos los untracked (ZIP, migraciones, `.bak`, etc.).
3. Abre estos archivos reales:
   - `app/modules/gestion_inventario_proveedores/api/movimientos.py`
   - `app/modules/gestion_inventario_proveedores/schemas/movimientos.py`
   - `app/modules/gestion_inventario_proveedores/services/movimientos.py`
   - `app/modules/gestion_inventario_proveedores/models/movimientos.py`
   - `app/modules/gestion_inventario_proveedores/models/catalogo.py`
   - `app/modules/gestion_inventario_proveedores/api/router.py`
   - `app/core/dependencies.py`, `app/database/session.py`, `app/core/tenancy/resolver.py`
   - `app/modules/gestion_usuarios_seguridad/repositories/repository.py`, `services/menu_service.py`
   - `app/main.py`
   - `tests/test_cu27_movimientos.py`, `tests/test_cu27_postgres_integracion.py`, `tests/test_cu26_backend.py`.
4. Si existe documentación oficial HU/CU de Sprint 3 en el workspace, leerla para determinar si las **alertas globales** son parte del CU27 actual o de otro caso de uso. Si no hay evidencia, mantener el alcance de las tres rutas actuales y reportar la brecha; no inventar funcionalidad.
5. Antes de editar, presenta un plan de archivos, tests e invariantes.

## Objetivo

Validar y reforzar el backend existente sin reescribirlo ni introducir rutas falsas. Preservar las 3 rutas reales: POST movimiento manual, GET historial paginado y GET existencias por lote. Endurecer pruebas y documentar el estado de permisos/alertas previo al frontend.

## Fase 1 — Auditoría de funcionalidad

- Documenta cada ruta, modelo de entrada/salida, códigos HTTP y dependencia de JWT tenant.
- Confirma `get_tenant_claims`, `obtener_administrador_actual`, `get_db`, conexión tenant y rechazo fail-closed.
- Examina `services/movimientos.py`: sumas/restas, stock reservado, vencimientos, idempotencia/dobles referencias y bloqueos.
- Determina si hay defectos reales reproducibles; no cambies lógica que ya cumple las reglas.
- Verifica que `registrar_movimiento_automatico()` no se exponga vía HTTP. El servidor, no el cliente, fija usuario, saldos y referencias.
- Revisa si existe función de menú CU27 y acciones en las siete clínicas. Si no existe, reporta plan RBAC; no edites datos para crearlas.

## Fase 2 — Correcciones mínimas, solo con evidencia

- Modifica `api/movimientos.py`, `schemas/movimientos.py`, `services/movimientos.py` o `models/movimientos.py` **solo** para reparar bugs identificados mediante tests.
- Preservar firmas/endpoints salvo necesidad técnica sustentada.
- Mantener `POST` restringido a los cinco tipos manuales: `STOCK_INICIAL`, `SALIDA_USO`, `AJUSTE_ENTRADA`, `AJUSTE_SALIDA`, `SALIDA_VENCIMIENTO`.
- Prohibir `ENTRADA_COMPRA`, `DEVOLUCION_ENTRADA`, `DEVOLUCION_PROVEEDOR` en HTTP. No permitir `usuario_id`, `referencia_*` o saldos en body.
- No crear stock negativo, no reducir por debajo de reserva ni consumir lotes vencidos; respetar bloqueo/commit/rollback. Nunca editar ni borrar movimientos históricos.
- Si hay problema de carreras, corregir de forma transaccional y probarlo con PostgreSQL local; no introducir soluciones solo del frontend.

## Fase 3 — Permisos y alcance de alertas

- Audita `funcion`, `rol_funcion` y `accion` en **modo de solo lectura** antes de integrar `requerir_permiso(..., LECTURA/ESCRITURA)`.
- Si no existe un nombre de función CU27 idéntico y correctamente asignado en las siete empresas, **no inventar permisos ni desplegar enforcement disruptivo**: entregar diagnóstico y migración/propuesta revisable por separado.
- Confirma si CU27 del Sprint 3 exige solo movimientos y existencias por lote, o también inventario consolidado/stock mínimo/alertas de vencimiento. El nombre de un archivo antiguo vacío no basta como especificación.
- Si hay requisitos oficiales de alertas y autorización expresa para ampliarlos, diseñar primero contrato GET read-only, paginación, umbrales y tests; no abrir endpoints inseguros ni consultar todos los tenants desde una sesión clínica.
- Dejar claramente documentado cualquier endpoint faltante requerido para una pantalla futura; no afirmar que existe.

## Fase 4 — Tests obligatorios

- Usar `tests/test_cu27_movimientos.py` y `tests/test_cu27_postgres_integracion.py` como base; agregar tests en `tests/test_cu27_*` cuando sean necesarios.
- Verificar: stock inicial una vez; entradas/salidas y ajustes; cantidad cero/negativa/overflow; reserva; ausencia de inventario; lotes/categorías/productos inactivos; uso de lote vencido; salida por vencimiento válida; motivo obligatorio; orden/paginación/filtro; 404/409/422.
- Prueba concurrencia y rollback sobre PostgreSQL de prueba local, nunca Supabase/tenant real.
- Pruebas de JWT tenant correcto, legacy y SaaS rechazados; claims cruzados de empresa, expiración/suspensión y administrador/no administrador.
- Asegurar que usuario y referencia automática no se puedan falsificar desde HTTP.
- Reejecutar pruebas CU26 para detectar regresiones.

### Comandos orientativos (respetar entorno)

```powershell
python -m pytest -v tests/test_cu27_movimientos.py tests/test_cu27_postgres_integracion.py
python -m pytest -v tests/test_cu26_backend.py
python -m pytest -q tests/test_auth_tenant.py tests/test_tenant_connection_router.py
```

`CU27_TEST_DATABASE_URL` se debe configurar solo apuntando a una base PostgreSQL **local de pruebas**, nunca una DB real. Si falta, indicar que los tests PostgreSQL fueron SKIPPED; no declararlos PASSED. No imprimir URLs con credenciales.

## Archivos con permiso de edición

**Preferentes:**
- `app/modules/gestion_inventario_proveedores/api/movimientos.py`
- `app/modules/gestion_inventario_proveedores/schemas/movimientos.py`
- `app/modules/gestion_inventario_proveedores/services/movimientos.py`
- `tests/test_cu27_movimientos.py`
- `tests/test_cu27_postgres_integracion.py`
- Nuevos tests `tests/test_cu27_*.py` con alcance controlado.

**Solo lectura salvo hallazgo justificado y aprobación explícita:** `app/core/**`, `app/database/**`, `app/modules/gestion_usuarios_seguridad/**`, `app/modules/gestion_inventario_proveedores/models/**`, `database/**`, `scripts/saas/**`, `app/modules/administracion_saas/**` y otros CU.

**Prohibiciones:** modificar tablas/datos reales de las siete empresas, migrar v3 sobre v4, editar `version_schema`, usar credenciales en código, tocar frontend, Git destructivo, commit/push/merge o deploy sin permiso.

## Informe de cierre

Entregar: 1) estado de los tres endpoints; 2) bugs y cambios exactos (o ausencia de bugs); 3) lista de archivos cambiados; 4) resultados pytest reales; 5) auditoría de seguridad/aislamiento; 6) situación de permisos y necesidad o no de alertas según alcance; 7) `git status`; 8) acciones aún necesarias para pruebas con siete empresas. No suponer que pruebas de dos entornos equivalen a E2E de siete.
