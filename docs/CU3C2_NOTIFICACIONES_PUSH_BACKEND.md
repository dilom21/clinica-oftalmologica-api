# Fase 3C.2 — Notificaciones push: diseño y persistencia

Documento local de la Fase 3C.2. **No** registra endpoints, no envía FCM y no
aplica migraciones. Su alcance es exclusivamente el diseño y la persistencia.

## 1. Estado de la fase

- Rama de trabajo: `dev-josias`.
- HEAD de referencia: `393f4e12a83b64a484cb24a81e2a1437c364bccd`.
- Módulo nuevo: `app/modules/notificaciones_push/`.
- No se modificó `app/main.py` ni se agregó router alguno.
- No se modificaron pagos, login, RBAC ni los servicios de citas.
- No se conectó a Supabase/PostgreSQL, no se ejecutó SQL y no se llamó a
  Firebase/FCM/APNs.

## 2. Persistencia tenant

Base física por empresa. Los IDs de usuario son locales al tenant: **no** se
agrega `tenant_id` ni `empresa_id` a estas tablas.

| Tabla | Propósito | Unicidad clave |
| --- | --- | --- |
| `dispositivo_push` | Dispositivo del usuario y token FCM operativo | Índice único parcial del token activo |
| `notificacion` | Bandeja por usuario | `evento_clave` único |
| `notificacion_entrega` | Outbox por dispositivo | `(notificacion_id, dispositivo_id)` único |
| `preferencia_notificacion` | Una fila por usuario | `usuario_id` único |
| `cita_evento` | Eventos estructurados de cita | `clave_idempotencia` única |

### 2.1 `dispositivo_push`

- `token_fcm` es un secreto operativo: se guarda solo aquí, nunca se loguea, no
  aparece en `repr` ni en los schemas de salida.
- `plataforma` restringida a `ANDROID`, `IOS`, `WEB`.
- Revocación lógica (`activo = false`, `revocado_en`), sin borrado físico.
- Índice único parcial `uq_dispositivo_push_token_activo` sobre `token_fcm`
  `WHERE activo` y el índice `ix_dispositivo_push_usuario_activo`.
- El límite de dispositivos por usuario se validará en la futura capa de
  servicio, no con un constraint incapaz de garantizar concurrencia.

### 2.2 `notificacion`

- Tipos: `CITA_CREADA`, `CITA_CONFIRMADA`, `CITA_REPROGRAMADA`,
  `CITA_CANCELADA`, `RECORDATORIO_CITA`, `ACTUALIZACION_OPERATIVA`.
- `datos` es JSON mínimo de navegación. El schema de persistencia rechaza
  claves clínicas conocidas (`diagnostico`, `motivo`, `observaciones`,
  `anamnesis`, `receta`, etc.).
- `ck_notificacion_lectura_coherente`: `leida` y `fecha_lectura` deben ser
  coherentes.
- Índices de bandeja por usuario + fecha descendente y usuario + lectura.
- La bandeja no depende de dispositivos ni de push habilitado.

### 2.3 `notificacion_entrega`

- Estados: `PENDIENTE`, `PROCESANDO`, `ACEPTADO_POR_FCM`, `FALLIDO`,
  `INVALIDO`. Proveedor restringido a `FCM`.
- `ACEPTADO_POR_FCM` significa que FCM aceptó la solicitud; no implica entrega
  ni lectura.
- Unicidad por notificación + dispositivo; índice de cola
  `ix_notificacion_entrega_cola (estado, proximo_intento)`.
- Unicidad parcial de `proveedor_mensaje_id` cuando no es NULL.
- No se implementan aún reintentos, leases ni `FOR UPDATE SKIP LOCKED`: eso
  pertenece a 3C.5/3C.6.

### 2.4 `preferencia_notificacion`

- Una fila por usuario con `push_habilitado`, `citas_creadas`,
  `citas_confirmadas`, `citas_reprogramadas`, `citas_canceladas` y
  `recordatorios`, todos `true` por defecto.
