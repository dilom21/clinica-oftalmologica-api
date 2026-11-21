# CU41 — Correcciones funcionales y preparación de validación real

## A. Estado inicial

- Worktree: `C:\Users\Harold\Desktop\Clinica-Server\clinica-api-integracion-cu38-cu41`.
- Rama: `local-cu41-backend`, inicialmente limpia, sin staging ni modificaciones ajenas.
- Base: `cee43c9369e21ffa8de15a40bb4fa6c06adba784`.
- `dev-harold` local también en cee43c9; origin/dev-harold en c673e3f y main en 5060268.
- No se creó otra rama/worktree, no se hizo fetch innecesario ni operaciones destructivas.
- Modelo y configuración independiente conservados; deshabilitado por defecto.

## B. Causas comprobadas, correcciones y riesgos residuales

Las primeras **11 instancias de pruebas de reproducción fallaron** contra cee43c9.
Después de las correcciones pasan junto con las pruebas previas. Evidencia reproducible:
`tests/test_cu41_conversacion_regresion.py`.

| Defecto | Causa comprobada y responsable | Corrección y evidencia | Riesgo residual |
|---|---|---|---|
| 01 Respuesta genérica | `conversar`: rama `if tarjetas` reemplazaba incondicionalmente el texto final con una constante. `salida_segura` rechaza cifras/importes sin fuente, pero no era la causa de esa sustitución cuando había tarjetas. | `composicion.componer` narra nombres/precios/fechas de DTO autorizados; `test_catalogo_responde_precios_sin_descartar_datos` rechaza el precio 9999 inventado por el mock y conserva el real. | Plantillas locales por tipo pueden requerir pulido lingüístico, especialmente listas largas. No dependen del modelo para valores críticos. |
| 02 Duplicados | `tarjetas.append` acumulaba listado/detalles sin coalescer; firma de llamada detectaba solo repeticiones exactas, no solapamientos entre herramientas. | Unión estable de entidades por ID, coalescencia de no-nulos/Decimal, tarjetas complementarias conservadas. Caché por solicitud y detalles resueltos desde listado recuperado. Tests de unión, cache SQL y caso 150/60. | No hay caché entre solicitudes; es intencional para respetar cambios y aislamiento. |
| 03 Selección inconsistente | `ProveedorConversacional` no especificaba tool_choice, descripciones escuetas y el orquestador aceptaba respuesta sin herramientas sin comprobar si faltaban datos solicitados. | Descripciones semánticas, tool_choice dirigido en consultas claras, recuperación determinista de omisiones y una reparación acotada de campos obligatorios faltantes. Tests de paráfrasis, sinónimos y lenguaje fuera de heurísticas. | No existen trazas seguras del request original a DeepSeek: no se puede atribuir retrospectivamente la omisión a una decisión interna concreta del modelo. Lenguaje ambiguo sigue requiriendo modelo real/aclaración. |
| 04 Continuidad | Solo pares de texto y JSON público incrustado en prosa; sin estado explícito ni reutilización determinista para seguimientos. El test anterior verificaba que había fecha/ID en un payload, no que se recuperaran nuevos horarios. | Contexto público tipado con profesional/fecha/preferencia/hora/fase/referencias; actualizado solo con resultados autorizados. Seguimientos vuelven a CU09, incluido cambio de turno/modo/profesional/fecha. Tests de ocho horarios, selección 09:00 y ocupación intermedia. | No garantiza interpretación de todas las referencias del lenguaje natural; se debe repetir la batería real. No conserva resultados privados de citas. |
| 05 Voz poco útil | `_respuesta` copiaba/truncaba la misma constante a texto_voz; delegaba la lectura privada a una futura UI. | Voz compuesta desde DTO reales, precios BOB, fechas españolas, nombres y horarios; citas privadas narradas localmente. Resúmenes de listas públicas con cursor para continuar. | La pronunciación final depende del TTS de Flutter, aún no implementado. |
| 06 Markdown | `_respuesta` no tenía transformación de formato. | `para_voz` trata listas, énfasis, enlaces, HTML y espacios; conserva tildes, nombres y números. Tests de decimales, 09:30, fechas y nombres. | No es un motor universal de Markdown/SSML; no interpreta código ni ejecuta HTML. |
| 07 Falso positivo | Patrón original `tengo(?! (?:una? )?cita)` coincidía en «tengo alguna cita»; la palabra «médica» no era la condición responsable. | Se reconoce la construcción administrativa del verbo, sin saltarse los escaneos independientes de síntomas, secretos, identidad o datos financieros en el resto del mensaje. Mantiene rechazo general de «tengo hipertensión» y frases mixtas. | Continúa siendo detección conservadora con falsos positivos/negativos, no anonimización garantizada. No se habilita para pacientes reales. |
| 08 Tarjetas inexistentes | El fallback literal decía «consulta las tarjetas» aun con lista vacía y tipo texto. | Coherencia derivada del resultado efectivo; sin tarjetas no se remite a ellas. Error de selección es orientación; vacío válido conserva tarjeta vacía con explicación. Tests de omisión, aclaración y referencias falsas. | Una respuesta libre del modelo puede ser semánticamente imperfecta; los datos críticos requieren herramientas. |
| 09 Vacíos | Rama privada constante no inspeccionaba `citas=[]`; errores 404/409 y ausencia de datos se agrupaban como genéricos. | Composición por tipo, nulos y páginas. N+1 para `hay_mas`, filtro CU10 opcional desde fecha/hora actual, distinción de servicio/profesional/cita inexistente y horario ocupado. Tests de vacíos, null, páginas y conflictos. | No se afirma completitud sobre páginas; máximo de consulta y horizonte siguen acotados. |
| 10 Swagger | Tags del padre se heredaban junto al tag CU41. Faltaba `responses` para errores. No era registro doble de handlers. | Tag genérico queda solo en GET del módulo; CU41 tiene un único grupo, rutas conservadas. Errores 401–504 documentados con ejemplos y esquema 422 compatible con detail string/lista. Test cuenta rutas efectivas (FastAPI usa routers incluidos perezosos). | Cambios futuros de routers requieren mantener los tests de unicidad y OpenAPI. |

