# CU41 — Chatbot administrativo (desarrollo local)

## Base y alcance

Implementación inicial confirmada en `cee43c9369e21ffa8de15a40bb4fa6c06adba784`
(sobre la base integrada `c673e3f`). Correcciones funcionales pendientes de revisión
en la rama `local-cu41-backend`, sin commit final. Módulo existente `notificaciones_interaccion_chatbot`.
Diagnóstico de los diez defectos y validación de correcciones: [CU41_CORRECCIONES.md](CU41_CORRECCIONES.md).
El archivo histórico vacío `casos_uso/cu38_interactuar_chatbot.py` se conserva sin
cambios; el orquestador vigente es `cu41_interactuar_chatbot.py`. Los archivos de
notificaciones y valoración no se modifican. No hay tablas nuevas ni persistencia.

Es una implementación para evaluación local, **no una habilitación de producción**.
No diagnostica, interpreta exámenes, prescribe ni ejecuta operaciones de escritura.
Tampoco registra, reserva, cancela o reprograma citas.

## Componentes

- `settings.py`: configuración independiente, validada e inmutable, SecretStr.
  `app/core/config.py` expone `CHATBOT_CONFIG` después del mecanismo dotenv existente.
- `api/chatbot.py`: JWT tenant explícito antes de resolución clínica; usuario, rol y
  perfil activos. Actor mínimo, fingerprint SHA-256 del JWT, nunca JWT en contexto.
  Cuerpo HTTP limitado a 20 000 bytes antes de parsear JSON.
- `services/memoria.py`: almacenamiento acotado, TTL, exclusión por conversación,
  rate limits por identidad/tenant/proceso y presupuesto global de llamadas IA.
- `services/proveedor.py`: AsyncOpenAI Chat Completions, SDK instalado 3.26.0,
  cero reintentos, reasoning desactivado, proyección explícita sin chain-of-thought.
- `services/privacidad.py`: rechazo local conservador de PII, secretos, consultas
  médicas y patrones de abuso. No es anonimización infalible.
- `services/herramientas.py`: ocho herramientas de lectura explícitamente registradas,
  argumentos Pydantic `extra=forbid`, fechas/IDs/horas validados, proyecciones tipadas.
- `services/contexto.py`: estado público tipado (profesional, fecha, preferencia,
  hora, fase, referencias y cursores públicos), compartido por texto/voz.
- `services/composicion.py`: narración local fundamentada, voz sin Markdown,
  deduplicación por identidad y resultados vacíos coherentes.
- `casos_uso/cu41_interactuar_chatbot.py`: contexto multivuelta, protocolo tool_call_id,
  presupuestos, respuestas locales y tarjetas. No SQL/HTTP arbitrario ni código dinámico.

No se reutilizan prompts, credenciales ni `generar_json` de `integracion_ia`.
Sus endpoints y comportamiento permanecen intactos. Router registrado desde `app/main.py`.

## Configuración y límites efectivos

Todos los nombres siguientes tienen prefijo `CHATBOT_`; ejemplos seguros en `.env.example`.

| Sufijo | Default | Rango |
|---|---:|---|
| ENABLED | false | booleano |
| DEEPSEEK_API_KEY | vacío | independiente; sin fallback a DEEPSEEK_API_KEY |
| DEEPSEEK_BASE_URL | https://api.deepseek.com | solo este origen HTTPS |
| DEEPSEEK_MODEL | deepseek-flash | cadena 1–80 |
| DEEPSEEK_TIMEOUT_SECONDS | 30 | 1–60 |
| MAX_OUTPUT_TOKENS | 512 | 32–2048 por llamada |
| MAX_HISTORY_MESSAGES | 12 | 2–40; pares completos usuario/asistente |
| MAX_TOOL_ITERATIONS | 5 | 1–8; incluye llamada final |
| MAX_TOOL_CALLS | 10 | 1–16 acumuladas por mensaje |
| MAX_MESSAGE_LENGTH | 1000 | 10–4000 caracteres |
| RATE_LIMIT_PER_MINUTE | 10 | 1–60, usuario/paciente/tenant; crear y enviar |
| TENANT_RATE_LIMIT_PER_MINUTE | 100 | 1–1000, entre pacientes |
| GLOBAL_RATE_LIMIT_PER_MINUTE | 500 | 1–5000, solicitudes y llamadas IA (ventanas separadas) |
| REQUEST_TIMEOUT_SECONDS | 60 | 1–120 |
| MAX_CONTEXT_BYTES | 32768 | 8192–131072; JSON UTF-8, incluye prompt y herramientas |
| MAX_TOOL_ARGUMENTS_BYTES | 2048 | 128–4096 por herramienta |
| MAX_TOOL_RESULT_BYTES | 8192 | 256–16384 por tarjeta |
| SESSION_TTL_SECONDS | 1800 | 1–7200 de inactividad |
| MAX_SESSIONS | 1000 | 1–2000 por proceso |
| MAX_SESSIONS_PER_PATIENT | 5 | 1–10 entre tokens del mismo usuario/tenant |
| MAX_MEMORY_BYTES | 16777216 | 8192–67108864, historial + estado público serializados + margen por sesión |
| MAX_CONCURRENT_REQUESTS | 8 | 1–32 por proceso |

