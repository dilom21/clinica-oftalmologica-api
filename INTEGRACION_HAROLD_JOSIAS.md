# Integración local Harold + Josias — Backend

Fecha: 23 de noviembre de 2026. Repositorio `clinica-oftalmologica-api`.
Rama local de integración: `integracion-harold-josias`.
No se ejecutó `git push`, no se creó rama remota y no se modificó `dev-harold`.

## 1. Estado inicial

| Referencia | Hash |
|---|---|
| `dev-harold` local | `f37b5d7055da2ce5810b151a519f649c206b1528` |
| `origin/dev-harold` | `f37b5d7055da2ce5810b151a519f649c206b1528` |
| `origin/dev-josias` | `35c4a33d42388980fc7772175e503cd1a43af610` |
| Base común | `506026827bf7cd75db71cbcf8045703e6f8352b9` |

`origin/dev-salet` es ancestro de `dev-harold` (verificado), por lo que el trabajo
de Salet ya estaba incorporado. `dev-salet`, `dev-jerez`, `dev-austin` y
`dev-quisbert` **no** son ancestros de `origin/dev-josias`. `origin/main` sí es
ancestro de Josias porque él fusionó `origin/main` en su rama; esa procedencia es
histórica y no se fusionó `main` directamente en esta integración.

Los cuatro SQL que aparecían modificados no tenían cambios de contenido: sus
bytes eran idénticos a HEAD. Se creó un stash limitado a esos archivos
(`stash@{0}`, mensaje `preintegracion-sql-eol-20261123`) que resultó **vacío**
(0 líneas). No se aplicó ni se descartó. Recuperación: `git stash apply stash@{0}`
(no restaura contenido) o `git stash drop stash@{0}` si se decide limpiarlo.

## 2. Integración y commits

- Rama creada desde `dev-harold`: `integracion-harold-josias`.
- Merge local `--no-ff --no-commit` de `origin/dev-josias`.
- Commit de merge: `3b74c51` (`merge: integrar dev-josias en dev-harold (backend CU26/CU27/CU36/push/IA)`).
- Commit de compatibilidad: `78a9cf2` (`test: adaptar expectativas a la integracion CU41 y RBAC modular`).
- HEAD final: `78a9cf2694b14420d18743f0d2f2211b517d08a3`.
- `dev-harold` local y remoto permanecen en `f37b5d7`.
- `git merge-base --is-ancestor dev-harold HEAD` y
  `git merge-base --is-ancestor origin/dev-josias HEAD`: ambos código 0.

## 3. Funcionalidades incorporadas de Josias

- CU26/CU27: inventario, categorías, lotes, movimientos y RBAC modular.
- CU36: ajustes de pago, anulación/corrección/reembolso, webhooks huérfanos,
  conciliación y ledger/restore.
- Reportes progresivos con KPIs, series, comparaciones y exportación CSV/XLSX/PDF
  (módulo administrativo).
- IA predictiva: pronóstico de demanda e inasistencia (módulo administrativo).
- Notificaciones push: registro de dispositivos, bandeja, preferencias,
  eventos de cita, recordatorios, worker FCM y revocación global.
- Endurecimiento SaaS: restore, backup, aprovisionamiento, validación estricta de
  claims JWT y permisos por módulo.
- Docker/forwarded headers y documentación de los bloques anteriores.

## 4. Conflicto y resolución

Único conflicto de contenido: `app/modules/gestion_pagos/api/administrativos.py`.
La base común no tenía esas líneas; Harold añadía el servicio/schema de
`estado_cuenta` (CU34) y Josias añadía `huerfanos_service` y los servicios de
ajustes. Se conservaron **ambos** imports. Las rutas de estado de cuenta y las de
ajustes/anulación/reembolso conviven sin duplicados; la suite completa lo valida.

## 5. Adaptaciones de compatibilidad

| Archivo | Motivo | Cambio |
|---|---|---|
| `tests/test_notificaciones_push_api.py` | La prueba de OpenAPI filtraba todo prefijo `/notificaciones` y capturaba también `/notificaciones-chatbot` (CU41). | Se acota el contrato push a `/notificaciones` y `/notificaciones/…`; CU41 conserva sus pruebas. |
| `tests/test_cu34_estado_cuenta.py` | El nuevo RBAC exige `funcion.modulo_id` y un módulo `Pagos`. | Se inserta el módulo y las funciones con `modulo_id` correcto. |
| `pytest.ini` | El ZIP extraído `CU26_Backend_dev_josias/` contiene una copia histórica de pruebas sin autenticación tenant, ya superada por `tests/test_cu26_backend.py`. | Se excluye ese directorio de la recolección; no se borraron archivos. |
| `.gitattributes` | Documentación Markdown importada y activos minificados tienen espacios finales intencionales/heredados. | Se desactiva el chequeo de trailing-space/blank-at-eof para `*.md` y `.graphify/**`. |

