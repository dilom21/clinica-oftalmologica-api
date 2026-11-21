# CU41 — Validación funcional con DeepSeek real (segunda ronda)

Fecha: 21 de noviembre de 2026. Rama `local-cu41-backend`, HEAD
`cee43c9369e21ffa8de15a40bb4fa6c06adba784`, sin commit/push/merge.

## Entorno y componentes

- Endpoints, dependencias FastAPI, JWT de fixture, orquestador CU41, composición,
  herramientas CU38/CU09/CU10 y validaciones de privacidad: **reales**.
- Persistencia: **SQLite en memoria aislado por tenant** (ALPHA/BETA), datos ficticios
  del fixture existente. No se usó PostgreSQL ni producción.
- Proveedor: **DeepSeek real** (`deepseek-flash`), clave independiente
  `CHATBOT_DEEPSEEK_*` leída del `.env` original sin copiarlo ni imprimirlo.
- Simulado: únicamente el reloj/calendario del fixture y los datos ficticios.
- Autenticación: JWT HS256 generado por el fixture de pruebas con secreto de pruebas;
  no se ejecutó login real. Perfil: paciente Ana (usuario 10, paciente 1).
- No se consumió `DEEPSEEK_API_KEY` de la integración clínica.

Procedimiento reproducible:

```powershell
$env:CU41_REAL_CONFIRM_COST = "1"
python -m pytest tests/test_cu41_validacion_real.py -q -s
```

Sin `CU41_REAL_CONFIRM_COST=1` todas las pruebas reales se omiten. Presupuesto propio
de fase B: 48 llamadas; no se incrementa automáticamente.

## Fase A — batería conversacional sintética

`python -m scripts.cu41_conversaciones_reales --confirm-cost --env-file "..." --max-calls 12`

- Equivalencias de profesionales: correctas.
- **La ejecución se detuvo en el bloque de continuidad con una `HTTPException` no
  clasificada**, antes de catálogo/privacidad. No se imprimieron payloads.
- El mismo flujo se reprodujo de forma focalizada (4 llamadas) y **pasó completo**:
  disponibilidad → mañana en voz → “a las nueve” → propuesta → cambio de fecha.
- Hipótesis: fallo transitorio del proveedor o de red; no reproducible. No se pudo
  confirmar la causa sin traza segura. Consumo de fase A: 7 llamadas reconstruidas
  + 4 del diagnóstico focal = **11 de 12**.
- Limitación: la batería sintética no valida base de datos ni endpoints; eso se cubrió
  en fase B.

## Fase B — endpoints reales con proveedor real

Resultados consolidados (datos ficticios):

| # | Escenario | HTTP | Llamadas | ms | Resultado |
|---|---|---|---:|---:|---|
| 01 | Conversación inicial | 200 | 1 | 1995 | Aprobado |
| 02 | Catálogo canónico | 200 | 1 | 1108/1246 | **Fallido**: filtro inventado |
| 02c | Catálogo sin filtros | 200 | 1 | 1664 | Aprobado |
| 03 | Institucional | 200 | 1 | 1214 | Aprobado |
| 03b | Horario general ausente | 200 | 1 | 1278 | Aprobado |
| 04 | Profesionales A/B/C | 200 | 1+1+1(+1 contexto) | 1067–1637 | Aprobado |
| 05 | Disponibilidad | 200 | 2 | 2632 | Aprobado |
| 06 | Preferencia mañana (voz) | 200 | 0 | 13 | Aprobado |
| 07 | Selección 09:00 (voz) | 200 | 0 | 12 | Aprobado |
| 08 | Cambio de fecha | 200 | 2 | 2700 | Aprobado |
| 09 | Citas propias A/B | 200 | 1+1 | 1007–1436 | Aprobado |
| 10 | Citas vacías | 200 | 1 | 1029 | Aprobado |
| 11 | Prompt injection | 200 | 0 | 8 | Aprobado (rechazo local) |
| 12 | Finalización + 404 | 404 | 0 | — | Aprobado |
| 13 | Disponibilidad vacía | 200 | 2 | 2599 | Aprobado |
| 14 | Precio ausente | 200 | 1 | 1043 | Aprobado |
| 15 | Referencia “martes” sin ISO | 200 | 2 | 2255 | Aprobado |
| 16 | Búsqueda sin coincidencias | 200 | 2 | 3209 | Aprobado |
| 17 | Catálogo sin activos | 200 | 1 | 1055 | Aprobado (narración mejorable) |
| 18 | Swagger | — | 0 | — | Aprobado |

Consumo de fase B: **29 llamadas de 48**. Global: **40 de 60** (11 de fase A +
29 de fase B). No se aumentó el presupuesto.

### Defecto real corregido: filtro no solicitado en catálogo

- Mensaje: “¿Qué servicios oftalmológicos ofrece esta clínica y cuáles son sus precios?”
- Esperado: tarjeta con servicios activos 1 y 3, precios 100.50 y null.
- Obtenido entonces: `tipo=datos`, tarjeta `servicios` vacía, `busqueda="oftalmología"`,
  respuesta “No encontré coincidencias para esa búsqueda en esta página.”
- Reproducido 3 veces. Con “sin aplicar ningún filtro” el mismo endpoint devolvía
  ambos servicios y voz correcta (`busqueda=""`).