El historial ocupa como máximo la mitad de MAX_CONTEXT_BYTES y el estado público
un cuarto; prompt/estado/historial/herramientas juntos siguen sujetos al límite completo.
Ventanas de consumo con máximo 10 000 claves;
no se desalojan claves activas para reiniciar límites. Crear otra conversación o
renovar token no reinicia límites de usuario/tenant. Hasta cinco llamadas IA y diez
herramientas por solicitud con los defaults; máximo de salida teórico 5 × 512 tokens.

El deadline asyncio cancela esperas al proveedor, su timeout por llamada es menor al
presupuesto restante. Consultas SQL reutilizadas son síncronas: el timeout no puede
interrumpir una consulta del driver que bloquee el event loop; se requieren timeouts
DB de despliegue y revisión de concurrencia antes de habilitar públicamente.
Se finaliza mediante rollback la transacción de autenticación y cada lectura antes
de esperar a DeepSeek; nunca se mantiene una transacción PostgreSQL durante la IA.

## Memoria y despliegue

Propietario = tenant_database_id, empresa_id, usuario_id, paciente_id y hash del JWT.
UUID opaco, conversación ajena e inexistente indistinguibles (404). Un nuevo JWT
invalida el acceso al contexto del anterior. Texto y voz usan el mismo UUID.
TTL con purga perezosa al operar, sin persistencia. DELETE borra el contexto.
Locks no bloqueantes: segunda operación simultánea devuelve 409. Los fallos y la
cancelación liberan el lock y no confirman turnos incompletos.

El estado no conserva slots como garantía: cada seguimiento de disponibilidad y
cada preparación de propuesta vuelve a CU09. Cambiar de tema descarta la selección
activa; una fecha o profesional corregidos reemplazan la selección anterior solo
con datos validados. Referencias privadas explícitas a citas se resuelven localmente,
se validan con CU10 y no se incluyen en historial externo ni en estado público.

**Todo es local a un proceso**: memoria, rate limits y concurrencia no se comparten
entre workers/réplicas ni sobreviven reinicios. No usar esta implementación como un
control distribuido de consumo. La activación pública depende de una estrategia de
sesión/despliegue revisada. El límite de bytes aproxima datos serializados, no RSS
exacto del intérprete. Cada cuerpo HTTP se limita a 20 000 bytes, pero la concurrencia
del orquestador no limita las conexiones que todavía estén enviando cuerpo o autenticándose;
esos controles corresponden también al servidor/proxy de despliegue. Las ventanas de
consumo tienen un límite independiente de 10 000 claves y expiración de 60 segundos.

## Herramientas

Identidad siempre desde Actor validado, nunca argumentos del modelo o cliente.
Todos los esquemas rechazan campos adicionales; no hay herramientas de escritura.

