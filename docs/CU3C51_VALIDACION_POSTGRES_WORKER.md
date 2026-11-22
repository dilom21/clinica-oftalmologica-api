# CU3C.5.1 — Validación PostgreSQL aislada del Worker FCM y migración v7 → v8

Fase 3C.5.1 del backend SI2 (Clínica Oftalmológica). Rama `dev-josias`.

Esta fase **no** agrega funcionalidad de producto, **no** aplica migraciones en
infraestructura real, **no** conecta tenants/Supabase/Control Plane real, **no**
llama Firebase/FCM, **no** lee `.env` ni secretos y **no** ejecuta operaciones
Git de escritura.

## 1. Repositorio y estado Git

| Dato | Valor |
|---|---|
| Ruta | `C:\SI2_Proyecto\clinica-oftalmologica-api` |
| Rama | `dev-josias` |
| HEAD inicial y final | `393f4e12a83b64a484cb24a81e2a1437c364bccd` |
| Staged inicial/final | 0 / 0 |
| Modificados inicial/final | 60 / 60 |
| Untracked inicial/final | 63 / 65 |
| Worktree | raíz del repositorio, no un worktree adicional |

Las dos entradas nuevas son `tests/test_cu3c51_postgres_worker.py` y
`docs/CU3C51_VALIDACION_POSTGRES_WORKER.md`. El incremento
de untracked respecto a la referencia de cierre 3C.5 (54) se explica por
artefactos ya existentes de esa fase y por los documentos de 3C.5/3C.5.1; el
conteo de modificados no cambió y no hubo borrados ni restauraciones.

## 2. Entorno PostgreSQL sintético y aislamiento

Se reutilizó la imagen **local** `postgres:17-alpine` (sin descarga, con
`--pull=never`) para crear un contenedor desechable:

| Control | Evidencia |
|---|---|
| Contenedor | `cu3c51-pg-synth` (nombre inequívocamente sintético) |
| Imagen | `postgres:17-alpine` (ID local `e5d2edd28b1f`) |
| Versión servidor | PostgreSQL **17.11** (`x86_64-pc-linux-musl`) |
| Publicación | solo `127.0.0.1:55432 → 5432`; sin exposición externa |
| Mounts | `null`; datos en `tmpfs` (`/var/lib/postgresql/data`), sin volúmenes |
| Privilegios | `Privileged=false`; sin `--cap-add` ni acceso al host |
| Bases creadas | `synth_cu3c51`, `synth_cu3c51_worker`, `synth_cu3c51_migr`, `synth_cu3c51_*` de prueba |
| Credenciales | usuario/clave efímeros de la instancia sintética; no se documentan ni persisten |
| Identidad | `current_database()` empieza por `synth_cu3c51`; catálogo solo contiene bases sintéticas y plantillas |
| Instancia nativa 5432 | **no utilizada**: escucha en `0.0.0.0` y no se pudo demostrar aislamiento/credenciales sin leer `.env` |

El arnés falla cerrado: exige host loopback, backend PostgreSQL y nombre de base
con prefijo `synth_cu3c51`. Toda base derivada respeta el prefijo.

Al terminar las pruebas el contenedor se detuvo y eliminó; al usar `tmpfs` no
quedó ningún dato en disco.

## 3. Esquema sintético v7

Un tenant v7 se preparó con el **mecanismo real** del proyecto:

1. tablas base mínimas del tenant con llaves `BIGINT` (`usuario`, `cita`, `rol`,
   `bitacora`, `paciente`, `modulo`, `funcion`, `accion`, `rol_funcion`),
   creadas solo con las columnas que las migraciones y el preflight exigen;
2. `app_migrations.schema_migration` mediante `bootstrap_ledger.sql` real;
3. migración 3C.2 `20261121000000` aplicada con `PsycopgTenantDatabase`
   (`apply_transactional`), que crea `dispositivo_push`, `notificacion`,
   `notificacion_entrega`, `preferencia_notificacion` y `cita_evento`;
4. ledger completado con las seis versiones previas y la migración externa que
   un tenant v7 real ya tendría aplicadas.

