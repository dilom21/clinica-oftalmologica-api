# Desarrollo local: FastAPI y worker CU31

Desde la raiz de `clinica-oftalmologica-api`, activa el entorno Conda y usa:

```cmd
conda activate clinica-backend
python scripts\dev.py
```

El launcher utiliza el mismo `python` del entorno activo e inicia exactamente
dos procesos hermanos:

- `python -m uvicorn app.main:app --reload`
- `python -m app.modules.gestion_pagos.notificacion_runner`

Uvicorn conserva el hot reload. Sus reinicios afectan solo al proceso de la
API; el worker no vive dentro de FastAPI y no se duplica con cada recarga.
Los dos procesos heredan el entorno y cargan el `.env` mediante la
configuracion existente.

Los registros de ambos aparecen en la misma terminal. Si cualquiera termina
inesperadamente, el launcher informa el codigo y detiene al otro. Para cerrar
el entorno pulsa `Ctrl+C` una vez; el launcher solicita el cierre de ambos
grupos de procesos y aplica un cierre forzado y acotado solo si no responden.

## Comandos independientes

Para diagnosticar solamente la API:

```cmd
python -m uvicorn app.main:app --reload
```

Para diagnosticar solamente el worker:

```cmd
python -m app.modules.gestion_pagos.notificacion_runner
```

No ejecutes el worker independiente al mismo tiempo que `scripts\dev.py`.
Ejecutar dos launchers manualmente tambien crea dos workers; PostgreSQL y la
idempotencia de Resend protegen la cola, pero no es una configuracion local
recomendada.

## Destinatario unico para comprobantes de prueba

El override esta desactivado por defecto. Para probar CU31 sin modificar el
correo de los pacientes sinteticos, configura localmente:

```dotenv
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxx
RESEND_FROM_EMAIL=onboarding@resend.dev
RESEND_FROM_NAME=Clinica Oftalmologica
EMAIL_TEST=correo-de-pruebas@example.com
```

Cuando `EMAIL_TEST` esta configurado, todos los comprobantes CU31 procesados
por el worker local (base legacy y tenants) se redirigen a esa direccion. No se
modifica el correo almacenado de ningun paciente. Si la variable esta ausente
o vacia, se utiliza normalmente el correo valido asociado al paciente.

Con el modo activo, una notificacion nueva queda `PENDIENTE` aunque ese
paciente sintetico no tenga correo valido. La outbox almacena el destinatario de
demostracion y los logs (`CU31_EMAIL_TEST_REDIRECT`) aclaran que la aceptacion o
entrega no corresponde al paciente. El PDF, la idempotencia y los reintentos no
cambian.

El arranque falla de forma segura si `EMAIL_TEST` es invalido o si se detecta
Render/produccion mediante las variables del entorno de despliegue. Para volver
al comportamiento normal elimina o deja vacio `EMAIL_TEST`. Para habilitar el
mismo destinatario unico en AWS sin depender de localhost existe
`RESEND_DEMO_MODE`, descrito mas abajo.

## Despliegue posterior

Este launcher es solo para desarrollo. En Render o AWS la API y el worker deben
continuar como servicios separados, con ciclos de vida, escalado, variables y
registros independientes. No se debe usar `--reload` en produccion.

## Modo demostracion en AWS (`RESEND_DEMO_MODE`)

El guard original rechazaba `EMAIL_TEST` en cuanto detectaba Render o
`APP_ENV=production`. Para el despliegue universitario de demostracion se agrega
una bandera explicita, desactivada por defecto:

```dotenv
RESEND_API_KEY=re_CLAVE_CONFIGURADA_EN_AWS
RESEND_FROM_EMAIL=onboarding@resend.dev
RESEND_FROM_NAME=Clinica Oftalmologica
EMAIL_TEST=haroldresend@gmail.com
RESEND_DEMO_MODE=true
```

Con `RESEND_DEMO_MODE=true` el unico destinatario autorizado es `EMAIL_TEST`:

- el arranque de la API y del worker falla de forma controlada si la bandera
  esta activa y `EMAIL_TEST` falta o no es un correo valido;
- las notificaciones nuevas se crean ya con ese destinatario;
- el worker no envia a ninguna otra direccion: si una fila de outbox anterior
  conserva el correo del paciente, la reasigna al destinatario autorizado antes
  de enviar y lo registra con el evento `CU31_DEMO_DESTINATARIO_REASIGNADO`
  (no es un cambio silencioso). Solo la reasigna cuando ningun intento previo
  pudo registrar la clave idempotente en Resend: `intentos <= 1`, o
  `intentos = 2` con `ultimo_error = RESEND_NO_CONFIGURADO` (ese error se
  produce antes de cualquier llamada HTTP);
- si un envio anterior pudo ser aceptado por Resend, cambiar el destinatario
  reutilizaria la misma clave con otro payload (riesgo de
  `409 invalid_idempotent_request`, o duplicado si se generara una clave nueva):
  en ese caso la fila queda `FALLIDO` con
  `ultimo_error = DEMO_DESTINATARIO_NO_REASIGNABLE`, no se envia, no se
  reintenta automaticamente y el reintento manual responde `409`. El worker
  registra `CU31_DEMO_REASIGNACION_BLOQUEADA`;
