# CU19 — Programar controles médicos

Implementación aditiva de I-061 (FastAPI) e I-062 (Angular), asociada a una atención previa y recuperable desde el historial del paciente.

## Modelo y arquitectura reutilizados

Se inspeccionaron los tres repositorios y el esquema PostgreSQL mediante consultas de solo lectura. Backend: router → service → repository → SQLAlchemy; schemas Pydantic; JWT y permisos `funcion`/`accion`/`rol_funcion`; bitácora transaccional. Web: Angular standalone, rutas lazy, servicios HTTP, Reactive Forms, signals, Sidebar y variables de diseño existentes. El repositorio móvil no se modificó. El módulo de notificaciones es un esqueleto y no ofrece una integración funcional para reutilizar.

**La tabla `control_medico` ya existía en PostgreSQL.** Se mapea y conserva su estructura: `id BIGINT GENERATED ALWAYS AS IDENTITY`, `consulta_clinica_id`, `paciente_id`, `oftalmologo_id`, `fecha_programada DATE`, `motivo VARCHAR(255)` y `estado VARCHAR(30)`. Solo se agrega `observaciones TEXT NULL`.

La relación es `Paciente ← HistorialClinico ← ConsultaClinica ← ControlMedico`. El responsable se obtiene de `ConsultaClinica.oftalmologo`. La tabla existente exige también paciente y oftalmólogo: el servidor completa ambos desde la consulta, valida su coherencia al actualizar y nunca acepta esos identificadores en el formulario. No se crea una segunda agenda ni se altera una cita.

Los controles anteriores con consulta, motivo o estado nulos continúan siendo legibles. Un control sin atención de origen no puede administrarse mediante CU19, y responde `409`; no se inventa una relación para repararlo.

## Archivos creados

En `clinica-oftalmologica-api`:

- [models/controles.py](app/modules/gestion_historial_clinico/models/controles.py): mapeo de la tabla existente.
- [schemas/controles.py](app/modules/gestion_historial_clinico/schemas/controles.py): creación, actualización parcial, respuesta y estados.
- [repositories/controles.py](app/modules/gestion_historial_clinico/repositories/controles.py): persistencia y filtros.
- [services/controles.py](app/modules/gestion_historial_clinico/services/controles.py): validaciones, autorización clínica y bitácora.
- [api/controles.py](app/modules/gestion_historial_clinico/api/controles.py): endpoints de CU19.
- [test_cu19_controles_medicos.py](tests/test_cu19_controles_medicos.py): pruebas de integración aisladas.
- [cu19_controles_medicos.sql](database/cu19_controles_medicos.sql): extensión aditiva de PostgreSQL y permiso nuevo.
- [aplicar_sql_cu19.py](scripts/aplicar_sql_cu19.py): verificación/aplicación del SQL sin importar la aplicación.
- [probar_cu19.py](scripts/probar_cu19.py): ejecución local de pruebas con configuración ficticia solo para ese proceso.
- [servidor_local_cu19.py](scripts/servidor_local_cu19.py): arranque local para probar CU19 con el pool de transacciones de Supabase.
- [test_cu19_servidor_local.py](tests/test_cu19_servidor_local.py): verificación aislada del arranque local y conservación de la configuración.
- Este documento.

En `clinica-oftalmologica-web`, bajo `src/app/features/gestion-historial-clinico/casos-uso/cu19-programar-controles-medicos/`:

- `models/control-medico.models.ts`.
- `services/control-medico.service.ts` y `control-medico.service.spec.ts`.
- `components/seguimiento-controles/seguimiento-controles.ts`, `.html`, `.css` y `.spec.ts`.
- `pages/programar-controles/programar-controles.ts`, `.html`, `.css` y `.spec.ts`.

## Archivos existentes modificados y motivo

