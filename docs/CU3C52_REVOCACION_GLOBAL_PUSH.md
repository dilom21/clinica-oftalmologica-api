# CU3C.5.2 — Revocación global segura de dispositivos Push invalidados por FCM

Fase 3C.5.2 del backend SI2 (Clínica Oftalmológica). Rama `dev-josias`.
Repositorio exclusivo: `C:\SI2_Proyecto\clinica-oftalmologica-api`.

Esta fase **no** configura FCM real, **no** crea scheduler, **no** conecta
tenants reales ni el Control Plane real, **no** aplica migraciones en
infraestructura real, **no** lee `.env`/secretos y **no** ejecuta operaciones
Git de escritura.

---

## 1. Estado Git

| Dato | Valor |
|---|---|
| Ruta | `C:\SI2_Proyecto\clinica-oftalmologica-api` |
| Rama | `dev-josias` |
| HEAD inicial y final | `393f4e12a83b64a484cb24a81e2a1437c364bccd` |
| Worktree | raíz del repositorio, no un worktree adicional |
| Staged inicial/final | 0 / 0 |
| Modificados (status) inicial/final | 60 / 60 |
| Archivos untracked inicial/final | 103 / 108 |
| Entradas de status untracked inicial/final | 67 / 70 |

El incremento de untracked corresponde a los artefactos nuevos de esta fase:
`repositories/revocaciones.py`, `services/revocaciones.py`, la migración
`20261123000000_cu3c52_revocacion_global_push.sql`, el informe
`docs/CU3C52_REVOCACION_GLOBAL_PUSH.md` y la suite
`tests/test_cu3c52_revocacion_global_push.py` (los dos primeros viven en
directorios untracked ya colapsados por `git status`).

`git diff --name-only` enumera 59 archivos frente a 60 entradas ` M` de
`git status`: la diferencia es preexistente y corresponde a
`database/tenant_migrations/versions/20261024150000_cu26_cu27_cu29.sql`, que
`git status` marca por normalización CRLF sin diferencia de contenido. No fue
tocado por esta fase.

---

## 2. Esquema real inspeccionado antes de diseñar

| Elemento | Hallazgo |
|---|---|
| `app/modules/notificaciones_push/models/models.py` | `dispositivo_push`, `notificacion`, `notificacion_entrega`, `preferencia_notificacion`, `cita_evento` (tenant) y `DispositivoPushRegistry` (`saas_control`) |
| `repositories/registro_global.py` | Métodos existentes: `obtener_por_huella`, `crear_pendiente`, `marcar_activo`, `marcar_revocado`; ninguno acotaba la actualización por tenant+fingerprint en una sola sentencia |
| `database/saas_control/009_create_dispositivo_push_registry.sql` | `UNIQUE (token_fingerprint)` global, `tenant_database_id` FK `ON DELETE RESTRICT`, estados `PENDIENTE/ACTIVO/REVOCADO`, `fecha_revocacion`, `huella_version`, sin token en claro, `REVOKE` a `PUBLIC/anon/authenticated` |
| `repositories/entregas.py` + migración `20261122000000` | Outbox con `lease_token`, índice parcial `WHERE estado='PROCESANDO'`, `FOR UPDATE SKIP LOCKED` solo en PostgreSQL; estados hasta `OMITIDO` |
| `services/worker_fcm.py` | En `TOKEN_NO_REGISTRADO` solo revocaba el dispositivo local; la liberación global estaba pendiente |
| `seguridad.calcular_huella_token` | SHA-256 hex determinista, 64 caracteres, sin sal |
| `database/tenant_migrations/tenant_migrator.py` | `EXPECTED_VERSIONS` y `BASELINE_LEDGER_VERSIONS` codifican la secuencia exacta; el manifiesto declara `supporting_files` |

**No existía** almacenamiento durable de solicitudes de revocación ni un
proceso que actualizara el Control Plane. Se añadió una tabla tenant aditiva
(no se reutilizó `notificacion_entrega`, cuya semántica es de entrega push, ni
`notificacion`, que es visible en la bandeja del paciente).

### Semántica de la unicidad global

