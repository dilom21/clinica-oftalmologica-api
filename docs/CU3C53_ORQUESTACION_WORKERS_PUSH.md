# CU3C.5.3 — Orquestación segura de workers Push

Fase 3C.5.3 del backend SI2 (Clínica Oftalmológica). Rama `dev-josias`.
Repositorio exclusivo: `C:\SI2_Proyecto\clinica-oftalmologica-api`.

Esta fase **no** configura FCM real, **no** crea un scheduler productivo,
**no** conecta tenants ni Control Plane reales, **no** aplica migraciones,
**no** lee `.env`/secretos y **no** ejecuta operaciones Git de escritura.
Coordina los workers ya existentes de 3C.5 (entregas FCM) y 3C.5.2
(revocaciones globales) sin duplicar su lógica.

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
| Entradas untracked inicial/final | 72 / 74 |

El incremento de untracked corresponde a los dos artefactos nuevos:
`docs/CU3C53_ORQUESTACION_WORKERS_PUSH.md` y
`tests/test_cu3c53_orquestacion_workers_push.py`. Los archivos de código
`orquestacion.py` y `orquestador.py` viven dentro de
`app/modules/notificaciones_push/`, directorio untracked preexistente que
`git status` colapsa en una sola entrada; por eso no añaden entradas nuevas.
No se hizo staging ni ninguna operación Git de escritura.

---

## 2. Arquitectura

### 2.1 Capas

| Archivo | Responsabilidad |
|---|---|
| `app/modules/notificaciones_push/orquestacion.py` | Coordinador inyectable de un ciclo tenant: validación, orden, estado y cierre de sesiones. |
| `app/modules/notificaciones_push/orquestador.py` | Composición raíz y punto de entrada de una sola ejecución para un job externo. |
| `services/worker_fcm.py` (3C.5) | Claim, lease, fencing, envío FCM y resultado de entregas. Sin cambios. |
| `services/revocaciones.py` (3C.5.2) | Outbox, commit global y ACK local de revocaciones. Sin cambios. |
| `providers/fcm.py` (3C.5) | Adaptador FCM HTTP v1 con `ProveedorAccessToken` inyectado. Sin cambios. |

No se creó una segunda tenancy, un segundo sistema de colas, un daemon ni un
endpoint HTTP.

### 2.2 Unidad de ejecución y tenancy

- La unidad es **un único tenant ya resuelto** por una `FuenteSesiones`.
- El coordinador no recibe `tenant_id`, `empresa_id`, `database_name`, DSN,
  host, puerto ni credenciales: solo recibe la fuente de sesiones y el
  proveedor.
- La fuente de despliegue
  (`FuenteSesionesControlPlane`) reutiliza `TenantResolver` y
  `TenantEngineRegistry`, sin modificar `get_db`, `TenantResolver`, login,
  JWT ni RBAC.
- `tenant_database_id` se valida como entero positivo y se compara con
  `db.info["tenant_context"].tenant_database_id` cuando el contexto existe:
  si no coinciden, el ciclo falla cerrado.
- No se enumera `tenant_database` ni ninguna lista de empresas.

### 2.3 Fronteras de sesión y transacción

```text
composición raíz
  ├── sesión tenant (obligatoria) ──► worker FCM (no abre Control Plane)
  └── sesión Control Plane (opcional) ──► procesador de revocaciones
```

- El worker FCM recibe únicamente la sesión tenant.
- El procesador de revocaciones recibe la sesión Control Plane inyectada y
  aplica el protocolo ya validado: **commit global primero, ACK tenant
  después**.
- No hay transacción distribuida ni exactly-once. Las dos bases siguen siendo
  independientes.
- El coordinador no mantiene transacciones durante HTTP: los workers ya
  confirman el claim antes de la red y persisten el resultado en otra
  transacción.
- Las sesiones se cierran siempre en `finally`, también ante excepción,
  cancelación o presupuesto agotado. `SesionesCiclo.cerrar()` es idempotente
  y hace `rollback` defensivo antes de `close`.

### 2.4 Orden y comportamiento condicional

1. Se abre la composición de sesiones.
2. Si hay cancelación previa, no se reclama nada.
3. Se ejecutan las entregas FCM.
4. Si hay cancelación o se agotó el presupuesto de ciclo, no se inicia la
   etapa siguiente.
5. Se ejecutan las revocaciones pendientes, incluidas las generadas por la
   etapa 3.

Si el proveedor FCM está `NO_CONFIGURADO` o devuelve error global de
configuración, el worker no reclama entregas ni consume intentos. Las
revocaciones ya pendientes pueden procesarse **solo** si la sesión Control
Plane fue autorizada y está disponible. Un error de sesión, Control Plane u
OAuth nunca se convierte en éxito.

---

## 3. Invocación por scheduler externo

Punto de entrada (una sola ejecución, sin daemon):

