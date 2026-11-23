# CU3C.5 — Worker de notificaciones push y FCM HTTP v1 (backend)

Fase 3C.5 del backend SI2 (Clínica Oftalmológica). Rama `dev-josias`.
Esta fase **no** aplica migraciones, **no** llama a Firebase/FCM, **no** crea
endpoints, **no** instala dependencias y **no** modifica Angular/Flutter.

## 1. Alcance implementado

| Elemento | Ubicación |
|---|---|
| Fanout de notificación in-app a entregas por dispositivo | `app/modules/notificaciones_push/services/entregas.py` |
| Integración transaccional con eventos de cita 3C.4 | `app/modules/notificaciones_push/services/eventos_cita.py` |
| Claim/lease y actualización condicionada | `app/modules/notificaciones_push/repositories/entregas.py` |
| Worker tenant-scoped | `app/modules/notificaciones_push/services/worker_fcm.py` |
| Punto de entrada invocable | `app/modules/notificaciones_push/worker.py` |
| Adaptador FCM HTTP v1 | `app/modules/notificaciones_push/providers/fcm.py` |
| Migración aditiva preparada (no aplicada) | `database/tenant_migrations/versions/20261122000000_cu3c5_worker_fcm_push.sql` |
| Preflight / verificación de la fase | `database/tenant_migrations/000_preflight_cu3c5.sql`, `999_verify_cu3c5.sql` |
| Pruebas aisladas | `tests/test_cu3c5_worker_fcm_push.py` |

## 2. Fanout (outbox)

`encolar_entregas(db, notificacion)`:

1. Deriva el destinatario de `notificacion.usuario_id` (resuelto en 3C.4 desde
   la cita y el paciente; nunca desde el request).
2. Lee la preferencia real del usuario (`preferencia_notificacion`). Sin fila se
   aplican los valores por defecto del modelo (`TRUE` en todos los canales).
3. Respeta la preferencia por tipo de notificación:
   `CITA_CREADA → citas_creadas`, `CITA_CONFIRMADA → citas_confirmadas`,
   `CITA_REPROGRAMADA → citas_reprogramadas`,
   `CITA_CANCELADA → citas_canceladas`, `RECORDATORIO_CITA → recordatorios`.
   `ACTUALIZACION_OPERATIVA` no tiene columna específica: depende solo de
   `push_habilitado` (contrato real del esquema, documentado).
4. Crea una entrega `PENDIENTE` por dispositivo **activo** del mismo usuario en
   la base tenant actual.
5. Es idempotente: relee las entregas existentes y la unicidad
   `(notificacion_id, dispositivo_id)` cierra carreras con savepoints.
6. No hace `commit`: la transacción es la del caso de uso de citas. Un fallo
   inesperado se propaga y revierte evento, notificación y entregas.
7. Si push está desactivado o no hay dispositivos, la notificación in-app se
   conserva sin entregas.

## 3. Worker

`procesar_entregas(db, *, proveedor, configuracion=None, reloj=None)`:

- Recibe **una** sesión tenant ya resuelta; no abre conexiones, no descubre
  bases, no enumera tenants y no acepta `tenant_id`/`empresa_id`/DSN.
- `ConfiguracionWorker`: lote (1-100), lease (5-3600 s), máximo de intentos
  (1-20), backoff base/máximo y timeout del proveedor (siempre menor que el
  lease).
- Reclama el lote, hace `commit` del claim y **después** llama al proveedor.
- Revalida antes de enviar: notificación no cancelada, dispositivo activo y del
  mismo usuario, y preferencia push vigente. Si algo dejó de ser elegible, la
  entrega pasa a `OMITIDO` (la bandeja no se afecta).
- Persiste cada resultado en su propia transacción condicionada al
  `lease_token`; un worker con lease vencido no puede sobrescribir a otro.

### Claim y leases

- `PENDIENTE` con `proximo_intento` nulo o vencido, o `PROCESANDO` con
  `bloqueado_hasta` vencido.
- PostgreSQL: `SELECT ... FOR UPDATE SKIP LOCKED` en transacción corta; la
  actualización vuelve a exigir la condición de reclamo.
- `lease_token` (nuevo, VARCHAR(64)) es el fencing token del claim;
  `bloqueado_hasta` es su expiración.
- SQLite **no** valida concurrencia real: la compilación del SQL de PostgreSQL
  se verifica estáticamente y la prueba de concurrencia real queda pendiente en
  un entorno PostgreSQL aislado autorizado.

## 4. FCM HTTP v1 y credenciales

- `FcmHttpV1` usa `httpx` (dependencia ya declarada) contra
  `https://fcm.googleapis.com/v1/projects/{proyecto}/messages:send`.
- El access token OAuth 2.0 proviene de un `ProveedorAccessToken` **inyectado**.
  No se leen `.env`, service-account JSON, DSN ni variables secretas.
- Sin proyecto/credenciales el adaptador queda `NO_CONFIGURADO`: el worker no
  reclama nada, no consume intentos y nunca simula una aceptación.
  `obtener_proveedor_fcm()` devuelve explícitamente ese estado.