No se copiaron filas reales. Usuarios, pacientes, citas, dispositivos,
notificaciones y entregas son ficticios (`usuario_id=9001`, tokens
`sintetico-token-*`). Ningún token se envió a FCM.

**Limitación declarada:** las migraciones CU31/CU32/CU33/CU36 no se reejecutaron
porque el repositorio no incluye el DDL completo del esquema base ni la tabla
legacy real. Sus versiones se registraron en el ledger con los checksums del
manifiesto. El delta validado es exactamente v7 → v8.

## 4. Concurrencia real (dos+ conexiones independientes)

Pruebas en `tests/test_cu3c51_postgres_worker.py`, con dos `Engine` SQLAlchemy y
`NullPool` (conexiones físicas independientes) y transacciones simultáneas sin
`sleep` arbitrarios.

### 4.1 `FOR UPDATE SKIP LOCKED`

| Escenario | Resultado |
|---|---|
| Worker A reclama E1 y mantiene el lock sin `commit` | E1 queda `PROCESANDO`, `lease_token=lease-a` |
| Worker B reclama concurrentemente con E2 elegible | B obtiene **solo E2**; no hay intersección |
| Solo existe una fila y A la tiene bloqueada | B devuelve `[]`; no duplica el claim |
| SQL compilado para dialecto PostgreSQL | contiene `FOR UPDATE SKIP LOCKED` |
| `EXPLAIN` del predicado de cola | plan `LockRows` sobre el outbox |

### 4.2 Lease vigente y vencido

- `PROCESANDO` con `bloqueado_hasta` futuro: **no** se reclama.
- `PENDIENTE` con `proximo_intento` futuro: **no** se reclama.
- `PROCESANDO` con `bloqueado_hasta` vencido: se recupera y recibe un
  `lease_token` nuevo (`lease-nuevo ≠ lease-vencido`).
- `intentos` no se modifica durante el claim (se conserva en 3).

### 4.3 Fencing a nivel SQL

1. A obtiene `lease-a` y confirma el claim.
2. El lease vence; B reclama y obtiene `lease-b`.
3. A ejecuta `actualizar_resultado(..., lease_token="lease-a")` → **0 filas**.
4. La fila sigue `PROCESANDO` con `lease-b`.
5. B persiste con `lease-b` → **1 fila**; estado final `ACEPTADO_POR_FCM` y
   `proveedor_mensaje_id` de B.

No se simuló fencing en memoria: la condición de `UPDATE` se evaluó en
PostgreSQL.

### 4.4 Transacciones

| Operación | Commit propio | Evidencia |
|---|---|---|
| `reclamar_lote` | No | rollback deja la entrega en `PENDIENTE`, sin lease |
| `actualizar_resultado` | No | participa de la transacción del worker |
| `procesar_entregas` | Sí: confirma el claim antes de red y cada resultado después | proveedor que lanza excepción deja la fila `PROCESANDO` con lease, recuperable tras vencer; sin resultado inventado |
| Worker exitoso | Sí | `ACEPTADO_POR_FCM`, `intentos=1`, `lease_token=NULL`, `fecha_aceptado` no nula |

Un fallo de red posterior al claim no deja estados parciales ni marca aceptación.

## 5. Migración v7 → v8

Mecanismo: `load_bundle` + `build_plan` + `execute_plan` +
`PsycopgTenantFactory`/`PsycopgTenantDatabase` reales.

| Paso | Resultado |
|---|---|
| Preflight v7 | correcto |
| Plan | estados: 7 versiones `applied`, `20261122000000` `pending` |
| Checksums de origen | validados contra `manifest.json` |
| Ejecución | `applied = ("20261122000000",)`; las 7 anteriores `skipped` |
| `lease_token` | `character varying(64)` |
| CHECK de estado | admite `PENDIENTE, PROCESANDO, ACEPTADO_POR_FCM, FALLIDO, INVALIDO, OMITIDO` |
| Índice de lease | `ix_notificacion_entrega_lease`, no único, parcial `WHERE estado='PROCESANDO'` |
| Índice de cola 3C.2 | presente |
| Verify | ejecutado dos veces, más preflight repetido: correcto |
| Ledger | contiene la versión v8 con su checksum del manifiesto |
| Versión de Control Plane | actualizada a `v8-sprint5-worker-fcm-push` (doble en memoria) |
| Reaplicar v8 | falla cerrado (`lease_token` ya existe) |
| Instalación parcial (lease sin `OMITIDO`) | preflight falla cerrado |
| Origen desconocido | plan rechazado |
| Checksum alterado en ledger | `ChecksumMismatchError` |