### Defecto adicional detectado con DeepSeek real (corregido)

| Defecto | Causa comprobada | Corrección y evidencia | Riesgo residual |
|---|---|---|---|
| Catálogo general vacío por filtro inventado | `conversar` fijaba `tool_choice` a `consultar_servicios` en la recuperación determinista, pero permitía al modelo generar `busqueda="oftalmología"` para una pregunta de listado. CU38 filtraba por ese término y devolvía cero. | `contexto.listado_general_servicios` detecta el listado inequívoco y `conversar` ejecuta localmente `consultar_servicios` con los defaults, sin llamada al proveedor ni filtro. Búsquedas, precios, detalle, referencias y paginación conservan el camino semántico/contextual. Se preservan referencias de servicios y se resuelven detalle/precio localmente cuando ya se mostraron. Tests: 27 en `test_cu41_catalogo.py`; validación real: pregunta original en 0 llamadas, 2 servicios, 100.50/null. | La detección de listado general es deliberadamente conservadora; una formulación muy ambigua sigue delegando al modelo o pidiendo aclaración. |

## C. Arquitectura resultante

1. Autenticación tenant/usuario/rol/perfil idéntica a la implementación inicial.
2. Minimización/rechazo local antes de cualquier llamada externa.
3. Estado público por conversación/propietario; historial y estado cuentan en memoria.
4. Interpretación libre con DeepSeek y ayudas léxicas/gramaticales para operaciones
   inequívocas. No existe una tabla de frases exactas ni un nuevo sistema de agenda.
5. Herramientas registradas, validación estricta y consultas a CU38/CU09/CU10.
6. Caché exclusivamente de la solicitud, deduplicación y narración determinista de hechos.
7. Confirmación atómica de historial administrativo minimizado + estado público al terminar.

El estado almacena referencias públicas, no copias de disponibilidad como garantías.
No guarda IDs/resultados de citas propias, contactos, precios ni datos médicos para el
proveedor. Las tarjetas privadas se redactan localmente y finalizan el turno sin llamada
posterior a DeepSeek. Referencias privadas explícitas se resuelven localmente mediante
CU10, verificando propiedad y sin incorporar el ID al historial externo.

La recuperación de argumentos se limita a una reparación de campos requeridos ausentes.
JSON inválido, claves adicionales (especialmente identidad), valores fuera de rango y
herramientas desconocidas siguen rechazados, sin desactivar seguridad. Repeticiones
de lectura pueden usar caché, pero todas cuentan en los presupuestos; bucles siguen
limitados por llamadas/iteraciones/tiempo. Nunca se cachea la verificación de una propuesta.

Se ampliaron opcionalmente consultas existentes (offset de profesionales y filtro
temporal exacto de citas) para paginar y consultar próximas citas sin cargar históricos
ni presentar citas de hoy ya pasadas como futuras. Defaults y endpoints CU09/CU10 previos
se conservan. No se modificó el algoritmo de slots ni el registro de citas.

### Observabilidad

Logger del caso de uso: evento operacional, nombre de herramienta **registrada**, motivo
categórico, número de invocaciones, iteración, duración y presencia booleana de contexto.
No hay mensajes, nombres de pacientes, fechas de citas, IDs de conversaciones/pacientes,
JWT, claves, argumentos, resultados ni razonamientos en estos diagnósticos.
`test_diagnostico_operacional_no_contiene_datos_sensibles` inspecciona los registros.