| Herramienta | Argumentos | Servicio / resultado |
|---|---|---|
| consultar_clinica | {} | TenantControlPlaneRepository.get_empresa_by_codigo del código autenticado; nombre comercial, teléfono, correo y dirección de empresa activa |
| consultar_servicios | busqueda hasta 150, offset 0–1000, limite 1–10 (default 5) | CU38 catalogo.listar; solo activos; tarjetas con precio Decimal o null |
| consultar_detalle_servicio | servicio_id positivo | CU38 catalogo.detalle; inactivo/inexistente sin resultados |
| consultar_profesionales | limite 1–10 (default 5), offset 0–1000 | CU09 listar_oftalmologos_activos; ID, nombre público y especialidad |
| consultar_disponibilidad | oftalmologo_id, fecha ISO, franja opcional manana/tarde | CU09 consultar_disponibilidad; una fecha, hoy a hoy+90 días, máximo 48 slots reales; mañana antes de las 12, tarde desde las 12 |
| preparar_solicitud_cita | oftalmologo_id, fecha ISO, hora_inicio local sin zona/segundos | CU09 para verificar cabida de duración CU10; propuesta no reservada, sin motivo, paciente_id ni servicio_id |
| consultar_mis_citas | limite 1–10 (default 5) | CU10 listar_citas con usuario validado; próximas PROGRAMADA/CONFIRMADA/EN_ESPERA desde fecha/hora local actual, SQL LIMIT, sin motivo/observaciones/datos de paciente |
| orientar_navegacion | destino enum, cita_id opcional | CU10 obtener_cita verifica propiedad cuando hay ID; acción local sin ejecutar cambios |

La ampliación mínima de CU09/CU10 añade límites/filtros opcionales a sus capas de
servicio/repositorio para evitar cargar históricos sin límite; sus rutas y defaults
anteriores no cambian. No hay un segundo algoritmo de disponibilidad ni catálogo.
La fecha local usa APP_TIMEZONE validada, por defecto America/La_Paz; no existe aquí
una zona específica por empresa. No se inventan horarios generales de apertura.

Resultados enviados al modelo: únicamente IDs/nombres públicos de servicios y
profesionales, intervalos sin ocupantes y propuestas de horario. Descripciones,
precios, contactos institucionales y citas privadas NO se envían. Los datos críticos
se narran localmente además de mostrarse en tarjetas, no como cifras inventadas por el modelo.
La moneda hablada es BOB (bolivianos), conforme al dominio vigente: CU34 soporta BOB
y PlanPago tiene restricción BOB. Catálogo no define otra moneda; no se usa la moneda
configurable del procesador Stripe como si fuera moneda del catálogo.
Resultados privados finalizan el turno localmente, sin otra llamada al proveedor.
El historial guarda solo texto administrativo y contexto público mínimo; jamás
tarjetas privadas, motivos de citas, pacientes ni reasoning. Errores de negocio
404/409 producen un resultado de herramienta genérico sin datos internos.

Selección híbrida: DeepSeek interpreta lenguaje/contexto libre y genera Function
Calling; para consultas inequívocas se orienta tool_choice a la herramienta fuente.
Si omite una herramienta obvia, FastAPI recupera la lectura; las continuaciones
inequívocas usan estado validado y consultas frescas. Argumentos requeridos ausentes
admiten una reparación acotada; JSON inválido, claves de identidad, herramientas
desconocidas y valores fuera de rango siguen rechazados. Todos los intentos cuentan
en presupuestos. Listado/detalle y repeticiones reutilizan resultados únicamente dentro
de la misma solicitud; nunca comparten caché entre usuarios, tenants o turnos.

El listado general inequívoco de servicios (`listado_general_servicios`) se ejecuta
localmente con los argumentos predeterminados de `consultar_servicios`, sin llamada al
proveedor y sin filtros inventados. Búsquedas por nombre, precio específico, detalle,
referencias a servicios ya mostrados y paginación conservan el camino semántico o
contextual. Un resultado vacío no se amplía automáticamente a todo el catálogo. Los
mensajes distinguen catálogo sin servicios activos, búsqueda sin coincidencias y página
sin resultados; «en esta página» solo se usa cuando hubo paginación.

## Privacidad y condiciones de habilitación

No se envían ORM, JWT, claves, conexiones, historias clínicas ni información financiera.
Detección/rechazo local de entradas sensibles antes de proveedor y memoria; segunda
revisión del historial antes de cada llamada. Datos del catálogo no redefinen permisos:
sin eval/exec/import dinámico, funciones desconocidas rechazadas y argumentos estrictos.
Respuestas numéricas o sospechosas sin respaldo se sustituyen por orientación local.
Urgencias reconocidas se orientan localmente a atención urgente, sin diagnóstico.

