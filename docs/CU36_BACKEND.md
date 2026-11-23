# CU36 — Anular o corregir pago (backend)

Sprint 4 · Web · Rama `dev-josias`.
Este documento describe la implementación backend de CU36, sus reglas, estados,
endpoints, migración y el plan de validación multiempresa.

## 1. Alcance implementado

Se cubren los tres tipos de pago:

1. **Directos**: efectivo, transferencia y QR.
2. **Stripe**: cancelación de intents no capturados, reembolsos totales y
   parciales, timeouts con resultado desconocido, webhooks duplicados/fuera de
   orden y captura tardía (`succeeded`) sobre un pago anulado.
3. **Cuotas**: reversión de la aplicación a la cuota, recálculo de cuota y plan,
   y bloqueo explícito del reembolso parcial (decisión D4 pendiente).

## 2. Modelo: libro de ajustes (D1)

| Tabla | Rol |
|---|---|
| `pago_ajuste` | Cabecera firmada de cada anulación, corrección, devolución, reembolso o conciliación. |
| `pago_ajuste_detalle` | Distribución del importe del ajuste por `servicio_realizado_id`. |
| `stripe_webhook_huerfano` | Refund verificado sin metadata en el tenant, pendiente de conciliación manual (E-3). |
| `saas_control.stripe_webhook_huerfano` | Cuarentena **central** de refunds cuyo PaymentIntent no prueba ningún tenant (I-2). Solo SUPERADMIN la atribuye a una empresa; nunca se escribe en la base legacy. |
| `bitacora` | Auditoría de la acción y del actor (complementaria, no sustituye). |

`pago.monto` y `pago_detalle.monto_aplicado` **nunca** se sobrescriben.

Campos controlados:

- `tipo`: `ANULACION`, `CORRECCION`, `DEVOLUCION`, `REEMBOLSO`, `RECONCILIACION`.
- `naturaleza`: `ERROR_DE_REGISTRO`, `PAGO_DUPLICADO`, `SERVICIO_CANCELADO`,
  `SOBREPAGO`, `PAGO_INSUFICIENTE`, `REVERSION_FINANCIACION`, `OTRO`.
- `efecto_caja`: `SIN_EFECTO`, `DEVOLUCION_MANUAL`, `DEVOLUCION_STRIPE`.
- `estado`: `PENDIENTE_PROVEEDOR`, `CONFIRMADO`, `FALLIDO`, `CANCELADO`,
  `DESCONOCIDO`.
- `importe` firmado: positivo solo en `CORRECCION`/`ERROR_DE_REGISTRO`, negativo
  en devoluciones/reembolsos, cero en anulación y conciliación.
- `clave_idempotencia` única; `referencia_externa` (refund id) única parcial.
- `creado_por`, `confirmado_por` y `conciliado_por` son **nullable**: los ajustes
  automáticos creados por webhook (`RECONCILIACION`) no tienen actor humano y se
  registran con `creado_por = NULL`. Nunca se inventa un identificador de usuario;
  el modelo SQLAlchemy, la migración y el esquema de respuesta coinciden en ello.

## 3. Importe vigente: único cálculo reutilizable

`app/modules/gestion_pagos/repositories/saldos.py`.

```
importe_base   = pago.monto / pago_detalle.monto_aplicado  si estado ∈ {APROBADO, REEMBOLSADO}
importe_vigente = importe_base + Σ ajustes CONFIRMADO
```

Invariantes aplicados:

1. La base se cuenta una sola vez.
2. Solo `CONFIRMADO` afecta saldos.
3. `ANULADO` aporta cero y su ajuste vale cero (sin doble descuento).
4. `REEMBOLSADO` conserva la base y se compensa con negativos confirmados.
5. Reembolso parcial → pago `APROBADO` con vigente positivo.
6. `PENDIENTE_PROVEEDOR`/`DESCONOCIDO` no se contabilizan pero bloquean.
7. Un `succeeded` tardío se reconcilia (nunca se ignora).