- `provider_message_id` se guarda solo si FCM devolvió un `name` válido.
- Payload: token del dispositivo, título/cuerpo genéricos y `data` con **solo**
  la allowlist de 3C.3 (`ruta`, `pantalla`, `seccion`, `accion`, `cita_id`) y
  valores string. Sin PII, texto clínico, tenant, huellas ni credenciales.
- No se registran tokens, access tokens, cabeceras `Authorization` ni cuerpos
  crudos de error.

## 5. Clasificación de resultados y reintentos

| Resultado del proveedor | Entrega |
|---|---|
| 2xx con `name` válido | `ACEPTADO_POR_FCM` + `proveedor_mensaje_id` + `fecha_aceptado` |
| 404/400 con `UNREGISTERED` / `INVALID_REGISTRATION` / `SENDER_ID_MISMATCH` | `INVALIDO` y dispositivo local desactivado |
| 429 | Reintento; `proximo_intento = ahora + max(backoff, Retry-After)` acotado |
| 5xx, timeout, error de red | Reintento con backoff exponencial acotado |
| 401/403 | Error global de configuración: no invalida tokens, no consume intentos; se libera el resto del lote |
| 400/404 sin código estructurado | `FALLIDO` terminal (`PAYLOAD_INVALIDO` / `DESTINO_NO_ENCONTRADO`) |
| Máximo de intentos alcanzado | `FALLIDO` terminal (`MAXIMO_INTENTOS_ALCANZADO`) |

Backoff: `base * 2^(intentos-1)` con tope configurable. Los códigos se
sanitizan a `[A-Z0-9_]` y `codigo_ultimo_error` está limitado a 100 caracteres.

### Tokens inválidos y Control Plane

Solo se desactiva el dispositivo **local** (`activo = false`, `revocado_en`).
La liberación de la huella SHA-256 en `saas_control.dispositivo_push_registry`
requiere una sesión de Control Plane que el worker no abre: queda como residuo
documentado (la huella permanece `ACTIVO`, fail-closed, hasta reconciliación
por el flujo administrativo existente).

## 6. Semántica de entrega

**at-least-once**, no exactly-once. Si FCM acepta el mensaje y el proceso cae
antes de persistir `proveedor_mensaje_id`, el reintento puede duplicar el
envío. Los leases y la unicidad reducen duplicados; no se promete entrega
única ni se marca aceptación antes de la respuesta válida del proveedor.

## 7. Esquema: migración aditiva preparada (NO aplicada)

`20261122000000_cu3c5_worker_fcm_push.sql` (sha256
`d769152308bed9a3086adae694e23edf859295e20828fc2478025d16db7897a9`):

- `ALTER TABLE public.notificacion_entrega ADD COLUMN lease_token VARCHAR(64)`.
- Reemplaza `ck_notificacion_entrega_estado` para admitir `OMITIDO` (estado
  terminal de entrega descartada sin enviar).
- Índice parcial `ix_notificacion_entrega_lease` sobre `bloqueado_hasta`
  cuando `estado = 'PROCESANDO'`.
- No edita ni reemplaza la migración 3C.2; no toca datos.

Manifiesto: `schema_version = v8-sprint5-worker-fcm-push`,
`allowed_source_schema_versions` incluye `v7-sprint5-notificaciones-push`,
preflight `000_preflight_cu3c5.sql`, verificación `999_verify_cu3c5.sql`.
`tenant_migrator.EXPECTED_VERSIONS` y `BASELINE_LEDGER_VERSIONS` actualizados.
Ninguna migración fue aplicada a base alguna.

## 8. Pruebas

`tests/test_cu3c5_worker_fcm_push.py` (39 pruebas): fanout multi-dispositivo,
exclusión de inactivos/ajenos, preferencias y defaults, preferencia revocada
después del encolado, idempotencia, rollback de fanout, propagación de fallos,
límite de lote, `proximo_intento`, lease activo/expirado, fencing de worker
antiguo, aceptación y `provider_message_id`, `UNREGISTERED`, 429/Retry-After,
5xx/backoff, timeout, 401/403 sin invalidar tokens, payload inválido, máximo de
intentos, sanitización de códigos, privacidad del payload y de los logs,
aislamiento entre tenants con IDs locales iguales, provider no configurado y
compilación `FOR UPDATE SKIP LOCKED`.

## 9. Pendientes / limitaciones

- Orquestación/scheduler del worker: no implementado (el punto de entrada es
  `ejecutar_worker_tenant`).
- Validación real de concurrencia PostgreSQL (`SKIP LOCKED`): pendiente.
- Envío FCM real: requiere configurar `proyecto_id` y un `ProveedorAccessToken`
  autorizado en el despliegue.
- Liberación global de huellas de tokens invalidados: pendiente de una fase
  con acceso autorizado a Control Plane.
- Integración Flutter/Angular: fuera de alcance.