La detección lingüística tiene falsos positivos y falsos negativos: nombres aislados,
síntomas indirectos o ataques novedosos pueden evadirla. No constituye garantía de
anonimización ni de exactitud semántica del modelo. Por ello ENABLED=false por defecto.
Antes de pacientes reales: revisión de privacidad/consentimiento, políticas de retención
del proveedor, pruebas adversariales y conversación real autorizada, estrategia distribuida,
timeouts DB, límites de conexiones HTTP y controles operacionales. No habilitar logging DEBUG de OpenAI/HTTP que registre
payloads. Este módulo no escribe conversaciones en logs ni bitácoras.

Información pendiente por clínica: horario general, políticas administrativas y FAQ
institucionales validadas. Actualmente se usan solo campos institucionales existentes;
el bot informa ausencia de otras fuentes, no ofrece FAQ clínicas inventadas.

## Contrato HTTP para Flutter

Authorization: Bearer JWT tenant activo. Content-Type: application/json.
OpenAPI es la referencia ejecutable de tipos y discriminadores. Campos adicionales
prohibidos. No se aceptan historial, rol, paciente_id, tenant_id ni empresa_id.

### Crear / finalizar

`POST /notificaciones-chatbot/conversaciones` con `{}` → 201:

```json
{"conversacion_id":"123e4567-e89b-42d3-a456-426614174000","expira_en_segundos":1800}
```

`DELETE /notificaciones-chatbot/conversaciones/{id}` → 204 sin cuerpo.
Reiniciar = finalizar + crear. No restaura consumo por minuto.

### Mensajes y respuesta natural

`POST /notificaciones-chatbot/conversaciones/{id}/mensajes`:

```json
{"mensaje":"Quiero una cita para el lunes","modalidad":"texto"}
```

Respuesta 200 (ejemplo de proveedor simulado):

```json
{"conversacion_id":"123e4567-e89b-42d3-a456-426614174000","respuesta":"¿Prefieres por la mañana o por la tarde?","texto_voz":"¿Prefieres por la mañana o por la tarde?","tipo":"texto","tarjetas":[]}
```

Continuar con `{"mensaje":"Por la mañana","modalidad":"voz"}` sobre el mismo UUID.
La modalidad no crea otro contexto. `texto_voz` ya contiene información útil de los
resultados, sin Markdown: nombres, precios, horarios y citas propias autorizadas.
No se corta indiscriminadamente a 700 caracteres: las listas públicas largas se
resumen por entidades/franjas y se ofrece continuar; las citas privadas se narran
localmente sin enviarlas al proveedor. No se genera audio, STT ni TTS en Backend.
Flutter puede leer directamente texto_voz y mostrar respuesta/tarjetas; no debe
reenviar tarjetas privadas al modelo para redactar la lectura.

### Tarjetas discriminadas

La respuesta usa el mismo sobre, `tipo=datos`, más `tarjetas` con los objetos siguientes.
Todos los ejemplos son ficticios. El precio es string decimal o null, nunca cero sustituto.

```json
{"tipo":"servicios","privacidad":"publica","servicios":[{"id":1,"nombre":"Consulta ficticia","descripcion":"Descripción del catálogo","precio":"100.50","duracion_minutos":30}]}
```

```json
{"tipo":"profesionales","privacidad":"publica","profesionales":[{"id":1,"nombre":"Profesional Ficticio","especialidad":null}]}
```

```json
{"tipo":"disponibilidad","privacidad":"publica","oftalmologo_id":1,"fecha":"2026-12-15","intervalos":[{"hora_inicio":"09:00:00","hora_fin":"09:30:00"}],"franja":"manana","profesional":{"id":1,"nombre":"Profesional Ficticio","especialidad":null}}
```

```json
{"tipo":"propuesta_cita","privacidad":"publica","oftalmologo_id":1,"fecha":"2026-12-15","hora_inicio":"09:00:00","hora_fin":"09:30:00","reservada":false,"requiere_confirmacion":true}
```

Flutter abrirá CU10 con oftalmologo_id, fecha, hora_inicio. Hora_fin es informativa,
calculada por CU10. **CitaCreate actual exige paciente_id**: el formulario existente
debe resolverlo desde el perfil autenticado, no del modelo. No enviar servicio_id.
Motivo/observaciones y confirmación se capturan en CU10, no mediante IA. La disponibilidad
se verifica nuevamente al registrar. El chatbot nunca llama registrar_cita.