Consumidores migrados al helper: `repositories/repository.py`
(`obtener_ids_servicios_cubiertos_por_pago_aprobado`, `listar_servicios_pendientes_pago`,
`obtener_pagos_aprobados_para_servicios_excluyendo`),
`repositories/planes_cuotas.py` (`montos_aprobados_por_servicio`),
`repositories/administrativos.py` (conflictos de cobro) y
`gestion_historial_clinico/tiene_pago_asociado` (CU22).

## 4. Reglas de negocio y transiciones

| Situación | Resultado |
|---|---|
| Pendiente nunca capturado (manual) | `ANULADO` + ajuste `ANULACION` importe 0. |
| Pendiente Stripe | Se cancela en el proveedor y solo entonces `ANULADO`. `succeeded` en provider → 409, use reembolso. |
| Aprobado sin dinero real | `ANULADO` con evidencia obligatoria y rol administrador (Stripe aprobado no se permite: use reembolso). |
| Aprobado con dinero real | No se anula: `DEVOLUCION`/`REEMBOLSO` con evidencia. |
| Devolución parcial confirmada | Pago sigue `APROBADO`, vigente = base − devuelto. |
| Devolución total confirmada | `REEMBOLSADO` y vigente 0. |
| Error de digitación | `CORRECCION` firmada (a la baja o al alza con evidencia); original inmutable; no puede dejar vigente 0 ni superar el precio aplicado. |
| Pago insuficiente real | `CORRECCION` negativa con naturaleza `PAGO_INSUFICIENTE`; el saldo queda pendiente. |
| Sobrepago real | `DEVOLUCION`/`REEMBOLSO` con evidencia. |
| Duplicado sin segundo movimiento | `ANULACION` con evidencia. |
| Duplicado con doble cobro real | `DEVOLUCION`/`REEMBOLSO`. |

Estados de `pago` usados: `PENDIENTE → ANULADO`, `RECHAZADO → ANULADO`,
`APROBADO → REEMBOLSADO` (solo total confirmado) o permanece `APROBADO`.

Roles: lectura con `Gestionar pagos/LECTURA`; escritura con
`Gestionar pagos/ESCRITURA`; además, anular un aprobado, devolver, reembolsar y
conciliar exige rol `admin`/`administrador`. Recepción puede anular pendientes no
cobrados y corregir a la baja. No se crean permisos ni seeds nuevos (D5).

## 5. Endpoints (prefijo `/pagos/administrativos`)

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/{pago_id}/anular` | Anula un pago no capturado. `Idempotency-Key` obligatorio. |
| POST | `/{pago_id}/corregir` | Corrección firmada con distribución por servicio. |
| POST | `/{pago_id}/reembolsos` | Devolución manual o reembolso Stripe. |
| GET | `/{pago_id}/ajustes` | Historial + importe base/vigente/reembolsado. |
| GET | `/ajustes/{ajuste_id}` | Detalle de un ajuste. |
| POST | `/ajustes/{ajuste_id}/conciliar` | Concilia una operación pendiente/desconocida. Acepta `refund_id` + `evidencia` para atribución manual auditada. |
| GET | `/reembolsos-huerfanos` | Refunds verificados sin metadata pendientes de conciliación (admin). |
| POST | `/reembolsos-huerfanos/{id}/atribuir` | Vincula un refund huérfano a un pago con `refund_id` explícito, evidencia y distribución por servicio. |

Administración SaaS (prefijo `/saas`, solo `SUPERADMIN`):

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/stripe/reembolsos-huerfanos` | Cuarentena central de refunds sin tenant resoluble (I-2). |
| POST | `/stripe/reembolsos-huerfanos/{id}/atribuir` | Atribución manual explícita a una `empresa_codigo`; valida PaymentIntent, moneda e importe vigente y copia el evento a la cuarentena del tenant. No mueve dinero. |

Ejemplo de corrección:

```json
POST /pagos/administrativos/500/corregir
Idempotency-Key: cor-500-2026-11-16

{
  "motivo": "Error de digitacion del cajero",
  "naturaleza": "ERROR_DE_REGISTRO",
  "importe": "-5.00",
  "evidencia": "Arqueo de caja 88/2026",
  "servicios": [{"servicio_realizado_id": 1001, "monto": "-5.00"}]
}
```

