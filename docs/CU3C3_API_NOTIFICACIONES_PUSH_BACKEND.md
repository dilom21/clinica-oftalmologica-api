# CU 3C.3 — API de dispositivos, preferencias y bandeja de notificaciones push

Implementación local (21-11-2026) del backend de notificaciones push para la
aplicación móvil de pacientes. **No se aplicaron migraciones, no se abrieron
conexiones reales y no se llamó a FCM/APNs.**

## 1. Puerta de autenticación tenant (hallazgo)

La auditoría 3C.1 se confirma en el código actual:

- `POST /seguridad/login/paciente` usa `get_db_legacy`
  (`app/modules/gestion_usuarios_seguridad/api/router.py`, endpoint
  `iniciar_sesion_paciente`) y emite un JWT legacy mediante
  `crear_access_token` (`app/modules/gestion_usuarios_seguridad/services/auth_service.py`).
  Ese token **no** contiene `token_type`, `tenant_id`, `empresa_id` ni
  `empresa_codigo`.
- Sí existe un login tenant real: `POST /seguridad/tenant/login`, que resuelve
  la empresa en el Control Plane, abre la base tenant y emite un JWT con
  `token_type="tenant"` y claims de empresa/tenant
  (`app/modules/gestion_usuarios_seguridad/services/tenant_auth_service.py`,
  `app/core/security.py::crear_tenant_access_token`).
- `app/database/session.py::get_db` conserva el comportamiento histórico:
  token ausente o legacy → base legacy; token tenant válido → base de la
  empresa revalidada; token SaaS → rechazado.

**Decisión:** los endpoints push exigen un JWT **tenant** válido y el rol
`Paciente`. Un token legacy se rechaza antes de abrir sesión y **no hay
fallback** a legacy. Por tanto:

- la API queda habilitada para pacientes autenticados por el login tenant;
- la app Flutter actual, que usa `/seguridad/login/paciente`, **no puede**
  consumirla todavía. Migrar el login móvil al flujo tenant requiere una fase
  separada y autorización expresa;
- no se modificó login, JWT, `get_db`, `TenantResolver` ni RBAC.

## 2. Endpoints

| Método | Ruta | Descripción |
| --- | --- | --- |
| POST | `/notificaciones/dispositivos` | Registra/actualiza el dispositivo FCM propio (idempotente). |
| DELETE | `/notificaciones/dispositivos/{dispositivo_id}` | Revoca lógicamente un dispositivo propio (idempotente). |
| GET | `/notificaciones` | Bandeja propia paginada (`page`, `page_size` ≤ 50). |
| PATCH | `/notificaciones/{notificacion_id}/leida` | Marca una notificación propia como leída (idempotente). |
| GET | `/notificaciones/preferencias` | Preferencias propias; defaults sin escritura. |
| PUT | `/notificaciones/preferencias` | Actualiza solo campos permitidos; booleanos estrictos. |

No se creó ningún endpoint de emisión, envío o administración.

## 3. Seguridad aplicada

- Identidad y tenant derivados del JWT validado y de la sesión resuelta por el
  backend (`db.info["tenant_context"]`); nunca de body, query o headers.
- Rol `Paciente` obligatorio; se revalida `rol_id` contra la base tenant.
- El registro de dispositivo rechaza `usuario_id`, `paciente_id`, `tenant_id`,
  `empresa_id`, `empresa_codigo`, `database_name`, DSN, huella y `activo`
  mediante `extra="forbid"`.
- El token FCM se valida en el backend (20–1024 caracteres), se calcula su
  huella SHA-256 en el servidor y nunca aparece en respuestas, `repr` ni logs.
- Recursos ajenos responden `404` con el mismo mensaje que un recurso
  inexistente.
- `datos` de la bandeja se filtra por allowlist (`ruta`, `pantalla`, `seccion`,
  `accion`, `cita_id`) y solo admite valores escalares.

## 4. Protocolo global de propiedad del token

Tabla `saas_control.dispositivo_push_registry` (huella SHA-256 únicamente):

1. Reserva `PENDIENTE` en Control Plane y `commit` de esa reserva.
2. Persistencia/actualización del `dispositivo_push` en la base tenant y
   `commit`.
3. Confirmación `ACTIVO` en Control Plane.

Reglas:

- huella `ACTIVO` o `PENDIENTE` de otro tenant/usuario → `409`; no se roba ni
  reasigna automáticamente;
- si falla el paso 2, se revoca **solo** la reserva creada por esa operación y
  se conserva cualquier propiedad previa;
- si falla el paso 3, el dispositivo queda persistido y la huella `PENDIENTE`:
  un reintento idempotente del mismo usuario la confirma; una reserva
  abandonada queda retenida (fail-closed) hasta reconciliación manual;
- la revocación local se confirma primero y la global después, solo si tenant,
  usuario y huella coinciden. Un fallo global deja la huella retenida y un
  reintento completa la liberación;
- no se implementó worker de reconciliación en esta fase.

## 5. Pruebas

`tests/test_notificaciones_push_api.py`: **57 passed** con SQLite en memoria,
esquema `saas_control` adjunto y JWT firmados reales. Cubre puerta tenant,
tokens legacy/SaaS/manipulados/cruzados, usuario inactivo, rol incorrecto,
aislamiento entre bases con IDs locales iguales, registro idempotente, campos
prohibidos, conflictos de propiedad, compensación, fallo de confirmación y
recuperación, revocación propia/ajena, bandeja, paginación, filtrado de
`datos`, lectura idempotente, preferencias, OpenAPI y ausencia del token en
respuestas y logs.

Regresiones focalizadas: `test_notificaciones_push_persistence.py`,
`test_auth_tenant.py`, `test_clinical_request_routing.py`,
`test_tenant_company_selector.py`, `test_tenant_connection_router.py`,
`test_tenant_patient_registration.py`, `test_tenant_token_expiration.py` y
`test_cu31_tenant_migrator.py`: **265 passed**. Total con la suite nueva:
**322 passed**.

## 6. Límites y pendientes

- Las migraciones 3C.2 (tenant y Control Plane) siguen **sin aplicar**; el
  despliegue exige autorización independiente.
- SQLite no reproduce concurrencia real de PostgreSQL: el contrato de carreras
  se simuló con constraints, precarga de estados y mocks; no se afirma haber
  probado concurrencia productiva.
- La app Flutter debe migrar al login tenant para usar estos endpoints.
- No hay envío FCM, worker, recordatorios, scheduler ni creación automática de
  notificaciones (fases posteriores).
- El límite de dispositivos por usuario, la política de rate limiting y la
  reconciliación automática de reservas `PENDIENTE` quedan pendientes de
  definición y autorización.
- Un token FCM compartido entre cuentas del mismo dispositivo debe resolverse
  con un flujo explícito de transferencia; hoy se rechaza de forma segura.