```json
{"tipo":"citas_propias","privacidad":"privada","citas":[{"id":1,"oftalmologo_id":1,"fecha":"2026-12-15","hora_inicio":"10:00:00","hora_fin":"10:30:00","estado":"PROGRAMADA"}]}
```

```json
{"tipo":"navegacion","privacidad":"privada","destino":"reprogramar_cita","cita_id":1}
```

Destinos permitidos: catalogo, agendar_cita, mis_citas, detalle_cita, reprogramar_cita,
cancelar_cita. Los tres últimos requieren cita propia. Son intenciones de navegación,
no rutas ni URLs, y nunca significan que se ejecutó la operación.

```json
{"tipo":"clinica","privacidad":"publica","nombre":"Clínica ficticia","telefono":null,"correo":null,"direccion":null,"horario_general":null}
```

Sobre privado completo:

```json
{"conversacion_id":"123e4567-e89b-42d3-a456-426614174000","respuesta":"No encontré citas próximas programadas con los filtros aplicados. Si deseas, puedo ayudarte a consultar disponibilidad.","texto_voz":"No encontré citas próximas programadas con los filtros aplicados. Si deseas, puedo ayudarte a consultar disponibilidad.","tipo":"datos","tarjetas":[{"tipo":"citas_propias","privacidad":"privada","citas":[],"hay_mas":false}]}
```

`tipo` del sobre: texto (conversación sin herramientas), datos (incluye un resultado
válido, aunque esté vacío), orientacion (aclaración o selección no disponible) o privacidad.
En rechazo local, tipo=privacidad, tarjetas=[]; entrada no persistida ni transmitida.
Frontend debe renderizar tarjetas como datos, no interpretarlas como instrucciones ni
reenviarlas a la IA. No conservar tarjetas de sesiones previas ni registrar payloads.

### Extensiones compatibles de tarjetas

Campos originales y discriminadores conservados. Se añaden campos con default null:
servicios: busqueda/offset/hay_mas; profesionales: offset/hay_mas;
disponibilidad: profesional/franja; propuesta: profesional; citas_propias: hay_mas.
profesional usa el objeto público {id,nombre,especialidad}. franja es manana/tarde/null.
N+1 registros determinan hay_mas sin consultar todo el historial. null significa que
no se conoce completitud (p. ej. detalle individual o combinación de consultas),
no equivale a false. Un conjunto recuperado/paginado nunca se anuncia como catálogo
o lista de citas completos. Entidades de servicios/profesionales se deduplican por ID;
las tarjetas complementarias de otros tipos se conservan. Decimales y nulos originales
no se reemplazan con cifras generadas. Con más de diez entidades se mantienen tarjetas
no solapadas de hasta diez, respetando el contrato y el orden de recuperación.

### Continuidad verificable (datos ficticios)

Disponibilidad de Salet Ejemplo para una fecha autorizada → preferencia de mañana
en voz → «a las nueve está bien» conserva profesional/fecha, consulta CU09 de nuevo
y prepara 09:00–09:30 con reservada=false y requiere_confirmacion=true. Si otro
paciente ocupa ese horario entre turnos, responde que ya no está disponible; no
crea la cita. Casos ejecutados con reloj controlado en test_cu41_conversacion_regresion.py.
Los ejemplos completos de antes/después están en CU41_CORRECCIONES.md.

Sobres ilustrativos compatibles con los esquemas (no transcripciones del modelo real):

```json
{"conversacion_id":"123e4567-e89b-42d3-a456-426614174000","respuesta":"Consulta ficticia: 150 bolivianos; duración de 30 minutos.","texto_voz":"Consulta ficticia: 150 bolivianos; duración de 30 minutos.","tipo":"datos","tarjetas":[{"tipo":"servicios","privacidad":"publica","servicios":[{"id":1,"nombre":"Consulta ficticia","descripcion":null,"precio":"150.00","duracion_minutos":30}],"busqueda":"","offset":0,"hay_mas":false}]}
```