`token_fingerprint` sigue siendo **único global**. Revocar cambia `estado` a
`REVOCADO` y fija `fecha_revocacion`; **no** elimina la fila ni libera el
`UNIQUE`. Una reclamación posterior de la misma huella la encuentra `REVOCADO`
y debe pasar por el flujo explícito de reasignación (fuera de alcance). No se
debilita ni se elimina la restricción.

---

## 3. Flujo implementado y límites de confianza

### 3.1 Worker FCM (sesión tenant, sin Control Plane)

Al recibir `UNREGISTERED`, `INVALID_REGISTRATION` o `SENDER_ID_MISMATCH`, en
**una sola transacción tenant**:

1. `UPDATE notificacion_entrega` a `INVALIDO` condicionado por `lease_token`
   (fencing de 3C.5);
2. desactivación lógica del `dispositivo_push`;
3. inserción idempotente de la solicitud en `dispositivo_push_revocacion`.

Si el lease ya no es propio, no se revoca ni se solicita nada. Si falla la
persistencia de la solicitud, la excepción revierte entrega INVALIDO +
desactivación + solicitud; no queda una revocación local silenciosa. El worker
**no importa** `open_control_session` ni recibe sesiones del Control Plane.

### 3.2 Procesador (sesiones tenant y Control Plane ya resueltas)

`services/revocaciones.procesar_revocaciones(db, control, *,
tenant_database_id, ...)`:

1. valida que `tenant_database_id` sea entero positivo de contexto interno y
   que coincida con `db.info["tenant_context"].tenant_database_id` cuando el
   backend lo haya resuelto (fail-closed);
2. reclama un lote con `lease_token` y `bloqueado_hasta` y **confirma el
   claim** antes de tocar el Control Plane;
3. por cada solicitud ejecuta `UPDATE ... WHERE token_fingerprint = :huella AND
   tenant_database_id = :tenant AND estado IN ('PENDIENTE','ACTIVO')` y
   **confirma el Control Plane**;
4. sólo después marca la solicitud tenant y hace su `commit`.

El procesador no descubre tenants, no enumera `tenant_database`, no acepta
identificadores HTTP y no abre conexiones por sí mismo: recibe las sesiones.

### 3.3 Casos del registro global

| Situación | Resultado local | Efecto global |
|---|---|---|
| Pareja tenant+fingerprint en `PENDIENTE/ACTIVO` | `COMPLETADA` | `REVOCADO` + `fecha_revocacion` |
| Misma pareja ya `REVOCADO` | `COMPLETADA` (idempotente) | Sin cambios; conserva la fecha original |
| Huella inexistente | `NO_ENCONTRADO` | Ninguno; no se inserta ni se busca en otros tenants |
| Huella de otro `tenant_database_id` | `RECHAZADA` (`HUELLA_OTRO_TENANT`) | Fila ajena intacta |
| Estado global inesperado | `PENDIENTE` con backoff | Sin modificación |
| Error del Control Plane | `PENDIENTE` con backoff | Sin modificación |

`usuario_id` **nunca** se usa como identidad global: sólo acompaña a
`tenant_database_id` y no participa en el filtro de la actualización global (el
filtro es tenant + fingerprint).

---

## 4. Persistencia nueva (tenant, aditiva)

Tabla `public.dispositivo_push_revocacion`:

| Columna | Tipo | Nota |
|---|---|---|
| `id` | `BIGINT IDENTITY` PK | |
| `dispositivo_id` | `BIGINT NOT NULL` FK `dispositivo_push.id` `ON DELETE RESTRICT` | conserva evidencia |
| `usuario_id` | `BIGINT NOT NULL` FK `usuario.id` `ON DELETE RESTRICT` | ID local del tenant |
| `token_fingerprint` | `VARCHAR(64) NOT NULL` | SHA-256 hex; nunca el token |
| `estado` | `VARCHAR(20)` | `PENDIENTE/PROCESANDO/COMPLETADA/NO_ENCONTRADO/RECHAZADA/FALLIDA` |
| `intentos` | `SMALLINT NOT NULL DEFAULT 0` | `CHECK >= 0` |
| `proximo_intento`, `bloqueado_hasta` | `TIMESTAMPTZ` | cola y lease |
| `lease_token` | `VARCHAR(64)` | fencing |
| `codigo_ultimo_error` | `VARCHAR(100)` | código saneado |
| `fecha_creacion`, `fecha_actualizacion`, `fecha_procesado` | `TIMESTAMPTZ` | |

