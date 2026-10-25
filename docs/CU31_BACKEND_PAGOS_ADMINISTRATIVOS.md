# CU31 — Backend de pagos administrativos y comprobantes

Estado: implementado en código, pero **no apto para producción hasta aplicar y
verificar** `db/cu31_pagos_administrativos_resend.sql` en la base principal y
en cada tenant. Esta preparación no ejecutó SQL, no modificó Supabase/Render y
no envió pagos ni correos reales.

## Contrato HTTP

Todas las rutas clínicas usan `app.core.dependencies.get_db`; el tenant se
resuelve desde el JWT firmado. No se aceptan nombres de bases ni tenant ids en
los cuerpos.

| Método | Ruta | Permiso |
|---|---|---|
| GET | `/pagos/administrativos/servicios-pendientes?paciente_id=...&consulta_clinica_id=...` | Administrador/Recepcionista + `Gestionar pagos/LECTURA` |
| POST | `/pagos/administrativos` | Administrador/Recepcionista + `Gestionar pagos/ESCRITURA` |
| POST | `/pagos/administrativos/{pago_id}/confirmar` | Administrador/Recepcionista + `Confirmar pagos bancarios/ESCRITURA` |
| GET | `/pagos/administrativos` | Administrador/Recepcionista + `Gestionar pagos/LECTURA` |
| GET | `/pagos/administrativos/{pago_id}` | Administrador/Recepcionista + `Gestionar pagos/LECTURA` |
| GET | `/pagos/administrativos/{pago_id}/comprobante` | Administrador/Recepcionista + `Gestionar pagos/LECTURA` |
| GET | `/pagos/administrativos/{pago_id}/notificacion` | Administrador/Recepcionista + `Gestionar pagos/LECTURA` |
| POST | `/pagos/administrativos/{pago_id}/notificacion/reintentar` | Administrador/Recepcionista + `Gestionar pagos/ESCRITURA` |
| GET | `/pagos/{pago_id}/notificacion` | Paciente propietario |
| POST | `/pagos/resend/webhook` | Firma Svix obligatoria; sin JWT |

`POST /pagos/administrativos` requiere `Idempotency-Key` (8–100 caracteres).
Ejemplo:

```json
{
  "paciente_id": 12,
  "servicio_realizado_ids": [301, 302],
  "metodo_pago": "TRANSFERENCIA",
  "referencia_bancaria": "BNB-2026-000123",
  "observaciones": "Verificado en banca empresarial"
}
```

El cuerpo no admite `monto`: FastAPI suma `precio_aplicado` con `Decimal`.
EFECTIVO queda APROBADO; TRANSFERENCIA y QR quedan PENDIENTE hasta el endpoint
de confirmación. El listado de servicios marca `cobrable` y
`conflicto_pago` cuando existe un pago manual pendiente o un intento Stripe
potencialmente cobrable.

## Seguridad financiera

- Los servicios se bloquean con `FOR UPDATE` en orden estable.
- Todos deben estar activos, tener precio positivo y pertenecer al mismo
  paciente y consulta.
- Un pago APROBADO, manual PENDIENTE o intento Stripe pendiente/rechazado
  bloquea el pago manual. Los Stripe no conciliados se rechazan de forma
  conservadora; no se realizan llamadas de red con locks abiertos.
- La idempotencia administrativa usa una columna separada de la referencia
  bancaria/PaymentIntent y un índice único PostgreSQL.
- Pago, detalles, bitácora y alta de outbox comparten un solo commit. Un error
  hace rollback. La confirmación repetida es idempotente.
- El PDF existente se reutiliza tras validar que el pago esté APROBADO y que
  sus detalles/montos sean consistentes. La autorización del paciente y la
  administrativa permanecen separadas.

## Resend y outbox

La transición efectiva a APROBADO crea exactamente una fila
`notificacion_pago` por `(pago_id, COMPROBANTE_PAGO)`. Esto se aplica también
al webhook Stripe. No se envía al crear un PaymentIntent, consultar un PDF ni
repetir un webhook.

