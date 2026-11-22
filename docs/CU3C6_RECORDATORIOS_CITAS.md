# CU3C.6 — Recordatorios automáticos de citas

## Estado

Implementación backend local, sin scheduler productivo, migraciones, conexiones
reales ni llamadas FCM/APNs. Reutiliza la bandeja, fanout, outbox y worker FCM
de 3C.2–3C.5.3.

## Política temporal

No se encontró una anticipación de negocio aprobada. Por ello no existe offset
por defecto. La CLI exige configuración explícita mediante opciones repetibles
`--recordatorio-anticipacion-minutos` y
`--recordatorio-gracia-minutos`. Sin ambas, el ciclo termina antes de abrir
sesiones con `POLICY_NOT_CONFIGURED` (exit 5). Valores inválidos terminan con
`POLITICA_INVALIDA` (exit 5).

La zona canónica es `APP_TIMEZONE`. La fecha y hora locales de `Cita` se
combinan con `zoneinfo`; horas DST inexistentes o ambiguas se omiten sin
adivinar. Solo son elegibles `PROGRAMADA` y `CONFIRMADA`, siempre futuras. La
gracia está acotada a 0–1440 minutos, cada anticipación a 1–525600 minutos y el
lote a 1–100 citas.

Ejemplo sintáctico (los valores deben proceder de una política aprobada, no de
este documento):

```text
python -m app.modules.notificaciones_push.orquestador \
  --empresa-codigo <CODIGO_DEL_JOB> \
  --recordatorio-anticipacion-minutos <VALOR_APROBADO> \
  --recordatorio-gracia-minutos <VALOR_APROBADO>
```

## Flujo e idempotencia

1. El orquestador valida la política antes de abrir sesiones.
2. El productor recibe la sesión tenant ya resuelta y selecciona citas futuras.
3. Resuelve `Cita → Paciente activo → Usuario activo` en esa sesión.
4. Crea una notificación `RECORDATORIO_CITA` genérica e in-app.
5. Reutiliza `encolar_entregas`, que aplica `push_habilitado` y `recordatorios`.
6. Confirma el lote atómico para que el worker existente pueda reclamarlo.
7. Ejecuta worker FCM y luego revocaciones globales como en 3C.5.3.

La clave es `recordatorio:<cita_id>:<offset_minutos>:<huella_horario>`. La
huella SHA-256 truncada representa fecha y hora programadas, no contiene PII y
cabe en `evento_clave VARCHAR(150)`. La unicidad existente evita duplicados;
solo una colisión de esa clave se trata como repetición idempotente.

Una reprogramación genera otra huella. El productor marca `cancelada_en` en
recordatorios anteriores cuando vuelve a examinar la cita. Además, el worker
revalida inmediatamente antes del proveedor: cita existente, horario/huella,
estado `PROGRAMADA/CONFIRMADA`, paciente y usuario activos y destinatario
coincidente. Reprogramación, cancelación, atención o inasistencia producen
`OMITIDO/RECORDATORIO_OBSOLETO`, sin llamada FCM. No se borra historial.

## Privacidad y multitenancy

- Título: `Recordatorio de cita`.
- Cuerpo: `Tienes una cita próxima.`
- Payload: solo allowlist existente (`ruta`, `pantalla`, `seccion`, `accion`,
  `cita_id`).
- Sin nombre, documento, correo, teléfono, profesional, motivo, diagnóstico,
  observaciones, tenant, DSN, token o fingerprint.
- El productor no abre conexiones, no enumera tenants y no acepta selectores de
  tenant; una invocación procesa solo la sesión entregada por 3C.5.3.

## Persistencia

No se añadieron tablas, columnas, migraciones ni checksums. Se reutilizan
`notificacion.cancelada_en`, `notificacion.evento_clave`,
`notificacion_entrega` y su estado `OMITIDO`.

## Validación y límites

La suite `tests/test_cu3c6_recordatorios_citas.py` usa SQLite en memoria y
relojes/proveedores falsos. PostgreSQL concurrente específico de 3C.6 no se
ejecuta sin un entorno sintético autorizado; la unicidad real ya fue validada
en fases anteriores, pero esta fase no afirma una nueva prueba concurrente.

Despliegue real sigue bloqueado hasta aprobar: anticipaciones y gracia de
negocio, scheduler/job, migraciones pendientes de fases previas y proveedor
OAuth FCM autorizado.