- API: [api/router.py](app/modules/gestion_historial_clinico/api/router.py), solo importación e inclusión del router nuevo. Se registra antes de `/{paciente_id}` para que `/controles` tenga su ruta propia.
- Web: `src/app/app.routes.ts`, agrega la ruta protegida y lazy `/programar-controles-medicos`.
- Web: `src/app/core/layouts/sidebar/sidebar.ts`, asocia el nombre de la función nueva con la ruta.
- Web: CU13, `pages/consultar-historial-clinico/consultar-historial-clinico.ts` y `.html`, importan y muestran el componente de seguimiento para el paciente seleccionado.
- Web: CU15, `pages/registrar-consulta/registrar-consulta.ts` y `.html`, agregan el enlace contextual «Programar control» tras una consulta guardada, con paciente y consulta ya conocidos.

Estos puntos permiten que CU19 sea accesible dentro de la navegación y el contexto clínico existentes. No se modificaron DTO, respuestas, nombres de endpoints, servicios anteriores, autenticación, estilos globales ni dependencias declaradas. No se eliminaron archivos o funcionalidades existentes. El archivo vacío `casos_uso/cu19_programar_controles_medicos.py` permanece como estaba; los casos ya implementados utilizan el servicio invocado desde el router.

## Endpoints y permisos

Todos usan el prefijo `/historial-clinico` y el JWT existente.

| Método | Ruta agregada | Permiso |
| --- | --- | --- |
| POST | `/consultas/{consulta_id}/controles` | `Programar controles médicos` + ESCRITURA |
| GET | `/consultas/{consulta_id}/controles` | `Consultar historial clínico` + LECTURA |
| GET | `/controles` | `Consultar historial clínico` + LECTURA |
| GET | `/controles/oftalmologo-actual` | `Consultar historial clínico` + LECTURA |
| GET | `/controles/{control_id}` | `Consultar historial clínico` + LECTURA |
| PUT | `/controles/{control_id}` | `Programar controles médicos` + ESCRITURA |

`AMBAS` satisface lectura y escritura según la dependencia existente. Las escrituras exigen además rol Oftalmólogo, perfil activo y que la consulta pertenezca al usuario autenticado. El endpoint de contexto devuelve el perfil resumido del oftalmólogo propio, o `null` para otros roles/perfiles inactivos; evita depender de permisos de otros casos de uso.

Los endpoints nuevos usan el mismo `get_db` de `app.core.dependencies` que autentica el JWT y comprueba los permisos. FastAPI reutiliza una única sesión por petición CU19 para esas validaciones y la operación clínica, evitando reservar una segunda conexión. Control y bitácora conservan su transacción única; no se modifican las dependencias existentes.

`GET /controles` acepta `paciente_id`, `consulta_clinica_id` y `estado`; conserva controles cancelados y realizados y ordena por fecha e identificador descendentes. Permite recuperar el seguimiento para módulos futuros sin implementar notificaciones ni una interfaz móvil.

Creación:

```json
{
  "fecha_programada": "2026-10-10",
  "motivo": "Revisar evolución del tratamiento",
  "observaciones": "Traer los resultados de los exámenes"
}
```

Usar una fecha igual o posterior a hoy en `America/La_Paz` y a la fecha de la atención. El formato es estrictamente `YYYY-MM-DD`. La consulta debe tener una fecha válida y representar una atención previa; historial y paciente deben estar activos. El estado inicial es `PROGRAMADO` y lo asigna el servidor.

La actualización admite uno o varios de los campos `fecha_programada`, `motivo`, `observaciones` y `estado`. Rechaza cuerpos vacíos, campos ajenos y valores nulos obligatorios; `observaciones: null` limpia el texto. Estados permitidos: `PROGRAMADO`, `REALIZADO`, `CANCELADO`. Se puede completar o cancelar un control vencido sin cambiar su fecha, y corregir su texto. Cambiar la fecha o reabrirlo desde un estado final exige una fecha actual o futura. No hay eliminación física. Creación/actualización y bitácora se confirman en una sola transacción.

Los errores siguen el formato existente `{"detail": ...}` y los códigos `401`, `403`, `404`, `409`, `422` y `500`. Los errores nuevos de persistencia se traducen a mensajes de usuario sin detalles técnicos.

## Cambio de PostgreSQL