Restricciones e índices:

- `uq_dispositivo_push_revocacion_dispositivo_huella (dispositivo_id, token_fingerprint)` — idempotencia; una rotación de token genera una solicitud nueva;
- `ck_..._huella` — 64 caracteres y minúsculas;
- `ck_..._estado`, `ck_..._intentos`;
- `ix_..._cola (estado, proximo_intento)`;
- `ix_..._lease (bloqueado_hasta) WHERE estado='PROCESANDO'`;
- `ix_..._usuario (usuario_id, estado)`.

No hay `tenant_id` ni `empresa_id` (una base física por tenant). No hay FK
cross-database. Sin RLS, conforme a la instrucción vigente del propietario.

---

## 5. Idempotencia y recuperación entre bases

- La solicitud se crea una sola vez por `(dispositivo_id, token_fingerprint)`;
  un duplicado concurrente se resuelve con savepoint + relectura.
- El claim tenant se confirma antes de la red/Control Plane.
- El ACK local está condicionado al `lease_token` vigente (fencing).
- Caída **antes** del commit del Control Plane → solicitud `PENDIENTE` con
  backoff; el reintento repite la operación.
- Caída **después** del commit del Control Plane y antes del ACK → la solicitud
  queda `PROCESANDO` con lease; al vencer, el reintento encuentra la fila
  global `REVOCADO` y termina `COMPLETADA` sin cambiar la fecha original.
- Nunca se marca `COMPLETADA` antes del commit del Control Plane; ningún fallo
  se convierte en éxito ficticio.
- El reintento global **no** reenvía nada a FCM: sólo toca la solicitud y el
  Control Plane.
- No se promete exactly-once; el procesamiento es idempotente y repetible.

---

## 6. Clasificación FCM conservada

| Respuesta | Solicitud de revocación |
|---|---|
| `UNREGISTERED`, `INVALID_REGISTRATION`, `SENDER_ID_MISMATCH` | **Sí** |
| 400 genérico | No |
| 401/403 credenciales/proyecto | No |
| 429 | No |
| 5xx / timeout / red | No |
| Proveedor no configurado | No |

No se modificó la clasificación validada en 3C.5.

---

## 7. Migración, preflight y verify

| Artefacto | Estado |
|---|---|
| `database/tenant_migrations/versions/20261123000000_cu3c52_revocacion_global_push.sql` | creado, **NO aplicado** |
| SHA-256 de la migración | `c304cc10a6eb3d94f28187000b06bae07e0b8134258c2ecb94c6e3d5a76f4c76` |
| `database/tenant_migrations/manifest.json` | `schema_version = v9-sprint5-revocacion-global-push`; origen permitido `v8-sprint5-worker-fcm-push` añadido; nueva entrada `20261123000000/cu3c52_revocacion_global_push` |
| `000_preflight_cu3c5.sql` | extendido en sitio (sigue `READ ONLY` + `ROLLBACK`); acepta v7/v8 sin la tabla y falla cerrado ante una instalación parcial de v9 |
| `999_verify_cu3c5.sql` | extendido en sitio; exige la tabla, unicidad, estados, índices y ausencia de token en claro |
| `tenant_migrator.py` | `EXPECTED_VERSIONS` incluye `20261123000000`; `BASELINE_LEDGER_VERSIONS` añade la línea base v8 |

Los checksums de las migraciones históricas no se alteraron. Los archivos de
preflight/verify no son artefactos versionados del ledger: se extendieron
conservando su contrato de sólo lectura, y su checksum nuevo lo calcula el
manifiesto en cada plan.

Checksums de soporte actuales (calculados, no persistidos):

- `000_preflight_cu3c5.sql`: `e1be191b7cfa13f644159e48eaabb62e275c591874f45e0260a6b22e6a43aac5`
- `999_verify_cu3c5.sql`: `395327897572d14176c91480d6112a1c7c4898a6c78da3be160d8ece4128d5cc`