- una fila antigua creada sin destinatario (paciente sin correo valido) nunca
  llego al proveedor: en modo demostracion el worker la reclama, le asigna el
  destinatario unico y la procesa con normalidad. Sin modo demostracion se
  conserva el comportamiento anterior (requiere reintento manual explicito);
- no se agregan tablas, estados, endpoints ni migraciones. Los cinco estados
  existentes (`PENDIENTE`, `PROCESANDO`, `ACEPTADO_POR_PROVEEDOR`, `FALLIDO`,
  `ENTREGADO`), la clave idempotente por pago y el aislamiento multitenant no
  cambian.

Con `RESEND_DEMO_MODE=false` (valor por defecto) se conserva el comportamiento
normal y el guard de Render/produccion: `EMAIL_TEST` solo se acepta en local.

## Operacion en AWS: API y worker como procesos independientes

Los dos procesos comparten imagen, sistema de archivos y variables, pero se
ejecutan por separado. Ningun integrante necesita abrir una consola ni lanzar
comandos por cada pago.

| Proceso | Comando | Variables imprescindibles |
|---|---|---|
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8000` (sin `--reload`) | `DATABASE_URL`, `JWT_SECRET_KEY`, `PASSWORD_RESET_URL_BASE`, `GMAIL_*`, `STRIPE_*`, `RESEND_*`, `EMAIL_TEST`, `RESEND_DEMO_MODE` |
| Worker | `python -m app.modules.gestion_pagos.notificacion_runner` | `DATABASE_URL`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `RESEND_FROM_NAME`, `EMAIL_TEST`, `RESEND_DEMO_MODE`, `RESEND_WORKER_BATCH_SIZE`, `RESEND_WORKER_INTERVAL_SECONDS`, `RESEND_TIMEOUT_SECONDS` |

En AWS el worker debe quedar como servicio permanente: un servicio ECS con task
definition propia (1 tarea, reinicio automatico) o una unidad `systemd` con
`Restart=always` sobre EC2. El proceso atiende `SIGTERM`/`SIGINT` y cierra el
ciclo en curso, por lo que ECS o `systemd` pueden reiniciarlo sin dejar
notificaciones en lease. La cola usa `FOR UPDATE SKIP LOCKED`, asi que dos
workers no duplican envios; aun asi, para la demostracion se recomienda una sola
tarea.

### Como verificar que el worker esta funcionando

1. `aws logs tail <grupo-del-worker> --follow` debe mostrar un ciclo cada
   `RESEND_WORKER_INTERVAL_SECONDS` (15 s por defecto) y ningun
   `Fallo el ciclo del worker de comprobantes`.
2. Tras un pago aprobado aparece `CU31_EMAIL_TEST_REDIRECT` con "no representa
   entrega al paciente".
3. En la base del tenant, `notificacion_pago` pasa de `PENDIENTE` a
   `ACEPTADO_POR_PROVEEDOR` y luego a `ENTREGADO` cuando llega el webhook
   firmado de Resend.
4. `GET /pagos/administrativos/{pago_id}/notificacion` expone el mismo estado.

### Si Resend falla temporalmente

- Rechazo 4xx: la fila queda `FALLIDO` con `ultimo_error = ENVIO_NO_ACEPTADO` y
  `proximo_intento` con retroceso exponencial (1, 2, 4 ... hasta 60 minutos),
  maximo 8 intentos. El pago confirmado no se revierte.
- 5xx, timeout o corte de transporte: `RESULTADO_ENVIO_DESCONOCIDO`. Se reintenta
  una sola vez dentro de la ventana de 24 h en la que Resend conserva la clave
  idempotente, para no duplicar un comprobante que el proveedor pudo aceptar.
- `DEMO_DESTINATARIO_NO_REASIGNABLE`: requiere intervencion manual. No se
  reintenta solo ni desde el endpoint porque la clave idempotente ya pudo usarse
  con el destinatario original; emitir una clave nueva podria duplicar el
  comprobante. Revisa el `pago_id` con el equipo y documenta la decision.
- Un tenant con problemas no detiene la cola de las demas clinicas: el ciclo
  continua y registra la excepcion con `empresa_codigo`.

### Como confirmar que el comprobante llega solo al destinatario autorizado

- En cada tenant, `SELECT DISTINCT destinatario FROM notificacion_pago;` debe
  mostrar unicamente `haroldresend@gmail.com` para las filas que el worker ya
  proceso (cualquier fila anterior se reasigna antes de enviar).
- `SELECT pago_id FROM notificacion_pago WHERE ultimo_error =
  'DEMO_DESTINATARIO_NO_REASIGNABLE';` debe devolver cero filas. Si devuelve
  alguna, no la reenvies a mano: la clave idempotente ya pudo usarse con el
  correo del paciente y hay que decidirlo con el equipo.
- En el panel de Resend, todos los envios de la demostracion deben figurar con
  ese destinatario y el remitente `onboarding@resend.dev`.
- Si `EMAIL_TEST` cambia, reinicia API y worker: la configuracion se valida al
  arrancar y no se recarga en caliente.