- Desactivar push no borra ni oculta notificaciones de la bandeja.

### 2.5 `cita_evento`

- Tipos: `CITA_CREADA`, `CITA_ESTADO_CAMBIADO`, `CITA_REPROGRAMADA`.
  `REPROGRAMADA` es un tipo de evento, nunca un estado de cita.
- Cambio de estado exige `estado_anterior` y `estado_nuevo` distintos y
  pertenecientes al enum real (`PROGRAMADA`, `CONFIRMADA`, `EN_ESPERA`,
  `ATENDIDA`, `CANCELADA`, `NO_ASISTIO`).
- Reprogramación exige fecha/hora anterior y nueva, y que al menos una cambie.
- Creación no admite estado ni agenda anterior.
- No contiene paciente, motivo, observaciones, diagnóstico ni texto clínico.
- La instrumentación de `registrar_cita`, `reprogramar_cita` y
  `cambiar_estado_cita` queda para 3C.4.

### 2.6 Política de claves foráneas y borrado

| FK | Acción | Justificación |
| --- | --- | --- |
| `dispositivo_push.usuario_id` | `RESTRICT` | No perder el secreto operativo ni la trazabilidad por un borrado accidental; preferir baja lógica del usuario. |
| `notificacion.usuario_id` | `RESTRICT` | Conservar la evidencia de notificaciones emitidas. |
| `notificacion.cita_id` | `SET NULL` | La notificación ya emitida sobrevive si la cita se retira administrativamente; solo pierde el enlace. |
| `notificacion_entrega.notificacion_id` | `RESTRICT` | Evidencia del outbox; nunca borrado en cascada. |
| `notificacion_entrega.dispositivo_id` | `RESTRICT` | El dispositivo se revoca lógicamente; su historial permanece. |
| `preferencia_notificacion.usuario_id` | `RESTRICT` | Evitar huérfanos de configuración. |
| `cita_evento.cita_id` | `RESTRICT` | Evidencia de auditoría de eventos. |
| `cita_evento.actor_usuario_id` | `SET NULL` | El evento debe sobrevivir aunque el actor sea dado de baja. |

No existe ninguna FK entre bases físicas distintas.

## 3. Registro global del Control Plane

Tabla `saas_control.dispositivo_push_registry`:

- `token_fingerprint`: SHA-256 hexadecimal (64 caracteres) del token FCM,
  único globalmente. **Nunca** se guarda el token en claro.
- `tenant_database_id`: FK real a `saas_control.tenant_database`, con
  `ON DELETE RESTRICT`. No existe FK hacia `usuario` del tenant.
- `usuario_id`: identificador local; solo tiene sentido junto con
  `tenant_database_id`.
- `estado`: `PENDIENTE`, `ACTIVO`, `REVOCADO`, con coherencia de
  `fecha_revocacion`.
- `huella_version`: versión del algoritmo (1 = SHA-256).
- Sin correo, nombre, paciente, datos clínicos ni contenido de notificaciones.
- `REVOKE ALL` a `PUBLIC`, `anon` y `authenticated`, siguiendo el patrón real
  del Control Plane.

La huella se calcula con `notificaciones_push.seguridad.calcular_huella_token`
y no debe exponerse en HTTP ni en logs.

### 3.1 Reasignación pendiente (no implementada)

Dos bases PostgreSQL independientes no participan en una transacción ACID
común. La futura reasignación de un token entre empresas deberá implementarse
como flujo recuperable: claim/reserva en Control Plane, actualización en la
base tenant y confirmación o compensación (saga). No se implementó ningún
servicio ni endpoint en 3C.2.

## 4. Migraciones y checksums

### 4.1 Tenant

- Migración: `database/tenant_migrations/versions/20261121000000_cu3c2_notificaciones_push.sql`
  - SHA-256: `4e0cb1de5fa9b6b13e5f22b8d3da372fbdeac2d5c2b58d7aff5efc90d22556d2`
