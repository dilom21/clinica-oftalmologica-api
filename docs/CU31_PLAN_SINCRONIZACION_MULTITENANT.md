# CU31: plan de sincronizacion multitenant

Estado: migrador implementado y probado de forma aislada; no ejecutado contra
PostgreSQL. Ninguna base, variable de entorno ni servicio de Render fue
modificado durante esta preparacion.

## 1. Fuente y objetivo

Se recuperaron mediante `SELECT` desde
`supabase_migrations.schema_migrations` las sentencias originales:

1. `20261007211200` (`crear_pago_y_pago_detalle`): crea `pago`,
   `pago_detalle`, sus restricciones, comentarios e indices iniciales.
2. `20261007211247` (`ajustar_indices_pago`): elimina
   `idx_pago_referencia_transaccion`, redundante con la restriccion `UNIQUE`.

Los archivos de `versions/` conservan esas sentencias. El segundo archivo solo
normaliza el salto de linea final requerido por el repositorio; el MD5 de la
fuente y el SHA-256 del artefacto local estan fijados en `manifest.json`.
Cualquier diferencia posterior debe detener el proceso.

El cambio posterior `20261019000000` es el SQL CU31 ya revisado en
`db/cu31_pagos_administrativos_resend.sql`. Agrega la idempotencia de pago,
el outbox `notificacion_pago`, seguridad y las dos funciones RBAC acordadas.

## 2. Orden exacto por tenant

1. Comprobar que el tenant esta `ACTIVA`, que su base coincide con el registro
   de control y que no hay otra migracion en curso.
2. Ejecutar `000_preflight_cu31.sql` en una conexion `READ ONLY`.
3. Capturar una huella del objeto historico `servicios_oftalmologicos`:
   columnas, restricciones, indices y cantidad de filas. No se copia ni altera.
4. Crear y verificar `app_migrations.schema_migration` con
   `bootstrap_ledger.sql`. Esta es infraestructura del migrador, no una tabla
   funcional en `public`.
5. Adquirir un advisory lock exclusivo asociado a base + plan CU31.
6. Aplicar `20261007211200` y registrar su version/checksum en la misma
   transaccion. Si falla cualquier sentencia, hacer rollback y detener todo el
   lote.
7. Aplicar `20261007211247` y registrar su version/checksum en la misma
   transaccion.
8. Aplicar `20261019000000`. Este archivo contiene su propio `BEGIN/COMMIT` y
   es idempotente. Solo despues del commit se registra el checksum. Si el
   proceso cae en esa pequena ventana, la reanudacion vuelve a ejecutar este
   SQL idempotente y luego valida; nunca se marca como aplicado por anticipado.
9. Ejecutar `999_verify_cu31.sql` en `READ ONLY` y comparar la huella historica
   con la capturada en el paso 3.
10. Ejecutar pruebas de humo del API contra la base de prueba. En produccion,
    limitarse a comprobaciones estructurales y consultas sin datos clinicos.
11. En una transaccion de la base de control, actualizar
    `saas_control.tenant_database.version_schema = 'v2-cu31'` solamente si los
    pasos 6 a 9 finalizaron correctamente.
12. Liberar el lock y continuar con el siguiente tenant. El comportamiento por
    defecto es detener el lote en el primer error.

No debe usarse `scripts/saas/provision_tenant.py`: ese flujo aprovisiona o
reconcilia bases completas y su preflight por conteos no representa un upgrade
incremental. Ademas, actualmente espera 75 indices en la fuente, mientras la
base principal auditada tiene 78.

## 3. Diferencias adicionales de CU31

Sobre las dos migraciones originales, el SQL definitivo debe cubrir:

- `pago.clave_idempotencia varchar(100) NULL` y su indice unico parcial;
- `notificacion_pago` con 16 columnas, PK, dos `UNIQUE`, dos `CHECK`, FK a
  `pago` con `ON DELETE RESTRICT` y los indices parciales de proveedor/cola;
- RLS habilitado en el outbox, sin politicas, y revocacion a `PUBLIC`, `anon` y
  `authenticated`, incluida su secuencia;
- funciones exactas `Gestionar pagos` y `Confirmar pagos bancarios` dentro del
  modulo Pagos;
- asignacion inicial: Administrador/AMBAS y Recepcionista/AMBAS para gestionar;
  Administrador/AMBAS y Recepcionista/ESCRITURA para confirmar;