`extra="forbid"` en todos los cuerpos; nunca se acepta `tenant_id`,
`empresa_id`, estado destino ni importe original desde el cliente.

## 6. Idempotencia y concurrencia

- `Idempotency-Key` obligatorio en toda escritura; única en `pago_ajuste`.
- **E-7:** la misma clave solo devuelve el resultado original si coinciden pago,
  tipo, naturaleza, importe, moneda, efecto de caja y la distribución por
  servicio. Cualquier diferencia (incluido reutilizar una clave `CORRECCION`
  en un `REEMBOLSO`) responde 409.
- Locks: pagos de cuota respetan `plan → cuota → intento → pago`.
- **E-4:** ninguna llamada externa se ejecuta con locks abiertos. En
  `reembolsar_pago` y `conciliar_ajuste` se registra/lee, se cierra la
  transacción, se llama a Stripe y se vuelve a bloquear y revalidar el estado
  antes de persistir. Un fallo externo no deja locks retenidos.
- Reintentos del mismo ajuste reutilizan la misma clave de idempotencia de Stripe
  (`clinica-tenant-{tenant_id}-ajuste-{ajuste_id}`).

## 7. Stripe

`providers/base.py` y `stripe_provider.py` incorporan
`crear_reembolso`, `recuperar_reembolso` y `listar_reembolsos`.
`ErrorProveedorPagoDesconocido` distingue timeout/conexión de un fallo definitivo.

Estados del reembolso: `succeeded → CONFIRMADO`, `pending → PENDIENTE_PROVEEDOR`,
`failed → FALLIDO`, `canceled → CANCELADO`, timeout → `DESCONOCIDO`.

Conciliación de un `REEMBOLSO` Stripe: nunca se toma el primer refund de
`Refund.list`. Un refund solo se atribuye al ajuste si coinciden
`metadata.ajuste_id` (y `metadata.pago_id` cuando está presente), el importe en
unidad mínima y la moneda, y debe ser **el único** candidato atribuible. Sin
coincidencia inequívoca (`0` candidatos, varios candidatos o refunds sin
metadata de ajuste) el ajuste permanece `DESCONOCIDO`/`PENDIENTE_PROVEEDOR`, no
se contabiliza y la API responde 409. El refund ya vinculado también se
revalida antes de confirmar.

Webhooks soportados: `refund.created`, `refund.updated`, `refund.failed` además de
los de `payment_intent.*`. El enrutamiento del tenant usa únicamente metadata
firmada del evento (`ajuste_id`, `pago_id`, marcadores de tenant) y se verifica la
firma antes de abrir cualquier base.

**I-2 — refunds del dashboard y cuarentena central.** Un refund creado desde el
dashboard de Stripe no lleva metadata propia. El router consulta el
`PaymentIntent` en el proveedor (fuente confiable, nunca datos del cliente) y usa
su metadata generada por el servidor para resolver el tenant. Si el
PaymentIntent no prueba ningún tenant (sin metadata, metadata parcial o
inexistente), el evento se persiste en `saas_control.stripe_webhook_huerfano`
antes de responder 2xx: **nunca se envía automáticamente a la base legacy**. La
respuesta 2xx solo ocurre tras la escritura durable; un fallo de persistencia
responde 5xx y Stripe reintenta. La atribución es manual, por `SUPERADMIN`, con
`empresa_codigo`, motivo y evidencia; se valida que el PaymentIntent exista en
esa empresa, que el pago sea Stripe, que la moneda coincida y que el importe no
supere el vigente. El evento se copia a la cuarentena del tenant para que su
administrador complete el ajuste CU36; la cuarentena central no contabiliza
dinero. No se aceptan atribuciones ambiguas ni se adivina el tenant.

**I-3 — `payment_intent.canceled`.** Toda cancelación de un intento no capturado
pasa por `anular_intento_no_capturado`: registra el ajuste `ANULACION` (importe
0) y su bitácora en la misma transacción. Un pago `APROBADO` **no se anula
automáticamente**: se crea una discrepancia `RECONCILIACION`
`PENDIENTE_PROVEEDOR` idempotente y se responde 2xx para evitar reintentos
infinitos de Stripe. Un `ANULADO`/`REEMBOLSADO` repetido es un no-op idempotente.