No se debilitó autenticación, RBAC, aislamiento ni privacidad.

## 6. Migraciones

`schema_version` pasó a `v9-sprint5-revocacion-global-push`; se conservaron las
migraciones previas y se añadieron:

- `20261116000000_cu36_ajustes_pago.sql`
- `20261117000000_cu36_webhooks_huerfanos.sql`
- `20261121000000_cu3c2_notificaciones_push.sql`
- `20261122000000_cu3c5_worker_fcm_push.sql`
- `20261123000000_cu3c52_revocacion_global_push.sql`
- Control-plane: `008_create_stripe_webhook_huerfano.sql`, `009_create_dispositivo_push_registry.sql`, `010_expand_version_schema.sql`
- RBAC tenant: `database/tenant_rbac/20261102_seed_cu26_cu27.sql`

Los **9 checksums** del manifiesto se verificaron contra los bytes reales y
coinciden. **No se aplicaron migraciones** a PostgreSQL, Supabase, AWS ni datos
reales; quedan pendientes para el entorno de despliegue autorizado.

## 7. Dependencias y configuración

- `requirements.txt` no cambió; `google-auth`, `httpx` y el resto ya estaban.
- Variables nuevas o relevantes (solo nombres): `FCM_PROJECT_ID`,
  `FORWARDED_ALLOW_IPS`. El proveedor FCM usa Application Default Credentials;
  no se incluyeron credenciales.
- `.env.example` actualizado por Josias: JWT de ejemplo no válido, mínimo 32 bytes
  en producción/Render. No se copió ni modificó el `.env` real.

## 8. Validación Backend

| Ejecución | Resultado |
|---|---|
| CU41 (chatbot, unidades, catálogo, social, regresión, real opt-in) | **242 aprobadas, 15 omitidas** |
| Suite general | **2753 aprobadas, 41 omitidas, 0 fallos, 0 errores; 1728 advertencias; 135,49 s** |
| `python -m compileall -q app` | Correcto |
| `git diff --check` | Correcto |
| OpenAPI | 164 rutas generadas correctamente |
| Migraciones | 9/9 checksums correctos |

Las 41 omisiones son pruebas PostgreSQL/entornos opcionales sin variables
autorizadas. Las advertencias son claves de prueba y adaptadores SQLite
deprecados. No se ejecutaron pagos, correos, push reales ni migraciones.

## 9. CU41 después de la integración

Los archivos de `notificaciones_interaccion_chatbot` no fueron modificados por el
merge. Se conservan DeepSeek, Function Calling, catálogo determinista, contexto
público, voz sin Markdown, respuestas sociales locales, privacidad, aislamiento
tenant y la prohibición de reservar automáticamente. CU41 sigue pasando 242
pruebas.

## 10. Riesgos y pendientes

- Aplicar las migraciones nuevas en un PostgreSQL/Supabase de pruebas autorizado
  antes de desplegar; no se probó el worker FCM real ni el envío push.
- El RBAC modular exige que los tenants existentes tengan módulos/funciones
  sembrados; el seed y las migraciones deben ejecutarse en orden.
- Producción/Render exige `JWT_SECRET_KEY` de al menos 32 bytes.
- El repositorio incorpora artefactos de Josias (`.graphify`, `.atl`, ZIPs, `.bak`,
  `CU26_Backend_dev_josias/`) que conviene limpiar en una tarea posterior.
- La integración se validó con SQLite/mocks para la mayoría de suites; no
  sustituye una prueba E2E con PostgreSQL y FCM reales.

## 11. Comandos para levantar el Backend integrado

```powershell
cd C:\Users\Harold\Desktop\Clinica-Server\clinica-oftalmologica-api
git switch integracion-harold-josias
# Configurar DATABASE_URL, JWT_SECRET_KEY y credenciales en el entorno local
python -m uvicorn app.main:app --reload
python -m pytest -q
```

## 12. Git final

- Rama actual: `integracion-harold-josias`.
- Sin upstream remoto; no existe `origin/integracion-harold-josias`.
- `git status` limpio.
- Sin push, sin PR, sin merge a `main`, sin cambios en `dev-harold`.