- Preflight: `database/tenant_migrations/000_preflight_cu3c2.sql`
  - SHA-256: `ddb410c9fbd8864d77439af881dc1e8c076201167d1abd070ef536a1dfe46661`
- Verificación: `database/tenant_migrations/999_verify_cu3c2.sql`
  - SHA-256: `75ff2bb73d0ad9eb42cfcf72ddd23fb521f6fe502d7cc4853c2d73a9cbc6c9bb`
- Manifest actualizado: `schema_version` = `v7-sprint5-notificaciones-push`,
  `allowed_source_schema_versions` incluye `v6-sprint4-cu36`, y la migración se
  registra con `transaction: runner`.
- `tenant_migrator.py`: `EXPECTED_VERSIONS` y `BASELINE_LEDGER_VERSIONS`
  incorporan la nueva versión y la línea base de v6. El preflight es de solo
  lectura y el verify también; ambos terminan en `ROLLBACK`.
- El preflight exige tablas presentes en todas las versiones soportadas y falla
  si las tablas nuevas ya existen (instalación parcial).

### 4.2 Control Plane

- Archivo: `database/saas_control/009_create_dispositivo_push_registry.sql`
  - SHA-256: `1947afa5537378d3462221f1eaf17fe8fc67f87a81438ea5ee3e30a98ef64d9e`
- El Control Plane no usa el runner/manifest tenant; sigue el patrón numérico
  manual `001..008`, por lo que no se inventó un segundo mecanismo. Su
  `postflight` verifica tabla, unicidad global y ausencia de columna de token.

**Ninguna migración fue ejecutada.** No se abrió conexión a PostgreSQL,
Supabase, legacy, tenants ni producción.

## 5. Repositorios y schemas

Repositorios (sin `commit`, sin abrir conexiones, sin resolver tenant):

- `repositories/dispositivos.py`
- `repositories/notificaciones.py`
- `repositories/entregas.py`
- `repositories/preferencias.py`
- `repositories/eventos_cita.py` (registro idempotente por clave)
- `repositories/registro_global.py` (Control Plane, solo huellas)

Schemas (`extra="forbid"`, `from_attributes=True`): DTO de persistencia, no
contratos HTTP. `DispositivoPushVista` no contiene `token_fcm`;
`DispositivoPushPersistencia.token_fcm` usa `repr=False`; los schemas de
notificación rechazan claves clínicas y los de evento validan la coherencia de
creación, cambio de estado y reprogramación.

## 6. Pruebas ejecutadas

- `python -m compileall app/modules/notificaciones_push tests/test_notificaciones_push_persistence.py` — OK.
- `pytest tests/test_notificaciones_push_persistence.py` — 43 aprobadas.
- Regresión focalizada: `test_cu31_tenant_migrator.py`,
  `test_cu36_i1_ledger_restore.py`, `test_cu36_i2_i7_correcciones.py`,
  `test_cu26_cu27_rbac.py`, `test_saas_restore.py` junto con las nuevas —
  281 aprobadas.
- `test_cu36_e3_e8_correcciones.py` y `test_cu36_anulacion_ajustes.py` —
  44 aprobadas.
- `import app.main` y generación de OpenAPI — OK (152 rutas, sin rutas nuevas).
- `git diff --check` — sin errores.

Las pruebas usan SQLite en memoria (incluido un `saas_control` adjunto) y no
tocan red, bases reales ni servicios externos.

## 7. Fuera de alcance (pendiente)

- Endpoints `/notificaciones/*` y registro HTTP de dispositivos.
- Login tenant móvil y cambios en autenticación.
- Instrumentación de servicios de citas (3C.4).
- Worker de push, FCM HTTP v1, OAuth de cuenta de servicio y reintentos
  (3C.5/3C.6).
- Recordatorios/scheduler, correo y panel administrativo.
- Reasignación recuperable de tokens entre empresas.
- Aplicación real de las migraciones y validación contra PostgreSQL.