El SQL agrega `observaciones` a `control_medico`, registra «Programar controles médicos» en el módulo clínico existente y asigna `AMBAS` al rol Oftalmólogo existente. Es transaccional y admite repetición. Conserva las columnas y relaciones anteriores y no reemplaza asignaciones ya configuradas.

Desde la raíz del API, para verificar requisitos sin escribir:

```powershell
.\venv\Scripts\python.exe scripts\aplicar_sql_cu19.py
```

Para aplicar exactamente el archivo SQL a la conexión de `.env`:

```powershell
.\venv\Scripts\python.exe scripts\aplicar_sql_cu19.py --aplicar
```

También se puede ejecutar el archivo SQL completo en el editor SQL de PostgreSQL/Supabase. El script informa si existe `observaciones` y muestra exclusivamente los permisos nuevos, sin leer datos clínicos o imprimir credenciales. No incorpora un framework de migraciones ni ejecuta DDL al arrancar FastAPI.

## Flujo y prueba manual

1. Iniciar la API y Angular con la configuración habitual del proyecto. Mantener los valores existentes de `.env`; la configuración actual de FastAPI requiere también las variables Gmail, aunque CU19 no envía correos.
2. Iniciar sesión con un usuario de rol Oftalmólogo que tenga un perfil de oftalmólogo activo asociado, lectura del historial y el permiso nuevo de escritura. Tener solo el rol no sustituye el perfil clínico.
3. Abrir **Historial clínico**, seleccionar un paciente con una consulta existente del mismo oftalmólogo y localizar **Controles médicos posteriores → Atenciones previas**.
4. Pulsar **Programar control** en la atención deseada. También se puede entrar desde el menú **Programar controles médicos** o desde el enlace mostrado tras registrar una consulta. La ruta acepta `?paciente_id=...&consulta_id=...`, evitando volver a seleccionar el contexto.
5. Ingresar fecha actual/futura, motivo y observaciones opcionales. Guardar y comprobar el mensaje de éxito y la lista actualizada sin recargar la aplicación.
6. Filtrar por atención y revisar fecha, motivo, observaciones y estado. El historial conserva la información previa y agrega este panel.
7. Pulsar **Administrar control**, modificar los datos o marcar **Realizado/Cancelado** y guardar. Confirmar que continúa visible y que no se creó o modificó una cita.
8. Probar motivo vacío, fecha inválida/pasada y consulta inexistente usando Swagger: deben rechazarse sin guardar. Intentar enviar paciente/oftalmólogo/consulta en el cuerpo: se rechaza con `422`.
9. Probar sin token (`401`) y con un rol o permiso insuficiente (`403`). Otro oftalmólogo puede consultar conforme a su permiso de historial, pero no administrar una consulta ajena. La UI habilita las acciones usando el menú de permisos y el perfil autenticado; al terminar la carga explica si falta el perfil o el permiso, o si la atención pertenece a otro oftalmólogo o no es una atención previa válida.
10. Navegar por pacientes, historial, citas y consultas; comprobar los flujos existentes y las respuestas conocidas. Revisar la bitácora para las acciones `PROGRAMAR_CONTROL_MEDICO` y `ACTUALIZAR_CONTROL_MEDICO`.

## Arranque local si Supabase rechaza conexiones

El error observado `EMAXCONNSESSION: max clients reached in session mode ... pool_size: 15` significa que el pool de sesiones de Supabase está rechazando conexiones adicionales. Puede ser intermitente: peticiones consecutivas funcionan mientras varias peticiones simultáneas fallan. El registro confirma errores `500` al consultar usuario, consultas y controles; el navegador puede mostrar un fallo de conexión cuando una excepción anterior al endpoint no incluye cabeceras CORS.

Detener la API actual con **Ctrl + C** y, desde la raíz del API, arrancar con:

```powershell
.\venv\Scripts\python.exe -m uvicorn scripts.servidor_local_cu19:crear_app --factory --reload --host 127.0.0.1 --port 8000
```

Después recargar Angular con **Ctrl + F5**. Conservar una sola instancia local de la API y mantener el frontend en `http://localhost:4200`.

