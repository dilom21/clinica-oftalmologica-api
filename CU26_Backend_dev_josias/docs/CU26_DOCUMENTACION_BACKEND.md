# CU26 — Gestionar productos, categorías y lotes

**Proyecto:** Plataforma web SaaS para la Clínica Oftalmológica  
**Sprint:** 3  
**Responsable:** Desarrollo en rama `dev-josias`  
**Tecnología:** Python, FastAPI, SQLAlchemy 2, Pydantic 2, PostgreSQL/Supabase  
**Alcance de esta entrega:** Backend de CU26; CU27 e interfaz Angular pendientes.

## 1. Descripción del caso de uso

Permite que un administrador de una clínica gestione el catálogo de insumos y productos oftalmológicos, clasificados por categoría y organizados en lotes trazables. Cada lote puede tener una fecha de fabricación y vencimiento. Los cambios se aplican exclusivamente a la base de datos de la clínica autenticada mediante el mecanismo multi-tenant existente.

## 2. Objetivo

Registrar, consultar, editar y desactivar categorías, productos y lotes de forma validada, manteniendo las relaciones entre entidades y la integridad del catálogo.

## 3. Actor, precondiciones y postcondiciones

- **Actor:** Administrador de una clínica autenticada con JWT clínico o tenant. La autorización granular basada en `rol_funcion` está planificada para una entrega posterior.
- **Precondiciones:** esquema del Sprint 3 aplicado en la base del tenant, JWT válido y usuario con rol administrador activo.
- **Postcondiciones:** información de catálogo persistida y relacionada correctamente, sin alterar los saldos del inventario.
- **Alternativas:** entradas inválidas devuelven HTTP 422; registro inexistente HTTP 404; duplicados o bloqueo por dependencias HTTP 409; credenciales inválidas HTTP 401 y rol no autorizado HTTP 403.

## 4. Tablas usadas

| Tabla | Función | Relaciones |
|---|---|---|
| `categoria` | Clasifica productos | `categoria.id` ← `producto.categoria_id` |
| `producto` | Identifica productos e incluye stock mínimo de alerta | `producto.id` ← `lote.producto_id` |
| `lote` | Traza número, fabricación, vencimiento y estado | FK a `producto` |
| `inventario` | Solo se consulta al desactivar lote para impedir baja con existencias | FK a `lote` |

Las tablas ya fueron creadas mediante las migraciones del Sprint 3 en las siete bases tenant. El backend **no ejecuta DDL** ni crea tablas automáticamente.

## 5. Arquitectura técnica

El router `app/modules/gestion_inventario_proveedores/api/router.py` expone la API `/inventario-proveedores`; recibe y devuelve modelos Pydantic de `schemas/catalogo.py`. La lógica de negocio está en `services/catalogo.py` y la representación ORM se define en `models/catalogo.py`.

La sesión se obtiene de `app.database.session.get_db`, que interpreta el JWT firmado, resuelve la empresa en el plano de control y selecciona la conexión del tenant. El módulo no acepta `database_name`, `tenant_id` ni parámetros de conexión proporcionados por el cliente. Para proteger la primera entrega se utiliza `Depends(obtener_administrador_actual)`; posteriormente conviene registrar la función de CU26 en `funcion` y `rol_funcion`, habilitando autorizaciones por LECTURA/ESCRITURA.

## 6. Endpoints REST implementados

Prefijo: `/inventario-proveedores`.

| Método | Ruta | Operación |
|---|---|---|
| GET | `/categorias` | Listar categorías |
| POST | `/categorias` | Crear categoría |
| GET | `/categorias/{id}` | Consultar categoría |
| PUT | `/categorias/{id}` | Editar categoría |
| PATCH | `/categorias/{id}/estado` | Activar/desactivar |
| GET | `/productos` | Listar productos, filtro `categoria_id` |
| POST | `/productos` | Registrar producto |
| GET | `/productos/{id}` | Consultar producto |
| PUT | `/productos/{id}` | Editar producto |
| PATCH | `/productos/{id}/estado` | Activar/desactivar |
| GET | `/lotes` | Listar lotes, filtro `producto_id` |
| POST | `/lotes` | Registrar lote |
| GET | `/lotes/{id}` | Consultar lote |
| PUT | `/lotes/{id}` | Editar lote |
| PATCH | `/lotes/{id}/estado` | Activar/desactivar |