## D. Antes/después (todos ficticios)

Los fragmentos siguientes están cubiertos por aserciones de pruebas automatizadas con
herramientas reales sobre SQLite y proveedor simulado. No son resultados de DeepSeek real.

| Caso | Antes | Después verificado |
|---|---|---|
| Catálogo de demostración | Tres tarjetas repetidas y constante genérica | Una tarjeta: Consulta General Oftalmológica 150.00 y Medición de Lentes (Refracción) 60.00; voz incluye ambos nombres, importes BOB y duración de 30 minutos. |
| Institucional | Constante y aviso innecesario sobre horario | Consulta de dirección/teléfono devuelve Calle ficticia y 12345678. Solo pregunta de horario general obtiene «no está registrado». |
| Profesionales | Paráfrasis simple sin tarjetas | Preguntas por oftalmólogos/especialistas/facultativos generan lista real, nombre y especialidad verbalizados aunque el mock omita herramienta. |
| Disponibilidad | Datos solo visibles en tarjeta | Para Salet Ejemplo y fecha controlada se recuperan 16 slots; preferencia de mañana en voz devuelve ocho slots y resumen con inicios de 8 a 11:30. |
| Seguimiento | Pedía repetir datos o devolvía texto genérico | «A las nueve está bien» conserva profesional/fecha, reconsulta CU09 y prepara 09:00–09:30, reservada=false y requiere_confirmacion=true. |
| Cambio de disponibilidad | No estaba probado | Ocupación de 09:00 entre turnos produce «ya no está disponible», sin propuesta ni escritura. |
| Citas propias | Advertencia de privacidad para pregunta administrativa | «¿Tengo alguna cita médica programada próximamente?» devuelve únicamente citas propias y lectura local de fecha/hora, sin resultados en payload externo. |
| Vacío de citas | «Consulta tus datos en las tarjetas privadas» | «No encontré citas próximas programadas con los filtros aplicados. Si deseas, puedo ayudarte a consultar disponibilidad». |
| Null/página | No diferenciaba valores ni alcance | Precio null → «precio no registrado». Página vacía → no coincidencias en esa página, no inexistencia universal. |
| Markdown | Asteriscos/listas en la lectura | Voz conserva Refracción, 60,50, 09:30, nombres y fechas, sin viñetas ni marcadores visuales. |

El sobre HTTP y todos los discriminadores originales se mantienen. Los ejemplos JSON
definitivos y campos opcionales compatibles figuran en [CU41_BACKEND.md](CU41_BACKEND.md).

## E. Pruebas y limitaciones de evidencia

Validación final del 21 de noviembre de 2026, Python 3.12.13 del entorno existente,
SQLite aislado, credenciales ficticias y proveedor simulado; sin consumo real:

| Ejecución | Resultado |
|---|---|
| test_cu41_chatbot.py | 70 aprobadas |
| test_cu41_unidades.py | 46 aprobadas |
| test_cu41_conversacion_regresion.py (nuevas) | 56 aprobadas |
| test_cu41_catalogo.py (nuevas) | 27 aprobadas |
| **Total CU41** | **199 aprobadas** |
| test_cu34_estado_cuenta.py | 25 aprobadas |
| test_cu38_catalogo.py | 25 aprobadas |
| test_integracion_ia_deepseek.py | 60 aprobadas, transporte simulado |
| **python -m pytest -q** | **2170 aprobadas, 24 omitidas, 0 fallidas, 0 errores; 170 advertencias; 151,92 s** |
| python -m compileall -q app | Correcto |
| git diff --check y whitespace de archivos nuevos | Correcto |
| SHA-256 de cuatro SQL del manifiesto contra HEAD, índice y worktree | Los cuatro coinciden con el manifiesto original |
| Ejemplos JSON de documentación / ejemplos OpenAPI | 13 / 2 validados contra Pydantic |

Los conjuntos se solapan y no deben sumarse. De las 24 omisiones, 9 son históricas:
siete CU27 sin CU27_TEST_DATABASE_URL y dos CU32/CU33 sin CU32_CU33_AUDIT_URL; las
otras 15 son la batería real opt-in sin CU41_REAL_CONFIRM_COST. Las advertencias
son dos claves JWT HMAC deliberadamente cortas en pruebas y 154 adaptadores datetime /
14 date SQLite deprecados. No se cambió producción para ocultarlas.

La suite inicial tenía cobertura de permisos, límites y herramientas simuladas, pero no
exigía respuestas fundamentadas útiles ni voz accesible. Casi todas las selecciones de
herramientas ya venían correctas en el mock. Conservar fecha en texto no probaba continuidad
efectiva, y la expectativa institucional obligaba a avisar del horario aunque no se pidiera.