La factory usa la misma base y credenciales. Solo para una URL `postgresql+psycopg` del pooler de Supabase en puerto `5432`, configura un engine local en puerto `6543`, con `NullPool` y `prepare_threshold=None`. Esto evita retener conexiones inactivas y desactiva las sentencias preparadas incompatibles con ese modo. El cambio afecta al proceso iniciado con esta factory; `.env`, el valor de `DATABASE_URL` y el arranque habitual `app.main:app` se conservan. Otras conexiones mantienen su configuración. Referencias: [modos de conexión de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres) y [sentencias preparadas de Psycopg](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).

Si faltan variables Gmail, este arranque completa valores ficticios exclusivamente en su proceso. Conserva valores reales configurados; probar envío de correos requiere credenciales Gmail reales.

Se verificó esta conexión con `SELECT 1` y la presencia de `control_medico.observaciones`. En una instancia temporal, las cuatro peticiones simultáneas de menú, perfil, consultas de paciente y controles devolvieron `200` con CORS para `http://localhost:4200`. La instancia temporal se detuvo y las comprobaciones no insertaron datos clínicos. Este resultado verifica el arranque local; los límites de Supabase siguen dependiendo de los demás clientes y de la carga compartida.

## Verificación automatizada

Pruebas locales de CU19:

```powershell
.\venv\Scripts\python.exe scripts\probar_cu19.py
```

Para incluir las pruebas del arranque local:

```powershell
.\venv\Scripts\python.exe scripts\probar_cu19.py tests\test_cu19_controles_medicos.py tests\test_cu19_servidor_local.py -q
```

Suite del API:

```powershell
.\venv\Scripts\python.exe scripts\probar_cu19.py tests -q --tb=no
```

El ejecutor configura valores ficticios solo en su proceso y utiliza SQLite en memoria. Las pruebas bloquean cualquier conexión a la base compartida. No modifica `.env`. Requiere `pytest` y el cliente de pruebas HTTP, además de las dependencias del proyecto. En este entorno se instalaron las bibliotecas Google ya declaradas que faltaban en el venv y `httpx`; no se actualizó `requirements.txt`.

Desde la raíz web:

```powershell
npm.cmd test -- --watch=false
npm.cmd run build
```

Resultados API: **101 pruebas del flujo CU19 y 11 del arranque local aprobadas**. Antes de los cambios: **283 aprobadas y 44 fallidas**. Después: **395 aprobadas y las mismas 44 fallidas**, comparando los identificadores exactos; no aparecieron fallos nuevos. Los fallos anteriores de CU15/CU16 se deben al helper ausente `agenda_repo.obtener_oftalmologo_activo_por_usuario_id`; no se reparó porque queda fuera de CU19. Las pruebas nuevas comprueban también que las respuestas existentes de pacientes, historial, citas y consultas permanecen iguales después de registrar un control. Se verifican apertura y cierre de una única sesión para JWT, permiso y operación en los seis endpoints CU19, conservando la atomicidad del guardado; las pruebas de la factory usan mocks, sin conexiones de red.

Resultados web: **56/56 pruebas aprobadas** en 10 archivos (las 29 anteriores y 27 nuevas). Compilación de producción aprobada. Persisten únicamente los tres avisos anteriores de tamaño CSS en CU10/CU16; CU19 no agrega avisos. Se verifican creación/actualización, validaciones, permiso/propiedad, doble envío, carga pendiente, cambio de paciente, reintento, contexto desde CU15 y conservación del contenido de CU13 cuando falla CU19. La interfaz explica las restricciones de rol, perfil, escritura, responsable y fecha; las pruebas comprueban también que no muestra advertencias de perfil o permiso mientras sus respuestas siguen pendientes.

Estado SQL: **aplicado y verificado en PostgreSQL el 4 de octubre de 2026**. `observaciones` está disponible y el rol Oftalmólogo tiene la función «Programar controles médicos» con acción `AMBAS`; rol, función y acción están activos. La ejecución no insertó datos clínicos. Las pruebas funcionales usan datos locales en memoria y no insertan pacientes, consultas o controles en la base compartida.