Los GET de listas omiten registros inactivos por defecto; se puede enviar `?incluir_inactivos=true`.

## 7. Ejemplo de flujo principal

**1. Crear una categoría** `POST /inventario-proveedores/categorias`:

```json
{"nombre":"Lentes intraoculares","descripcion":"Productos para procedimientos"}
```

**2. Crear un producto** `POST /inventario-proveedores/productos`:

```json
{"categoria_id":1,"codigo":"LENT-001","nombre":"Lente intraocular","unidad_medida":"UNIDAD","stock_minimo":5}
```

**3. Crear un lote** `POST /inventario-proveedores/lotes`:

```json
{"producto_id":1,"numero_lote":"LOT-2026-A","fecha_fabricacion":"2026-01-15","fecha_vencimiento":"2028-01-15"}
```

Los ID son ilustrativos: en una prueba real se deben utilizar los IDs devueltos por los POST anteriores.

**4. Consultar catálogo:** `GET /inventario-proveedores/productos?categoria_id=1` y `GET /inventario-proveedores/lotes?producto_id=1`.

## 8. Reglas de negocio y validaciones

1. El nombre de la categoría no puede estar vacío; la base impide duplicados ignorando espacios extremos y mayúsculas/minúsculas.
2. El código del producto es único; el producto exige una categoría activa.
3. El stock mínimo es un umbral no negativo de alerta, no el stock físico actual.
4. Un lote pertenece a un producto activo; su número es único dentro de ese producto.
5. Cuando ambas fechas existen, vencimiento debe ser igual o posterior a fabricación.
6. No se desactiva una categoría con productos activos ni un producto con lotes activos.
7. No se desactiva un lote cuando tiene existencias o reservas registradas.
8. La actualización del lote no permite trasladarlo a otro producto, para preservar trazabilidad.
9. No se borra físicamente el catálogo: se cambia `estado` para preservar referencias históricas.
10. Las operaciones usan `commit` con rollback si falla una restricción de integridad.

## 9. Pruebas y evidencias

El archivo `tests/test_cu26_backend.py` incluye pruebas aisladas con SQLite en memoria para: (a) flujo completo de categorías, productos y lotes; (b) validaciones y conflictos; (c) rechazo sin token. Son pruebas locales que **no acceden a Supabase** y no sustituyen la prueba integrada con PostgreSQL y tokens reales.

Comandos sugeridos:

```powershell
.\.venv\Scripts\python.exe -m compileall -q .\app\modules\gestion_inventario_proveedores
.\.venv\Scripts\python.exe -m pytest -q .\tests\test_cu26_backend.py
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Para la demostración, abrir `http://127.0.0.1:8000/docs`, autorizar con un token de un administrador clínico y ejecutar POST y GET en secuencia. Capturar: Swagger con rutas CU26; respuesta HTTP 201 al crear; respuesta HTTP 200 al consultar; HTTP 422 por fechas incoherentes; HTTP 409 por categoría duplicada.

## 10. Resultado y trabajo pendiente

**Resultado técnico de esta entrega:** implementación propuesta de 15 endpoints con validación de datos, persistencia ORM y aislamiento por la dependencia multi-tenant preexistente. Las pruebas aisladas pasaron en un entorno de referencia; todavía debe instalarse y verificarse dentro del proyecto local `dev-josias` antes de declarar el backend integrado.

**Pendiente para CU26 antes de producción:** registro granular de permisos `funcion/rol_funcion` en todas las clínicas, auditoría de mutaciones en `bitacora`, pruebas integradas PostgreSQL y validación de casos de concurrencia. **Pendiente CU27:** stock y movimientos transaccionales con bloqueo e idempotencia. **Pendiente frontend:** pantallas Angular.

## 11. Archivos modificados al instalar

- `app/modules/gestion_inventario_proveedores/models/catalogo.py` — ORM.
- `app/modules/gestion_inventario_proveedores/schemas/catalogo.py` — Pydantic.
- `app/modules/gestion_inventario_proveedores/services/catalogo.py` — reglas de negocio.
- `app/modules/gestion_inventario_proveedores/api/router.py` — rutas (respaldará el router anterior).
- `app/main.py` — registro de router (edición conservadora).
- `tests/test_cu26_backend.py` — archivo de pruebas (copiar manualmente).

No se incluye migración SQL nueva, no se modifica `main`, no se ejecuta `git push`, ni se eliminan ZIP o archivos `.bak`.