No se ejecutó ninguna migración en infraestructura real.

---

## 8. Pruebas ejecutadas y resultados exactos

### 8.1 Suite nueva (SQLite en memoria + Control Plane simulado)

`tests/test_cu3c52_revocacion_global_push.py` — **33 passed**.

Cubre: códigos FCM invalidantes; errores no invalidantes; solicitud única y
clave de idempotencia; token original ausente; rollback conjunto; worker sin
Control Plane; revocación de la pareja correcta; repetición idempotente con
fecha preservada; tenant B no revoca a A; dos tenants con el mismo
`usuario_id` local; huella inexistente; estado global inesperado; sólo la fila
objetivo; caída antes del commit global; caída después del commit global y
antes del ACK con reintento; lease vigente/vencido; fencing; validación del
tenant interno; ausencia de Control Plane; ausencia de token/huella en logs;
estática de migración/manifiesto/preflight/verify.

### 8.2 PostgreSQL sintético aislado

Se reutilizó la imagen **local** `postgres:17-alpine` (ID `e5d2edd28b1f`, sin
descarga, `--pull=never`):

| Control | Evidencia |
|---|---|
| Contenedor | `cu3c52-pg-synth` (desechable) |
| Versión | PostgreSQL **17.11** |
| Publicación | solo `127.0.0.1:55433 → 5432` |
| Datos | `tmpfs` en `/var/lib/postgresql/data`, sin volúmenes |
| Bases | `synth_cu3c51*` (prefijo sintético obligatorio del arnés) |
| Credenciales | efímeras de la instancia; no documentadas |
| Al terminar | contenedor detenido y eliminado |

Se ejecutó `tests/test_cu3c51_postgres_worker.py` con
`CU3C51_SYNTHETIC_DATABASE_URL`: **18 passed**. Incluye la migración real
v7 → v8 → v9 mediante `load_bundle`/`build_plan`/`execute_plan` y el adaptador
`PsycopgTenantDatabase`, además de dos pruebas nuevas de esta fase:

- restricciones v9: unicidad `(dispositivo_id, token_fingerprint)`, `CHECK` de
  estados, `CHECK` de huella y ausencia de columna de token;
- claim con `FOR UPDATE SKIP LOCKED`: dos conexiones independientes reclaman
  filas distintas, sin doble claim.

El `verify` y el `preflight` del manifiesto se ejecutaron como parte del plan
(v7 → v9) sin errores.

### 8.3 Regresiones

| Suite | Resultado |
|---|---|
| `test_cu3c5_worker_fcm_push.py`, `test_notificaciones_push_persistence.py`, `test_notificaciones_push_api.py`, `test_cu3c4_eventos_citas_notificaciones.py`, `test_cu31_tenant_migrator.py`, `test_cu36_i1_ledger_restore.py` (con la suite nueva) | **255 passed** |
| `test_auth_tenant.py`, `test_tenant_connection_router.py`, `test_clinical_request_routing.py`, `test_tenant_token_expiration.py` | **162 passed** |
| `test_cu10_paciente_movil.py` | **14 passed** |
| `test_cu3c51_postgres_worker.py` (PostgreSQL sintético) | **18 passed** |
| `python -m compileall app tests` | exit 0 |
| Importación `app.main` + OpenAPI | 157 rutas (sin endpoints nuevos) |
| `git diff --check` | sin errores |

Total ejecutado en esta fase: **449 pruebas aprobadas**, cero fallos.

### 8.4 Ajustes a pruebas existentes

El cambio de versión de esquema exigió actualizar expectativas que fijaban v8
como última versión:

- `test_cu31_tenant_migrator.py`: orden/versión final, fuentes permitidas,
  eventos aplicados y tamaño del ledger;
- `test_cu3c5_worker_fcm_push.py`: manifiesto v9 y DDL SQLite del outbox nuevo;
- `test_notificaciones_push_persistence.py`: modelo/tabla, DDL, manifiesto y
  concatenación de SQL para verificar nombres de constraints;