```bash
python -m app.modules.notificaciones_push.orquestador \
  --empresa-codigo <CODIGO_DEFINIDO_EN_EL_JOB> \
  --lote-entregas 20 \
  --lote-revocaciones 20 \
  --lease-segundos 120 \
  --timeout-proveedor-segundos 10 \
  --duracion-maxima-segundos 90
```

Reglas de despliegue:

- El código de empresa es un **parámetro de la definición del job**, resuelto
  contra el Control Plane. No se acepta por HTTP, cuerpo ni cabecera de
  usuario, y no se imprime.
- La recurrencia y el host pertenecen al scheduler/job runner externo. El
  repositorio no crea cron, no arranca desde `app.main` y no expone rutas.
- El proceso termina siempre con un código de salida determinista y un único
  JSON agregado en stdout.
- No se leen `.env`, service-account JSON, DSN, tokens ni secretos.
- El proceso puede recibir `SIGTERM`/`SIGINT`: marca cancelación y no inicia
  una etapa nueva. Los claims ya confirmados quedan recuperables por lease.

### 3.1 Límites y validación

| Límite | Rango | Efecto |
|---|---|---|
| `tamano_lote` (entregas/revocaciones) | 1–100 | Máximo de filas reclamadas por etapa. |
| `lease_segundos` | 5–3600 | Vencimiento del claim; recuperable tras expirar. |
| `max_intentos` | 1–20 | Límite de reintentos de cada worker. |
| `timeout_proveedor_segundos` | ≥0.1 y **menor** que el lease | Timeout HTTP del proveedor. |
| `duracion_maxima_segundos` | >0 y **≤ lease menor** | Presupuesto blando entre etapas. |

`ConfiguracionCiclo` valida todo antes de abrir sesiones. Un valor inválido
produce un error de configuración saneado y código de salida 2, sin tocar la
base.

### 3.2 Códigos de salida

| Estado | Código | Significado |
|---|---|---|
| `COMPLETADO` | 0 | Ciclo correcto con trabajo procesado. |
| `SIN_TRABAJO` | 0 | No había filas reclamables. |
| `PARCIAL` | 1 | Hubo fallos terminales, conflictos de lease o revocaciones reprogramadas. |
| `ERROR_SESION` | 2 | No se pudo abrir/validar la sesión tenant. |
| `ERROR_CONTROL_PLANE` | 2 | Control Plane ausente o no disponible; no se finge éxito. |
| `ERROR_PROVEEDOR` | 2 | Error global de credenciales/configuración FCM. |
| `CANCELADO` | 3 | Detención solicitada; los leases quedan recuperables. |
| `TIEMPO_AGOTADO` | 3 | Se agotó el presupuesto entre etapas. |
| `ERROR_INESPERADO` | 3 | Excepción no clasificada; se hace rollback y se cierran sesiones. |
| `PROVEEDOR_NO_CONFIGURADO` | 4 | No se reclamaron entregas ni se consumieron intentos. |

---

## 4. OAuth y credenciales FCM

- `FcmHttpV1` sigue dependiendo de un `ProveedorAccessToken` inyectado.
- No se leen archivos de service account, `.env`, variables secretas ni
  credenciales locales.
- No se implementó un proveedor OAuth improvisado ni se añadieron
  dependencias.
- `obtener_proveedor_fcm()` devuelve un adaptador sin credenciales: estado
  real `NO_CONFIGURADO`. No se simula una aceptación FCM.
- **BLOQUEADO**: falta un gestor de secretos/proveedor OAuth aprobado por el
  despliegue. Contrato requerido:

```python
class ProveedorAccessToken(Protocol):
    def access_token(self) -> str: ...
```

El despliegue debe construir `FcmHttpV1(proyecto_id=..., credenciales=...)`
con una fuente autorizada y pasarlo al coordinador. Mientras eso no exista,
el ciclo solo procesa revocaciones pendientes y termina con código 4.

---

## 5. Recuperación y garantías

- **Leases**: el claim se confirma antes de la red. Una caída deja la fila
  `PROCESANDO`; al vencer `bloqueado_hasta` otro ciclo la recupera.
- **Fencing**: el resultado se actualiza condicionado por `lease_token`; un
  worker antiguo no sobrescribe a uno nuevo.
- **Revocación entre bases**: si el Control Plane confirma y el proceso cae
  antes del ACK local, el reintento encuentra la fila `REVOCADO` y termina de
  forma idempotente (`ya_revocadas`).
- **At-least-once**: no existe exactly-once. Si FCM acepta un mensaje y el
  proceso cae antes de persistir `proveedor_mensaje_id`, el reintento puede
  duplicar el envío.
- **Cancelación/tiempo agotado**: no se inicia una etapa nueva; los claims
  existentes expiran por lease. No se confirma ningún resultado no persistido.

---

## 6. Observabilidad y privacidad

Cada ciclo registra un único evento agregado con:

- `ejecucion_id` aleatorio no sensible (16 hex);
- inicio/fin, duración y estado;
- contadores de entregas y revocaciones;
- disponibilidad de Control Plane y código de error saneado.

Nunca se registran: token FCM, access token, fingerprint completo, PII,
título/cuerpo, payload, DSN, host, credenciales ni nombre/código de empresa.
No se añadieron métricas ni endpoints públicos.

---

## 7. Pruebas

Suite nueva: `tests/test_cu3c53_orquestacion_workers_push.py` — **31 pruebas
aprobadas**. Cubre:

- ciclo completo (entregas + revocaciones) y orden de etapas;
- revocación generada en la misma ejecución;
- proveedor `NO_CONFIGURADO`: cero claims, cero intentos;
- error global de credenciales sin éxito ficticio;
- lote vacío, límite de lote, reintento y fallo terminal;
- fallo de sesión tenant, contexto tenant incoherente y ausencia de Control
  Plane;
- fallo antes del commit global y después del commit/antes del ACK con
  reintento idempotente;
- excepción del proveedor con cierre de sesiones y lease recuperable;
- cancelación previa y posterior a entregas, presupuesto agotado;
- límites inválidos y CLI con configuración inválida;
- lease vigente, fencing y recuperación tras vencimiento;
- aislamiento de dos tenants con IDs locales iguales;
- sanitización de logs y ausencia de rutas HTTP / arranque desde `app.main`.

Comandos ejecutados y resultados:

```text
python -m pytest tests/test_cu3c53_orquestacion_workers_push.py -q
31 passed

python -m pytest tests/test_notificaciones_push_persistence.py \
  tests/test_notificaciones_push_api.py \
  tests/test_cu3c4_eventos_citas_notificaciones.py \
  tests/test_cu3c5_worker_fcm_push.py \
  tests/test_cu3c51_postgres_worker.py \
  tests/test_cu3c52_revocacion_global_push.py \
  tests/test_cu3c53_orquestacion_workers_push.py -q
223 passed, 17 skipped

python -m pytest tests/test_cu31_tenant_migrator.py \
  tests/test_cu34_historial_comprobante_pagos.py \
  tests/test_clinical_request_routing.py \
  tests/test_cu26_cu27_rbac.py -q
101 passed

python -m pytest tests/test_auth_tenant.py tests/test_tenant_company_selector.py \
  tests/test_tenant_connection_router.py tests/test_tenant_patient_registration.py \
  tests/test_tenant_provisioning.py tests/test_tenant_token_expiration.py -q
341 passed

python -m compileall -q app tests
OK

python -c "import app.main; ... app.openapi()"
157 rutas; 0 rutas de worker/orquestador/ciclo

git diff --check
OK (solo avisos preexistentes de LF→CRLF)
```

Los 17 `skipped` son las pruebas PostgreSQL sintéticas de 3C.5.1: requieren
`CU3C51_SYNTHETIC_DATABASE_URL` y no se dispuso de esa instancia en esta
ejecución. **No se afirma** concurrencia PostgreSQL validada en esta fase.

---

## 8. Bloqueos de despliegue

| Elemento | Estado | Motivo |
|---|---|---|
| Scheduler/job productivo | **BLOQUEADO** | No hay host, job ni ventana autorizados. El punto de entrada existe, pero no se activó ninguna recurrencia. |
| Proveedor OAuth FCM | **BLOQUEADO** | No hay gestor de secretos/proveedor de access token aprobado. El adaptador permanece `NO_CONFIGURADO`. |
| Envío Push real | **BLOQUEADO** | Depende de OAuth y de migraciones aplicadas en un tenant autorizado. |
| Validación PostgreSQL de concurrencia | **BLOQUEADO en esta ejecución** | Sin instancia sintética aislada (`CU3C51_SYNTHETIC_DATABASE_URL`). |
| Migraciones | No requeridas | Esta fase no añade DDL ni modifica el manifest. Nada fue aplicado. |

---

## 9. Confirmaciones

- Cero conexiones a Clínica Visión Clara, Supabase, Control Plane real,
  tenants reales o bases compartidas.
- Cero llamadas a Firebase/FCM/APNs.
- Cero migraciones aplicadas.
- Cero lectura de `.env`, DSN, contraseñas, JWT, tokens o service-account JSON.
- Cero instalación/actualización de dependencias.
- Cero cambios en Angular, Flutter, login, JWT, RBAC, `get_db` o
  `TenantResolver`.
- Cero endpoints HTTP nuevos y cero arranque de workers desde `app.main`.
- Cero operaciones Git de escritura (`add`, `commit`, `push`, `merge`,
  `rebase`, `checkout`, `switch`, `reset`, `restore`, `clean`, `stash`).
- Los cambios preexistentes se preservaron intactos.

**Fin de la Fase 3C.5.3. No se avanza a 3C.6 ni Flutter sin autorización
expresa.**
