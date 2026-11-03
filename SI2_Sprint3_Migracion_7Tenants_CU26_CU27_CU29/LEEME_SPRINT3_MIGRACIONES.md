# SI2 js — Clínica Oftalmológica · Migraciones CU26, CU27 y CU29

**24 de octubre de 2026 — Paquete de migración para los 7 tenants existentes**

**Estado al generar este paquete:** los tenants **NO han sido migrados** por ChatGPT. Se verificó en Supabase que los siete aparecen `ACTIVA`, todos con `version_schema = v2-cu31`. El conector de Supabase consulta la base `postgres` y no expone selección de otra base del mismo servidor. Por ello el SQL no se ejecutó remotamente.

## Alcance real

* **Si un tenant no tiene `proveedores`**, se crea esa tabla con el esquema y los índices (`ix_proveedores_nit`, `ix_proveedores_id`) compatibles con `public.proveedores` de la base principal. La tabla nueva queda **vacía**. Nunca se copia el proveedor que existe en la base central.
* Se crean siete tablas: `categoria`, `producto`, `lote`, `inventario`, `movimiento_inventario`, `compra`, `detalle_compra`.
* La migración conserva las FK, CHECK, UNIQUE e índices de las migraciones CU26, CU27 y CU29 que ya fueron aplicadas a `postgres.public`.
* En las siete tablas nuevas se habilita RLS y se revocan permisos directos a `anon` y `authenticated`. El backend deberá aplicar autenticación y permisos funcionales; no se crean políticas públicas.
* Se registra la nueva migración y su SHA-256 en `app_migrations.schema_migration` (ledger de cada tenant) y se actualiza `saas_control.tenant_database.version_schema` a `v3-sprint3-cu26-cu27-cu29` después de verificar.
* **No se migran CU32 ni CU33**; no se crean `plan_pago` ni `cuota`, porque faltan acuerdos de diseño y pagos.

## Antes de ejecutar

1. Respaldar el tenant de prueba y confirmar que puede restaurarse; no ejecutar restore ahora.
2. Asegurarse de que no haya migraciones o escrituras incompatibles en curso. Coordinar la ventana de mantenimiento.
3. Tener Git y un respaldo de los archivos del repositorio local. La extracción agrega **dos archivos nuevos**, sin sobrescribir `migrate_active_tenants.py` ni `manifest.json` de CU31.
4. Verificar que el equipo tiene Python, `psycopg`, `sqlalchemy` y un `DATABASE_URL` funcional en el `.env` del backend, apuntando a la base **`postgres`**, y que la conexión usa pooler de sesión **5432**, no de transacciones 6543.
5. Desde PowerShell, extraer el ZIP al directorio raíz del backend `C:\SI2_Proyecto\clinica-oftalmologica-api` y ejecutar:

```powershell
cd C:\SI2_Proyecto\clinica-oftalmologica-api
.\.venv\Scripts\python.exe .\scripts\saas\migrate_sprint3_tenants.py --plan --database tenant_vision_clara
```

El modo `--plan` es **solo lectura**. Debe devolver `plan_hash`, esquema esperado y estado de tablas/ledger sin contraseñas.

## Ejecutar primero en un tenant (solo tras validar el plan)

```powershell
$env:SPRINT3_TENANT_MIGRATIONS_ALLOW = '1'
.\.venv\Scripts\python.exe .\scripts\saas\migrate_sprint3_tenants.py --execute --database tenant_vision_clara --confirm-hash HASH_EXACTO_DEL_PLAN
Remove-Item Env:SPRINT3_TENANT_MIGRATIONS_ALLOW
```

Sustituir `HASH_EXACTO_DEL_PLAN` por el hash real impreso en el paso anterior. **No es una contraseña**. Si el esquema cambió después del plan, la ejecución se rehúsa. Si hay incompatibilidades de `proveedores`, `usuario.id` o el ledger CU31, el proceso se detiene sin intentar remendar tablas existentes.

## Planificar y ejecutar los siete tenants restantes

Después de comprobar de forma independiente el primer tenant y sus endpoints, generar un nuevo plan:

```powershell
.\.venv\Scripts\python.exe .\scripts\saas\migrate_sprint3_tenants.py --plan --all
```

Comprobar que aparecen los 7 tenants esperados. El primer tenant debería figurar como ya aplicado. Para ejecutar el plan general:

```powershell
$env:SPRINT3_TENANT_MIGRATIONS_ALLOW = '1'
.\.venv\Scripts\python.exe .\scripts\saas\migrate_sprint3_tenants.py --execute --all --confirm-hash HASH_EXACTO_DEL_NUEVO_PLAN
Remove-Item Env:SPRINT3_TENANT_MIGRATIONS_ALLOW
```

El ejecutor procesa cada base en una transacción individual. Si una falla se detiene; los tenants ya migrados no se revierten automáticamente. Para reintentar, investigar el error y generar **un plan nuevo**. Si la transacción tenant terminó pero falló actualizar `version_schema` en `saas_control`, el programa puede reanudar actualizando solamente esa versión tras validar el ledger y las tablas.

## Bases del plano de control (7)

- `tenant_instituto_vision`
- `tenant_medico_ocular`
- `tenant_oftalmo_norte`
- `tenant_oftalmocare`
- `tenant_vision_clara`
- `tenant_vista_sur`
- `tenant_visual_oriental`

## Notas de negocio obligatorias para Austin y Josias

* CU29 **no** aumenta stock automáticamente al crear `compra`; su backend debe recibir una compra `CONFIRMADA` y registrar la recepción completa como operación atómica: bloquear inventario, verificar el saldo, insertar movimiento `ENTRADA_COMPRA` con `referencia_tipo='DETALLE_COMPRA'`, `referencia_id=detalle_compra.id`, y actualizar `inventario.stock_actual` en la misma transacción.
* El índice único `uq_movimiento_entrada_detalle_compra` ayuda a impedir recepciones duplicadas por detalle. No sustituye la transacción ni el control de concurrencia del servicio.
* `lote.numero_lote` es único dentro de cada producto, no globalmente.
* El diseño actualmente admite **recepción íntegra**, no parcial. No implementar recepción parcial sin revisar tablas y restricciones.
* `inventario` almacena existencia por lote y `producto.stock_minimo` es solo umbral de alertas. No duplicar stock en `producto`.
* La migración de estructura no concede permisos de uso a roles del sistema. Crear primero `funcion`/`rol_funcion` y verificar permisos antes de habilitar pantallas.
* No utilizar el migrador antiguo de CU31 para revertir `version_schema` a v2; su lista de versiones y verificaciones están acopladas a CU31.

## Pruebas y límites

Se verificó que el archivo Python compila y se comprobó el contrato SQL con un análisis estático. No se ha realizado una prueba de conexión ni una ejecución real en una base tenant desde este entorno. Ante el primer error, detenerse y compartir solo el mensaje **sin credenciales**.