- `test_cu36_i1_ledger_restore.py`: ledger esperado de v8 y v9;
- `test_cu3c51_postgres_worker.py`: versión v9 y exclusión de v9 del ledger
  sembrado, más las dos pruebas PostgreSQL nuevas.

Ninguna prueba se relajó para ocultar fallos: todas siguen verificando los
mismos invariantes sobre la nueva versión.

---

## 9. Archivos creados / modificados

Creados:

- `app/modules/notificaciones_push/repositories/revocaciones.py`
- `app/modules/notificaciones_push/services/revocaciones.py`
- `database/tenant_migrations/versions/20261123000000_cu3c52_revocacion_global_push.sql`
- `tests/test_cu3c52_revocacion_global_push.py`
- `docs/CU3C52_REVOCACION_GLOBAL_PUSH.md`

Modificados:

- `app/modules/notificaciones_push/models/models.py` (modelo `RevocacionPushGlobal`)
- `app/modules/notificaciones_push/models/__init__.py`
- `app/modules/notificaciones_push/repositories/registro_global.py` (revocación acotada)
- `app/modules/notificaciones_push/repositories/__init__.py`
- `app/modules/notificaciones_push/services/__init__.py`
- `app/modules/notificaciones_push/services/worker_fcm.py`
- `app/modules/notificaciones_push/worker.py` (punto de entrada del procesador)
- `app/modules/administracion_saas/tenant_migrator.py`
- `database/tenant_migrations/manifest.json`
- `database/tenant_migrations/000_preflight_cu3c5.sql`
- `database/tenant_migrations/999_verify_cu3c5.sql`
- `tests/test_cu31_tenant_migrator.py`
- `tests/test_cu3c5_worker_fcm_push.py`
- `tests/test_notificaciones_push_persistence.py`
- `tests/test_cu36_i1_ledger_restore.py`
- `tests/test_cu3c51_postgres_worker.py`

No se modificaron login, JWT, RBAC, `get_db`, `TenantResolver`, routers HTTP,
Angular ni Flutter.

---

## 10. Bloqueos y limitaciones

1. **Migración v9 NO aplicada** en ningún tenant: queda preparada para una
   ejecución autorizada con el migrador oficial.
2. La actualización real de `saas_control.tenant_database.version_schema` a v9
   no se ejecutó; en el entorno sintético se usó el doble de Control Plane que
   ya emplea el arnés de 3C.5.1.
3. SQLite valida reglas, transacciones y fencing por consulta, pero **no**
   concurrencia real; ésta se validó en PostgreSQL 17.11 sintético.
4. El outbox de revocaciones no tiene scheduler/orquestador: el proceso debe
   invocarse externamente por tenant, igual que el worker FCM.
5. Si el token almacenado no produce una huella válida (<20 o >1024
   caracteres), el dispositivo se desactiva localmente **sin** solicitud
   global; no existe huella que revocar. Residuo documentado.
6. La reasignación de una huella `REVOCADO` a otro tenant sigue fuera de
   alcance y requerirá un flujo explícito y recuperable.
7. El envío FCM real y las credenciales OAuth siguen sin configurar.

---

## 11. Confirmaciones

- Cero conexiones a Clínica Visión Clara, otros tenants, legacy, Supabase o
  Control Plane real.
- Cero llamadas Firebase/FCM/APNs; el proveedor FCM fue un doble local.
- Cero lectura de `.env`, DSN, contraseñas, JWT, tokens o service-account JSON.
- Cero migraciones aplicadas en infraestructura real; sólo se preparó v9 y se
  validó en bases `synth_cu3c51*` del contenedor desechable ya eliminado.
- Cero instalaciones/descargas: se usó la imagen Docker local existente.
- Cero endpoints públicos nuevos (`OpenAPI` sigue con 157 rutas).
- Cero staging y cero operaciones Git de escritura (`add`, `commit`, `push`,
  `merge`, `rebase`, `checkout`, `switch`, `reset`, `restore`, `clean`,
  `stash`).

**Fase 3C.5.2 finalizada. Detención obligatoria: no se avanza a 3C.5.3, 3C.6 ni
Flutter sin autorización expresa.**