Checksums verificados:

- v8 `20261122000000_cu3c5_worker_fcm_push.sql`:
  `d769152308bed9a3086adae694e23edf859295e20828fc2478025d16db7897a9`
- `000_preflight_cu3c5.sql`:
  `8c0e2b590cfe4c769d56e7100141a815f8c6802c6484d08b2353c56ebcabe7b7`
- `999_verify_cu3c5.sql`:
  `64efbe5c8b2f94c7a179165e92cc49891023542c69b597a023109b5da8946652`
- Manifest: `schema_version=v8-sprint5-worker-fcm-push`, origen permitido
  `v7-sprint5-notificaciones-push`.

**Limitación:** el Control Plane se sustituyó por un doble en memoria porque
esta fase prohíbe abrir su base real. La validación cubre el DDL, ledger,
preflight, verify y la transición de versión local; la actualización real en
`saas_control.tenant_database` queda pendiente de una ejecución autorizada.

## 6. Regresiones

| Suite | Resultado |
|---|---|
| `tests/test_cu3c51_postgres_worker.py` (nueva, PostgreSQL) | **16 passed** |
| `test_cu3c5_worker_fcm_push.py`, `test_notificaciones_push_persistence.py`, `test_notificaciones_push_api.py`, `test_cu3c4_eventos_citas_notificaciones.py` | **158 passed** |
| `test_cu31_tenant_migrator.py`, `test_cu36_i1_ledger_restore.py` | **60 passed** |
| `test_auth_tenant.py`, `test_tenant_connection_router.py`, `test_clinical_request_routing.py`, `test_tenant_token_expiration.py` | **162 passed** |
| `python -m compileall app tests` | exit 0 |
| Importación `app.main` + OpenAPI | 157 rutas |
| `git diff --check` | sin errores |

Total ejecutado: **396 pruebas aprobadas**, sin fallos.

## 7. Archivos

Creado:

- `tests/test_cu3c51_postgres_worker.py` — arnés PostgreSQL aislado (se omite
  limpiamente si no existe `CU3C51_SYNTHETIC_DATABASE_URL`).
- `docs/CU3C51_VALIDACION_POSTGRES_WORKER.md` — este informe.

No se modificó código productivo, migraciones, manifest, checksums, login, JWT,
RBAC, `get_db`, TenantResolver, Angular ni Flutter.

## 8. Confirmaciones

- Cero conexiones a tenants reales, Clínica Visión Clara, legacy, Supabase o
  Control Plane real.
- Cero llamadas Firebase/FCM/APNs; proveedor FCM sustituido por dobles.
- Cero lectura de `.env`, DSN, contraseñas, JWT o service-account JSON.
- Cero migraciones aplicadas en infraestructura real; solo en bases
  `synth_cu3c51_*` del contenedor desechable, ya eliminado.
- Cero instalaciones o descargas: se usó la imagen Docker local existente.
- Cero operaciones Git de escritura (`add`, `commit`, `push`, `merge`,
  `rebase`, `checkout`, `switch`, `reset`, `restore`, `clean`, `stash`).

## 9. Limitaciones y pendientes

1. La base v7 reproduce fielmente el subconjunto de notificaciones y las tablas
   base exigidas; no reejecuta CU31/CU32/CU33/CU36 porque el DDL base completo
   no está en el repositorio.
2. La actualización de versión en Control Plane real no se ejecutó.
3. La concurrencia se validó con dos conexiones en una instancia PostgreSQL 17
   local aislada; no sustituye una prueba de carga productiva.
4. El worker mantiene semántica **at-least-once**: si FCM acepta y el proceso
   cae antes de persistir, el reintento puede duplicar el envío.
5. La liberación global de huellas de tokens invalidados en Control Plane sigue
   pendiente.
6. Orquestación/scheduler del worker permanece fuera de alcance.