**E-3 — refunds huérfanos:** un refund verificado sin metadata de ajuste/tenant ya
no responde 409 (bucle de reintentos sin rastro). Se persiste en
`stripe_webhook_huerfano` y solo entonces se responde 2xx; si la escritura falla
se responde 5xx para que Stripe reintente. La atribución automática (E-2) sigue
siendo estricta: sin `metadata.ajuste_id` inequívoca no se contabiliza. Un
administrador puede conciliar manualmente con `refund_id` explícito, evidencia y
distribución por servicio; se validan PaymentIntent, importe, moneda, referencia
y que la metadata no apunte a otro ajuste/pago. El ajuste se crea como
`REEMBOLSO`/`DEVOLUCION_STRIPE` confirmado y el evento pasa a `ATRIBUIDO`.

**E-5 — `succeeded` tardío en cuotas:** la comprobación de pago `ANULADO` se
evalúa antes del retorno temprano de la rama de cuotas. Se restaura el pago a
`APROBADO`, se aplica/registra la cuota según corresponda, se crea un ajuste
`RECONCILIACION` `PENDIENTE_PROVEEDOR` (idempotente por clave) y la bitácora
`RECONCILIAR_STRIPE_SUCCEEDED_TARDIO`.

**Hallazgo crítico corregido:** un `payment_intent.succeeded` sobre un pago local
`ANULADO` ya no responde `procesado=true` sin efecto. Se restaura el pago a
`APROBADO` (dinero realmente capturado), se registra bitácora
`RECONCILIAR_STRIPE_SUCCEEDED_TARDIO` y se abre un ajuste `RECONCILIACION`
`PENDIENTE_PROVEEDOR` para gestionar el reembolso. Es idempotente por clave.

## 8. Cuotas

- Anulación/reembolso total confirmado revierte `intento.aplicado_a_cuota`,
  devuelve la cuota a `PENDIENTE` y, si el plan estaba `COMPLETADO`, lo vuelve
  `ACTIVO`. Las demás cuotas no se tocan.
- Corrección de importe sobre un pago aplicado a cuota: **bloqueada** (409).
- Reembolso parcial de una cuota: **bloqueado** (409, D4 pendiente), porque el
  modelo `cuota.estado` no representa cobertura parcial.

## 9. Compatibilidad con otros CU

- **CU22**: `tiene_pago_asociado` ya no bloquea si el pago está `ANULADO`,
  `REEMBOLSADO` o es un rechazo manual; sigue bloqueando `PENDIENTE`, `APROBADO`,
  intentos Stripe rechazados y ajustes abiertos.
- **CU31**: `confirmar_pago` rechaza confirmar mientras exista un ajuste abierto.
- **E-6 / CU32**: toda anulación de intento no capturado pasa por
  `anular_intento_no_capturado`, que registra `ANULACION` importe 0 y bitácora.
  Un pago `PENDIENTE`/`RECHAZADO` se anula; un `APROBADO` responde 409 y exige
  conciliación administrativa. Ni `planes_cuotas.crear_intencion_stripe` ni el
  reintento Stripe cambian estados fuera del libro CU36.
- **E-8**: si `precio_aplicado` es `NULL` o `<= 0`, una corrección positiva
  responde 409: no se puede validar el tope y no se inventan precios.
- **CU34**: `PagoAdministrativoRespuesta` agrega campos opcionales
  `importe_vigente`, `importe_reembolsado`, `ajustes_pendientes`.
- **CU35**: `PagoHistorialItem` agrega `importe_vigente` e `importe_reembolsado`.
- Comprobantes: se conserva el original; no se crea PDF/outbox de nota de ajuste
  (D6 pendiente) ni se deshacen notificaciones enviadas.

## 10. Migración