```json
{"conversacion_id":"123e4567-e89b-42d3-a456-426614174000","respuesta":"Preparé una propuesta para el 15 de diciembre de 2026, de 9 a 9:30 con Profesional Ficticio. Aún no está reservada: abre el formulario de citas y confirma. La disponibilidad se verificará de nuevo.","texto_voz":"Preparé una propuesta para el 15 de diciembre de 2026, de 9 a 9:30 con Profesional Ficticio. Aún no está reservada: abre el formulario de citas y confirma. La disponibilidad se verificará de nuevo.","tipo":"datos","tarjetas":[{"tipo":"propuesta_cita","privacidad":"publica","oftalmologo_id":1,"fecha":"2026-12-15","hora_inicio":"09:00:00","hora_fin":"09:30:00","reservada":false,"requiere_confirmacion":true,"profesional":{"id":1,"nombre":"Profesional Ficticio","especialidad":null}}]}
```

### Errores

Formato habitual FastAPI `{"detail":"mensaje seguro"}`; 422 de Pydantic usa lista de
errores. 401 JWT inválido/legacy/SaaS/expirado o usuario inactivo; 403 rol/perfil inactivo;
404 perfil ausente o conversación ajena/inexistente/expirada; 409 conversación ocupada;
413 cuerpo excesivo; 422 esquema/herramienta inválida; 429 límite de consumo/contexto/
herramientas/capacidad (Retry-After=60 para consumo por ventana); 502 respuesta de proveedor
inválida o resultado demasiado grande; 503 deshabilitado, sin clave, error de cuota,
autenticación o indisponibilidad del proveedor; 504 deadline total agotado.

## Pruebas y comprobación real opcional

`tests/test_cu41_chatbot.py`: HTTP, autenticación real, bases SQLite independientes por
tenant, herramientas reales CU38/CU09/CU10, mock conversacional, privacidad y solo SELECT.
`tests/test_cu41_unidades.py`: configuración, TTL, memoria, concurrencia, rate limits,
presupuestos, SDK OpenAI real con httpx.MockTransport (sin red ni consumo).
`tests/test_cu41_conversacion_regresion.py`: defectos manuales reproducidos inicialmente
en rojo, omisiones/errores del modelo, composición, memoria pública, voz, nulos,
paginación, deduplicación, actualización de disponibilidad, cierre y privacidad mixta.
`tests/test_cu41_catalogo.py`: listado general determinista sin filtros inventados,
búsquedas/precios/referencias, paginación, catálogo sin activos y mensajes de vacío.
`tests/test_cu41_validacion_real.py`: opt-in facturable con DeepSeek real y endpoints
reales sobre SQLite aislado; sin `CU41_REAL_CONFIRM_COST=1` permanece omitido.

La conexión sintética previa fue confirmada por el usuario. No se volvió a consumir
la API real en las correcciones. Batería conversacional pendiente, SOLO tras autorización:

```powershell
python -m scripts.cu41_conversaciones_reales --confirm-cost --env-file "RUTA_AUTORIZADA" --max-calls 12
```

Modelo DeepSeek real + herramientas completamente ficticias, sin BD. Máximo 12 llamadas
(parámetro entre 1 y 20), 256 tokens de salida por llamada, cero reintentos y 120 segundos
para la batería; cubre equivalencias, lunes/fecha explícita, seguimiento voz/texto,
selección de las nueve, cambio de fecha, datos fundamentados, citas privadas y reparación
de una llamada deliberadamente incompleta. Comprueba propiedades/datos, no frases exactas.
Su funcionamiento se prueba con proveedor simulado, lo que NO acredita calidad del
modelo real. No imprime transcripciones, claves, resultados privados ni razonamientos.
No carga .env original al Backend: selecciona solo CHATBOT_DEEPSEEK_* para el adaptador;
el bootstrap usa configuración ficticia y desactiva dotenv antes de importar core/config.

El script anterior de conectividad sigue disponible, pero no hace falta repetirlo:

```powershell
python -m scripts.cu41_smoke_deepseek --confirm-cost --env-file "RUTA_AUTORIZADA"
```

El script selecciona únicamente CHATBOT_DEEPSEEK_API_KEY/BASE_URL/MODEL/TIMEOUT_SECONDS,
sin cargar otro secreto en la configuración. No copia .env ni habilita CHATBOT_ENABLED.
Datos totalmente ficticios, herramienta simulada sin DB, máximo dos llamadas de 64 tokens,
timeout total 35 segundos. Sin --confirm-cost no hace llamadas. No imprime clave ni respuesta
del proveedor. También permite variables de entorno, sin archivo. No sustituye la revisión
de seguridad ni una validación de conversaciones reales autorizada con datos ficticios.