El destinatario se deriva exclusivamente de
`pago → pago_detalle → servicio_realizado → paciente → usuario.correo`; nunca
se recibe desde un cliente. Se exige un único paciente, usuario activo y correo
sintácticamente válido. El esquema actual no tiene una marca de correo
verificado: antes de producción debe definirse una política verificable de
confirmación de email. En el entorno de prueba `onboarding@resend.dev` solo
puede enviar al destinatario autorizado por la cuenta; no debe sustituirse
silenciosamente el correo real.

El worker se despliega como servicio separado:

```text
python -m app.modules.gestion_pagos.notificacion_runner
```

Reclama filas con `FOR UPDATE SKIP LOCKED`, confirma el reclamo, genera el PDF
y llama a Resend fuera de la transacción financiera. Usa idempotencia por
tenant/pago/tipo, lease de 10 minutos, máximo 8 intentos y backoff exponencial.
Un fallo deja el pago APROBADO y el PDF disponible. `ACEPTADO_POR_PROVEEDOR`
solo significa aceptación de Resend; `ENTREGADO` exige webhook firmado.

Variables requeridas por API/worker (solo nombres):

- `RESEND_API_KEY`
- `RESEND_FROM_EMAIL`
- `RESEND_FROM_NAME`
- `RESEND_WEBHOOK_SECRET`
- `RESEND_TIMEOUT_SECONDS` (opcional)
- `RESEND_WORKER_BATCH_SIZE` (opcional)
- `RESEND_WORKER_INTERVAL_SECONDS` (opcional)

Eventos Resend a suscribir: `email.delivered`, `email.failed` y
`email.bounced`. URL: `https://<api>/pagos/resend/webhook`.

## Migración pendiente y orden coordinado

La migración agrega `pago.clave_idempotencia`, la tabla de outbox, índices de
unicidad/cola y exactamente dos funciones dentro del módulo existente `Pagos`:

- `Gestionar pagos`: Administrador y Recepcionista → AMBAS inicialmente.
- `Confirmar pagos bancarios`: Administrador → AMBAS y Recepcionista →
  ESCRITURA inicialmente.

Las asignaciones se insertan solamente cuando se crea cada función. Una
reejecución no modifica ni restaura permisos que CU05 haya cambiado o revocado.
El reintento de notificación utiliza `Gestionar pagos/ESCRITURA`; no se crea
una tercera función. La migración no borra ni recrea datos, no toca el control
plane ni tablas históricas.

`notificacion_pago` es backend-only: la migración habilita RLS sin políticas
cliente y revoca todos los privilegios de `PUBLIC`, `anon` y `authenticated`
sobre la tabla y su secuencia. No se usa `FORCE ROW LEVEL SECURITY`: la API y
el worker deben conectarse directamente como propietario de la tabla o con un
rol `BYPASSRLS` revisado. Antes del despliegue se debe comprobar `current_user`,
el propietario de la tabla y `rolbypassrls`; si el usuario de Render no cumple
uno de esos dos contratos, hay que diseñar un rol backend de privilegio mínimo
antes de aplicar la migración.

La FK del outbox usa `ON DELETE RESTRICT`, no `CASCADE`. Una notificación es
evidencia operativa del cobro y no debe desaparecer por un borrado accidental
del pago. El backend no expone borrado de pagos. Esta decisión implica que una
eventual política legal de purga deberá anonimizar o archivar explícitamente la
evidencia antes de eliminar el pago.