Cambios justificados en pruebas previas:

- El helper de pruebas de protocolo ahora pregunta «Necesito orientación», en lugar de
  solicitar catálogo mientras el mock arbitrariamente devolvía otra herramienta.
- Se acepta caché de repeticiones dentro del presupuesto; se sigue exigiendo 429 para
  bucles que exceden iteraciones y para límites de herramientas/contexto/memoria.
- La prueba de continuidad comprueba tarjeta/fecha/profesional/preferencia, no exige una
  llamada externa redundante solo para inspeccionar su payload.
- La prueba institucional no exige mencionar un horario no preguntado; conserva null en
  la tarjeta y una nueva prueba pregunta específicamente por el horario ausente.
- No se eliminaron tests ni se relajaron expectativas de autorización o confidencialidad.

## F. Validación real preparada, NO ejecutada

La conectividad sintética ya fue confirmada previamente por el usuario. No se repitió.

```powershell
python -m scripts.cu41_conversaciones_reales --confirm-cost --env-file "RUTA_AUTORIZADA" --max-calls 12
```

La batería usa el orquestador/adaptador reales con herramientas ficticias, sin DB,
pacientes ni HTTP del Backend. No sustituye Swagger con un tenant de prueba autorizado
ni pruebas de Flutter/dispositivo. No se activa CHATBOT_ENABLED en el sistema.

- Prueba equivalencias semánticas, también una pregunta fuera de las ayudas léxicas.
- Disponibilidad con fecha y lunes, seguimiento texto/voz, nueve, cambio de fecha.
- Fundamentos de catálogo, precios ficticios y consultas privadas sin filtración posterior.
- Introduce deliberadamente una llamada incompleta para probar reparación con el modelo.
- Aserciones por tipos/datos/propiedades, no por coincidencias exactas de prosa.
- Solo variables CHATBOT_DEEPSEEK_* seleccionadas; sin fallback a IA clínica.
- Máximo 12 llamadas por defecto, configurable entre 1 y 20; 256 tokens por llamada;
  presupuesto global 120 segundos, cero reintentos SDK. Coste de entrada también depende
  del contexto acotado. Debe autorizarse explícitamente antes de ejecutarlo.
- Imprime fases/checks y resultado operacional, nunca transcripciones, secretos o datos.
- El cableado del runner se ejecutó con proveedor simulado; NO acredita éxito real.

## G. Git y preservación

Verificación final: HEAD cee43c9369e21ffa8de15a40bb4fa6c06adba784; 13 archivos
rastreados modificados y cinco nuevos, sin staging. `git diff --check` correcto.
Archivos nuevos: services/contexto.py, services/composicion.py (bajo el módulo CU41),
tests/test_cu41_conversacion_regresion.py, scripts/cu41_conversaciones_reales.py y este informe.

Todos los cambios quedan sin commit/staging en el worktree de local-cu41-backend.
No se modificaron referencias de dev-harold, main, dev-salet ni remotos; sin push ni merge.
Los cambios son del módulo CU41, las ampliaciones opcionales de lectura descritas,
pruebas, documentación y la nueva batería opt-in. No se tocó .env original, settings,
modelo/API Keys, Flutter, Angular, Supabase, AWS, Stripe, Resend, SQL ni manifiesto.

## H. Conclusión y riesgos pendientes

El defecto de catálogo quedó corregido y validado de forma focalizada con DeepSeek real:
la pregunta original y sus variaciones se resuelven localmente, sin filtros inventados
y con los servicios/precios de CU38; las búsquedas específicas, el precio nulo, el
servicio inexistente, el catálogo sin activos y la continuidad texto/voz conservaron
su comportamiento. El listado general no consume proveedor; los seguimientos de
disponibilidad y la selección de horario tampoco.

No se declara aprobación de producción: la validación usó SQLite aislado y JWT de
fixture, no PostgreSQL ni login real de pacientes. Sigue pendiente la revisión de
pronunciación al integrar el TTS de Flutter y la evaluación de paráfrasis no previstas.
No se garantiza interpretación perfecta del lenguaje.

Siguen vigentes límites por proceso (sin coordinación de workers/réplicas), expiración
perezosa, timeouts de PostgreSQL necesarios para consultas síncronas, control de conexiones
en proxy/servidor y revisión de privacidad/consentimiento/retención. La detección automática
no es anonimización infalible. Horario general y FAQ institucionales faltantes deberán
ser proporcionados/validados por cada clínica. No inferirlos de los horarios médicos.
La moneda vigente es BOB conforme al dominio actual; si se incorpora multimoneda, el
catálogo deberá declararla explícitamente antes de cambiar su narración.