Artefactos:
- `database/tenant_migrations/versions/20261116000000_cu36_ajustes_pago.sql`
- `database/tenant_migrations/versions/20261117000000_cu36_webhooks_huerfanos.sql` (E-3)
- `database/saas_control/008_create_stripe_webhook_huerfano.sql` (I-2, Control Plane)
- `database/tenant_migrations/versions/20261024150000_cu26_cu27_cu29.sql` (externo CU26;
  normalizado a LF para que su SHA-256 coincida con `manifest.json`, I-7)

Versión destino: `v6-sprint4-cu36`. Fuentes permitidas:
`v3-sprint3-cu26-cu27-cu29`, `v4-sprint3-cu32-cu33`, `v5-sprint4-cu36`.

- `manifest.json` actualizado con versiones y SHA-256; `tenant_migrator.py`
  incluye ambas versiones en `EXPECTED_VERSIONS` y la línea base de v5.
- `stripe_webhook_huerfano`: payload textual del evento, unicidad por
  `stripe_event_id`, checks de importe/moneda/estado, FK `ajuste_id` RESTRICT,
  índices por `refund_id`/`payment_intent_id` y parcial de pendientes.
- **RLS desactivado** en todas las tablas CU36 por instrucción del propietario;
  preflight/verify fallan si alguien lo habilita.
- FK `ON DELETE RESTRICT`, checks de importe/signo/efecto, índices de idempotencia
  y referencia externa.
- **I-1:** `restore_runner._expected_ledger` deriva el ledger exacto de
  `BASELINE_LEDGER_VERSIONS` + migración externa. v2-cu31, v3, v4, v5 y v6 tienen
  ledgers distintos y verificables; un backup mal etiquetado no se restaura.
- **I-4:** `provision_tenant.SCHEMA_VERSION` se lee del manifiesto y
  `prepare_schema_baseline` aplica las migraciones CU36 faltantes al clon,
  registra el ledger exacto y ejecuta `999_verify_cu36.sql`. Ningún tenant se
  etiqueta `v6` sin tener físicamente `pago_ajuste`, `pago_ajuste_detalle` y
  `stripe_webhook_huerfano`. La reconciliación verifica el esquema en solo lectura
  antes de conservar la etiqueta.
- **No se ejecutó** en ninguna base compartida ni productiva; solo en PostgreSQL
  17 aislado.

Procedimiento (cuando se autorice):

```powershell
python scripts/saas/migrate_active_tenants.py --plan
# revisar plan_hash
$env:CU31_TENANT_MIGRATIONS_ALLOW=1
python scripts/saas/migrate_active_tenants.py --execute --confirm-plan <hash>
```

La variable debe coincidir exactamente con `ALLOW_ENV` de
`scripts/saas/migrate_active_tenants.py` (`CU31_TENANT_MIGRATIONS_ALLOW`).

## 11. Validación

Suite aislada (SQLite en memoria, sin red ni base compartida):

```
python -m pytest tests/test_cu36_anulacion_ajustes.py -q
python -m pytest tests/test_cu36_e3_e8_correcciones.py -q
python -m pytest tests/test_cu36_i1_ledger_restore.py -q
python -m pytest tests/test_cu36_i2_i7_correcciones.py -q
python -m pytest -q
```

Resultado de esta corrección (auditoría I-1..I-7): suite completa
**2135 passed / 9 skipped**, con los 2 fallos preexistentes de
`CU26_Backend_dev_josias/tests/test_cu26_backend.py` (401 "Not authenticated",
ya reproducidos en el commit base `393f4e12`; no los introduce CU36).

Validación PostgreSQL descartable (contenedor `si2-cu36-pg-audit`, PostgreSQL
17.11, base `si2_cu36_test` conservada intacta; bases `si2_cu36_i_*` creadas y
eliminadas en la misma corrida): **55/55 verificaciones**.

- **I-1 ledgers exactos**: v2-cu31 (3), v3 (4), v4 (5), v5 (6) y v6 (7 entradas)
  coinciden solo con su propio ledger; cualquier versión cruzada, entrada
  faltante o checksum alterado se rechaza. El `rollback` de ledger (reinsertar
  la versión) restaura la verificación.