- verificaciones fuertes de columnas, defaults, restricciones, indices, RLS,
  privilegios y RBAC.

`pago_detalle -> pago` conserva `CASCADE`, porque el detalle no tiene sentido
sin su cabecera. `notificacion_pago -> pago` usa `RESTRICT`, porque el outbox es
evidencia operativa del envio y no debe desaparecer por borrado en cascada.

## 4. Conteos y legado

No se debe exigir igualdad ciega con `postgres.public`. Los siete tenants
parten de 27 tablas y 67 indices, pero conservan la tabla historica
`servicios_oftalmologicos` y sus dos indices, ausentes en el esquema canonico.

El delta esperado del plan es:

- tres tablas funcionales nuevas;
- tres secuencias identity nuevas;
- siete indices netos de las dos migraciones originales;
- seis indices adicionales de idempotencia/outbox.

Por ello, un tenant con el baseline auditado terminaria previsiblemente con 30
tablas y 80 indices, frente a 29 y 78 en `postgres`. Esos totales son una
alarma secundaria; la aceptacion se decide por objetos y huella, no por el
numero aislado. Esto tambien evita repetir la confusion historica entre 69 y
72 indices, que dependia del momento de la migracion y de indices redundantes.

## 5. Diseno del migrador Python

El comando separado `scripts/saas/migrate_active_tenants.py` implementa el plan
sin reutilizar el aprovisionador. Usa SQLAlchemy/psycopg ya instalados y ofrece:

- `--plan`: solo lectura; enumera tenants, ledger, hashes y pre/postcondiciones;
- `--execute --confirm-plan <hash-del-plan>`: habilita cambios despues de la
  autorizacion explicita y de configurar temporalmente
  `CU31_TENANT_MIGRATIONS_ALLOW=1`.

Reglas del comando:

- obtener URLs desde la configuracion existente sin imprimir usuario, host
  completo, contrasena ni query parameters;
- ordenar tenants de forma determinista y aplicar uno por uno;
- recalcular SHA-256 antes de abrir una transaccion;
- si una version existe con el mismo checksum, omitirla;
- si una version existe con otro checksum, abortar;
- para migraciones controladas por el runner, DDL + asiento del ledger forman
  una sola transaccion;
- registrar en el ledger solo ejecuciones confirmadas; los errores se escriben
  en un reporte local saneado y el proceso termina con codigo distinto de cero;
- usar timeouts y advisory lock para evitar dos despliegues simultaneos;
- no avanzar `version_schema` hasta que el verificador final pase;
- producir un resumen por tenant: omitida/aplicada/error, version anterior,
  version final, checksums y duracion, sin datos clinicos ni secretos.

Para trazabilidad central durable puede agregarse, en una migracion posterior
del control plane, una tabla `saas_control.tenant_migration_run`. No es requisito
para ejecutar este plan: la autoridad de que una migracion fue aplicada reside
en el ledger de cada tenant y `version_schema` solo expresa el estado final.

El hash del plan cubre las tres migraciones, el bootstrap del ledger, el
preflight, el verificador final, los tenants seleccionados y el estado actual
de cada ledger. El ejecutor rechaza el puerto 6543: el ensayo y la migracion
deben usar conexion directa o Supavisor session mode en 5432, porque requieren
un advisory lock de sesion.

### Comandos preparados (no ejecutar sin autorizacion)

Base descartable cuyo nombre termine en `_test` o `_testing`:

```powershell
$env:CU31_TEST_DATABASE_URL = 'postgresql://ROL:CLAVE@HOST:5432/tenant_cu31_test?sslmode=require'
python scripts/saas/migrate_active_tenants.py --plan --test-url-env CU31_TEST_DATABASE_URL
$env:CU31_TENANT_MIGRATIONS_ALLOW = '1'
python scripts/saas/migrate_active_tenants.py --execute --test-url-env CU31_TEST_DATABASE_URL --confirm-plan '<HASH_DEL_PLAN>'
Remove-Item Env:CU31_TENANT_MIGRATIONS_ALLOW
Remove-Item Env:CU31_TEST_DATABASE_URL
```

Lote de tenants activos, solamente despues de aprobar el ensayo:

```powershell
python scripts/saas/migrate_active_tenants.py --plan
$env:CU31_TENANT_MIGRATIONS_ALLOW = '1'
python scripts/saas/migrate_active_tenants.py --execute --confirm-plan '<HASH_DEL_PLAN>'
Remove-Item Env:CU31_TENANT_MIGRATIONS_ALLOW
```

Para limitar una futura ejecucion a un tenant registrado se agrega
`--database tenant_nombre`. El modo descartable comprueba que el nombre no
coincida con ninguna base registrada y nunca actualiza el control plane.

## 6. Ensayo independiente y recuperacion

1. Obtener backup verificado del tenant elegido como plantilla: roles, esquema
   y datos. Conservarlo cifrado y fuera del repositorio.
2. Restaurar en una base PostgreSQL/Supabase de pruebas independiente. Para
   datos funcionales, usar un subconjunto anonimizado; nunca reutilizar correos,
   documentos, telefonos, observaciones o datos clinicos reales.
3. Verificar version de PostgreSQL y extensiones. El changelog de Supabase de
   septiembre de 2026 obliga a revisar si existen indices `ltree`/`btree_gist`,
   usos heredados de `pgcrypto` u operadores personalizados antes de un upgrade.
4. Ejecutar primero `--plan` y guardar su hash. Luego realizar un ensayo global
   con `BEGIN`/`ROLLBACK` para las dos migraciones originales y las validaciones.
5. Hacer un segundo ensayo con commit en la base descartable, porque persistencia
   del ledger, reinicio y reanudacion no se demuestran con rollback.
6. Probar: pago presencial, pago Stripe ya existente, reintento con igual clave,
   rechazo de clave reutilizada con payload distinto, multiples detalles,
   generacion del PDF, creacion del outbox y reintento de Resend sin duplicar el
   cobro.
7. Forzar fallos despues de cada frontera transaccional y comprobar que la
   reanudacion omite versiones confirmadas y repite de forma segura CU31 si el
   ledger aun no se escribio.
8. Comparar huellas antes/despues y ejecutar toda la suite backend.
9. Recuperacion: ante error en un tenant activo, detener el lote, no cambiar su
   `version_schema` y restaurar solo si hubo un commit no reversible que no pueda
   corregirse hacia adelante. Las migraciones originales son DDL transaccional;
   el rollback es la primera defensa.

## 7. Seguridad separada de `postgres.public`

La correccion de las 28 tablas de la base principal con RLS desactivado y grants
potencialmente amplios no debe mezclarse con CU31. Primero se debe inventariar
por tabla:

- si es consumida por Data API o solo por FastAPI;
- privilegios efectivos de `anon`, `authenticated`, `service_role` y del rol
  directo de Render/worker;
- propietario, `BYPASSRLS`, politicas existentes y consultas reales.

Plan recomendado:

1. Crear un rol LOGIN dedicado para FastAPI/worker con privilegios minimos; no
   asumir que sera `postgres` ni que tendra `BYPASSRLS`.
2. Para tablas exclusivamente backend, retirar grants de Data API y mantenerlas
   fuera de acceso de `anon`/`authenticated`.
3. Para tablas realmente expuestas, habilitar RLS y crear politicas de propiedad
   o tenant; `TO authenticated` por si solo no autoriza a nivel de fila.
4. Desplegar por grupos con pruebas de lectura/escritura del backend antes de
   retirar el acceso anterior.
5. Solo al final rotar Render al rol dedicado. Mantener un rollback de grants y
   conexion durante la ventana.

La configuracion de exposicion de Data API y RLS son capas distintas. Desde
2026 Supabase permite proyectos donde las tablas nuevas no reciben grants de
Data API automaticamente; las bases existentes deben auditarse explicitamente.

## 8. Puertas de autorizacion pendientes

Antes de cualquier cambio real se necesita autorizacion explicita para:

1. crear/restaurar la base de pruebas;
2. ejecutar `bootstrap_ledger.sql` y las tres migraciones en esa base;
3. ejecutar pruebas funcionales con credenciales no productivas;
4. aprobar el resultado del ensayo, backup y ventana de mantenimiento;
5. ejecutar por tenant activo, en el orden acordado;
6. actualizar `version_schema` despues de cada validacion;
7. desplegar Render y el worker en una autorizacion separada;
8. abordar el plan RLS/grants de la base principal como cambio independiente.