1. Mantener la API anterior activa.
2. Tomar/verificar backups de principal y siete tenants.
3. Ejecutar el ensayo en PostgreSQL local/staging cuyo nombre termine en
   `_test` o `_testing` (nunca contra Supabase):

   ```powershell
   $env:PGHOST = 'localhost'
   $env:PGUSER = 'postgres'
   $env:PGPASSWORD = '<solo-secreto-local>'
   ./scripts/db/test_cu31_migration_rollback.ps1 `
       -HostName localhost -Database clinica_cu31_test
   ```

   El helper comprueba el nombre/host, sustituye en memoria el único `COMMIT`
   final por `ROLLBACK`, activa `ON_ERROR_STOP` y ejecuta todas las
   postcondiciones. No altera el archivo SQL ni persiste el esquema ensayado.
4. Aplicarlo con `COMMIT` primero en principal y después, de forma secuencial,
   en los siete tenants; detenerse ante cualquier postcondición fallida.
5. Confirmar por base: 29 tablas, 29 secuencias, 207 columnas y 75 índices;
   `notificacion_pago` vacío; las dos funciones asociadas al módulo `Pagos` y
   las cuatro asignaciones iniciales acordadas.
6. Desplegar API y después el Background Worker con las variables anteriores.
7. Configurar el webhook firmado de Resend y realizar smoke tests con datos no
   clínicos y el único destinatario de prueba autorizado.

Los guardas de aprovisionamiento SaaS se actualizaron a la versión
`v2-cu31` y al inventario 29/29/207/75. Esos conteos se deducen del delta de la
migración y deben confirmarse contra PostgreSQL antes del despliegue; si el
inventario real difiere, no se debe relajar el guard, sino auditar la diferencia.

### Versión de esquema por tenant

La migración clínica no actualiza `saas_control.tenant_database`: el plano de
control vive en otra base y no existe una transacción atómica entre ambas. Para
cada tenant se seguirá este orden:

1. registrar `database_name`, versión previa y backup verificado;
2. ejecutar el ensayo con `ROLLBACK` sobre su clon de pruebas;
3. aplicar el SQL con `COMMIT` en el tenant y revisar todas sus postcondiciones;
4. verificar en esa misma conexión las 29 tablas, 29 secuencias, 207 columnas,
   75 índices, RLS habilitado, privilegios cliente revocados, FK `RESTRICT` y
   las dos funciones RBAC;
5. solo entonces actualizar una fila del control plane:

   ```sql
   BEGIN;
   UPDATE saas_control.tenant_database
      SET version_schema = 'v2-cu31',
          ultima_verificacion = CURRENT_TIMESTAMP
    WHERE database_name = '<TENANT_VERIFICADO>'
      AND estado = 'ACTIVA'
      AND version_schema IS DISTINCT FROM 'v2-cu31'
   RETURNING id, database_name, version_schema, ultima_verificacion;
   -- Debe devolver exactamente una fila. Si no, ROLLBACK e investigar.
   COMMIT;
   ```

6. consultar el inventario de los siete tenants y exigir siete filas activas
   con `version_schema = 'v2-cu31'`. Si la actualización del control plane
   falla, no se repite la migración a ciegas: se verifican primero las
   postcondiciones del tenant y luego se reintenta solamente el metadato.

## Límites conscientes

- La exclusión de doble cobro interno está protegida por locks, estados e
  índices; una transferencia bancaria externa o un Stripe todavía no
  conciliado no puede garantizarse absolutamente desde la base. Por eso los
  conflictos quedan bloqueados hasta conciliación humana/proveedor.
- No se implementaron CU32/CU33, pagos parciales, Angular, Flutter, cambios de
  `.env` ni infraestructura externa. El único cambio RLS es el cierre
  backend-only de `notificacion_pago` descrito arriba.
- El correo automático requiere migración, worker, secreto de webhook y una
  política real de verificación de destinatarios antes de considerarse listo
  para producción.

## Integración posterior con Angular y CU05

`GET /seguridad/menu` y `GET /seguridad/modulos-funciones` exponen las dos
funciones con sus nombres exactos. Angular deberá asociar la ruta de la pantalla
de pagos únicamente a `Gestionar pagos`. `Confirmar pagos bancarios` es un
permiso operativo para habilitar la acción de confirmar dentro de esa pantalla,
no una ruta ni una pantalla independiente. Los cambios realizados desde CU05
se consultan en cada petición y no requieren emitir un JWT nuevo.