- **Backup/restore/rollback real**: `TenantBackupRunner.dump` (public +
  app_migrations) + `restore_exact` sobre `si2_cu36_i_restore` verifica v5;
  borrar una fila del ledger se detecta; un segundo restore devuelve v5 exacto;
  un backup v5 no se acepta como v6.
- **I-4 provisionamiento**: clon sin `pago_ajuste`/`stripe_webhook_huerfano` →
  `prepare_schema_baseline` aplica ambas migraciones CU36, registra el ledger v6
  completo, pasa `999_verify_cu36.sql` y `verify_current_schema` confirma los
  tres objetos.
- **I-2 Control Plane**: `008_create_stripe_webhook_huerfano.sql` aplica sobre
  `001`; columnas, unicidad por evento, RLS deshabilitado y atribución manual
  persistida; una atribución sin empresa es rechazada por constraint.
- **I-7**: el SQL externo CU26 no contiene CR y su SHA-256 coincide con el
  manifiesto.
- **I-1 (columna real):** `verify_migration_ledger` lee `checksum_sha256`, la
  columna real de `bootstrap_ledger.sql` y del migrador; antes consultaba
  `checksum`, lo que hacía fallar la verificación contra una base real.

### Matriz 7/7 (preparada, NO ejecutada)

No hubo acceso a Supabase ni a las siete bases productivas. La matriz queda
preparada para ejecutarse con autorización separada; no se declara 7/7.

| Empresa | Base | `version_schema` origen | Ledger v4 completo | Permisos `Gestionar pagos` | Migración CU36 | Validación CU36 |
|---|---|---|---|---|---|---|
| Visión Clínica | `tenant_vision_clara` | por verificar | por verificar | por verificar | preparada | pendiente |
| Instituto Visión | `tenant_instituto_vision` | por verificar | por verificar | por verificar | preparada | pendiente |
| Médico Ocular | `tenant_medico_ocular` | por verificar | por verificar | por verificar | preparada | pendiente |
| Oftalmo Norte | `tenant_oftalmo_norte` | por verificar | por verificar | por verificar | preparada | pendiente |
| OftalmoCare | `tenant_oftalmocare` | por verificar | por verificar | por verificar | preparada | pendiente |
| Vista Sur | `tenant_vista_sur` | por verificar | por verificar | por verificar | preparada | pendiente |
| Visual Oriental | `tenant_visual_oriental` | por verificar | por verificar | por verificar | preparada | pendiente |

Consulta de solo lectura para completar la matriz (ejecutar por tenant, nunca en
esta tarea):

```sql
SELECT current_database(),
       (SELECT version FROM app_migrations.schema_migration
         ORDER BY applied_at DESC LIMIT 1) AS ultima_version,
       to_regclass('public.pago_ajuste') IS NOT NULL AS cu36_instalado,
       count(*) FILTER (WHERE f.nombre IN ('Gestionar pagos')) AS permisos
  FROM public.funcion f
 WHERE f.estado IS TRUE;
```

Criterio para declarar 7/7: migración aplicada en clon/entorno aislado, objetos y
constraints verificados, permisos presentes y suite CU36 ejecutada con datos
sintéticos en cada empresa. Nada de eso se hizo aquí.

## 12. Límites conocidos / decisiones pendientes

- **D4**: reembolso parcial de cuota bloqueado con respuesta de negocio segura.
- **D5**: no se crean permisos nuevos; se reutiliza `Gestionar pagos` + rol.
- **D6**: no se genera nota PDF/outbox de ajuste.
- Corrección de importe sobre pagos aplicados a cuota: bloqueada.
- El ajuste `RECONCILIACION` requiere cierre manual por un administrador.
- **E-3 / I-2**: un refund huérfano requiere que un administrador lo atribuya
  explícitamente (`refund_id`, evidencia y distribución por servicio); nunca se
  contabiliza automáticamente. Si el PaymentIntent no prueba el tenant, el evento
  queda en la cuarentena central y un SUPERADMIN debe indicar la empresa antes de
  que el tenant lo concilie. No se crea nota PDF ni comprobante nuevo.
- **E-8**: las correcciones positivas sobre servicios sin `precio_aplicado` quedan
  bloqueadas con 409 hasta que exista un precio histórico válido.