## Validación vigente de correcciones (21 de noviembre de 2026)

| Conjunto | Resultado |
|---|---|
| CU41 previo HTTP/unidades | 70 + 46 aprobadas |
| Regresiones conversacionales | 56 aprobadas |
| Nuevas regresiones de catálogo | 27 aprobadas |
| **CU41 total** | **199 aprobadas** |
| CU34 / CU38 / IA existente (sin red) | 25 / 25 / 60 aprobadas |
| **Suite general** | **2170 aprobadas, 24 omitidas, 0 fallos, 0 errores; 170 advertencias; 151,92 s** |
| compileall app / diff --check | Correctos |
| Cuatro checksums SQL del manifiesto contra HEAD/índice/worktree | Coinciden con originales |
| Validación real focalizada | 12 llamadas dentro del presupuesto; catálogo corregido |

Conjuntos solapados, no sumarlos. De las 24 omisiones, 9 son históricas (7 CU27 y 2
CU32/CU33) y 15 corresponden a la batería real opt-in sin `CU41_REAL_CONFIRM_COST=1`.
Advertencias: dos claves HMAC de pruebas y adaptadores SQLite deprecados.
HEAD sigue en cee43c9, rama local-cu41-backend, sin staging/commit/push/merge.
La validación real usó SQLite aislado y JWT de fixture; no sustituye PostgreSQL ni
login real de pacientes. Detalle en CU41_VALIDACION_REAL.md.

## Validación histórica inicial (anterior a cee43c9; no describe el Git ni los totales vigentes)

Python 3.12.13 del entorno existente `clinica-backend`, dependencias sin cambios.
Configuración temporal con DATABASE_URL SQLite en memoria y credenciales ficticias,
sin copiar ni modificar .env original. Ninguna llamada facturable al proveedor.

| Conjunto | Resultado |
|---|---|
| CU41 HTTP/integración/seguridad | 70 passed |
| CU41 unidades/configuración/memoria/SDK simulado | 46 passed |
| CU34 específico | 25 passed |
| CU38 específico | 25 passed |
| IA clínica existente específico | 60 passed |
| Regresión adicional (auth, tenancy, pagos, planes/cuotas, servicios, migrador, restore, provisioning) | 507 passed, 2 warnings |
| Suite general final | **2087 passed, 9 skipped, 170 warnings; 0 failed, 0 errors** |
| python -m compileall -q app | Correcto |
| git diff --check | Correcto; archivos nuevos también inspeccionados por whitespace |
| Ejemplos JSON de este documento | 11 validados contra Pydantic |

Los conjuntos se solapan; no deben sumarse. La regresión de agenda/citas también se
ejecutó de forma focalizada y está incluida en la suite general. Las nueve omisiones:
siete pruebas PostgreSQL CU27 sin CU27_TEST_DATABASE_URL y dos CU32/CU33 sin
CU32_CU33_AUDIT_URL. Advertencias: dos claves HMAC deliberadamente cortas en pruebas
de JWT, 154 adaptadores datetime y 14 date SQLite deprecados bajo Python 3.12.

Los cuatro checksums originales del manifiesto coinciden con bytes de HEAD, índice y
worktree. Sin diferencias en CU34, CU38, integracion_ia, manifiesto ni SQL histórico.
No se añadieron dependencias, ni se tocó Flutter/Angular, Supabase, Stripe, Resend o AWS.

Git: HEAD sigue en c673e3f, rama local-cu41-backend, sin commits ni staging. Siete archivos
rastreados modificados (configuración, main, router del módulo y ampliaciones opcionales
de consulta en agenda/citas), doce archivos nuevos (ocho Python del módulo, dos pruebas,
este documento y script smoke). main, dev-harold, origin/dev-harold y la rama de integración
anterior mantienen sus referencias. No hubo push ni merge a otra rama.

Conclusión: implementación local disponible para revisión e integración Flutter en pruebas.
Habilitación con pacientes reales pendiente de los controles de privacidad, despliegue,
contenido institucional y validación real autorizada descritos arriba.