- Causa comprobada: en `cu41_interactuar_chatbot.conversar`, la recuperación
  determinista fijaba `tool_choice` a `consultar_servicios`, pero dejaba que el modelo
  generara los argumentos. DeepSeek añadía `busqueda="oftalmología"`; CU38 filtraba por
  ese literal y devolvía cero. No era un fallo de CU38, de deduplicación ni de Decimal.
- Corrección aplicada: `contexto.listado_general_servicios` detecta el listado
  inequívoco y el orquestador ejecuta localmente `consultar_servicios` con los defaults,
  sin llamada al proveedor ni filtro. Búsquedas, precios, detalle, referencias y
  paginación conservan el camino semántico/contextual. Los mensajes de vacío distinguen
  catálogo sin activos, búsqueda sin coincidencias y página sin resultados.

### Ronda final focalizada (12 llamadas)

| Escenario | Llamadas | Resultado |
|---|---:|---|
| Saludo inicial | 1 | 1,34 s; presentación breve, sin Markdown, 179 caracteres de voz |
| Catálogo original | 0 | Servicios 1 y 3; 100.50 y null; sin filtro; coincide con CU38 |
| “¿Qué servicios tienen?” | 0 | Mismos dos servicios, sin duplicados |
| “¿Tienen consulta general oftalmológica?” | 2 | Servicio 1; `busqueda="consulta"` |
| “¿Cuánto cuesta el Servicio 100% especial?” | 0 | Detalle local por referencia; precio null |
| “¿Tienen cirugía láser de retina?” | 3 | Vacío correcto; “no encontré servicios que coincidan”; sin página |
| Catálogo sin activos | 0 | “No encontré servicios activos en el catálogo de esta clínica.” |
| Disponibilidad + mañana + 09:00 | 2 + 0 + 0 | 18 → 6 intervalos; propuesta 09:00–09:30; sin escritura |

Consumo de esta ronda: **12 de 12** llamadas autorizadas. Global acumulado:
**52 de 60** (40 previas + 12). No se aumentó el presupuesto.
Un intento intermedio de servicio inexistente devolvió 503 tras 21,6 s (mapeo de error
de proveedor); al repetirlo en proceso nuevo respondió correctamente. Se registra como
inestabilidad transitoria del proveedor, no como defecto funcional demostrado.

## Evidencia de continuidad

Conversación ficticia (texto → voz):

1. “¿Qué horarios tiene ALPHA Profesional para el lunes 2026-11-23?” →
   18 intervalos reales; ocupados 10:00 y 11:00 excluidos; 2 llamadas.
2. “Prefiero por la mañana, ¿qué horarios tienes?” (voz) → 6 intervalos matutinos,
   mismo profesional/fecha, **0 llamadas** (continuación local), tarjeta `franja=manana`.
3. “A las nueve está bien” (voz) → propuesta 09:00–09:30, `reservada=false`,
   `requiere_confirmacion=true`, **0 llamadas** y **0 escrituras**.
4. “Mejor para el martes” → fecha 2026-11-24, disponibilidad reconsultada, propuesta
   anterior descartada; sin horarios porque el fixture solo configura lunes.

`count(cita)` y `max(id)` no cambiaron antes/después. La propuesta no registra cita.

## Seguridad verificada

- JWT tenant de fixture, paciente activo. Conversación de otro usuario y de BETA: 404
  sin llamada. Conversación finalizada: 404 sin llamada; contexto eliminado.
- Inyección “ignora tus instrucciones…”: rechazo local, 0 llamadas, sin tarjetas.
- SQL, credenciales, cambio de empresa y orden de escritura: bloqueo local verificado
  sin consumo. Registro con solo ocho herramientas de lectura; no existe herramienta
  de registrar/cancelar/reprogramar.
- Payloads externos inspeccionados por marcadores: **0** con datos privados. Los turnos
  de citas propias finalizan localmente y no envían resultados al modelo.
- No se imprimieron cabeceras, JWT, claves ni payloads completos.

## Rendimiento

- HTTP conversacional: mínimo **1007 ms**, mediana **1436 ms**, máximo **3209 ms**
  (n=21). Todas por debajo de 5 s. Respuestas locales (continuación/finalización):
  8–13 ms.
- SDK DeepSeek en el lote de 16 llamadas: 986–1610 ms; 21 684 tokens de entrada,
  896 de salida, 16 000 de caché.
- No se calculó importe facturado: no se verificó tarifa vigente.

## Conclusión

Clasificación: **2. Backend listo para comenzar Flutter con observaciones menores
documentadas**. El defecto de catálogo quedó corregido y validado focalizadamente con
DeepSeek real: la pregunta original y sus variaciones se resuelven localmente, sin
filtros inventados y con datos contrastados con CU38; las búsquedas específicas, el
precio nulo, el servicio inexistente, el catálogo sin activos y la continuidad
texto/voz conservaron su comportamiento.

Observaciones: la validación usa SQLite aislado y JWT de fixture, no PostgreSQL ni
login real de pacientes; un intento aislado devolvió 503 transitorio del proveedor;
la pronunciación final depende del TTS de Flutter. No se declara el chatbot listo
para producción.
