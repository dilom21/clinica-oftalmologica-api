# CONTEXTO_VOZ_IA_BACK.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Paso:** 6A — Backend de Voz + IA para reportes  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

Este bloque prepara la parte backend de la experiencia por voz.

IMPORTANTE:

- La captura de voz y la transcripción voz→texto se hará en Angular con la Web Speech API del navegador.
- FastAPI NO recibirá audio en este bloque.
- DeepSeek NO transcribirá audio.
- El backend recibirá únicamente TEXTO ya transcrito y lo interpretará para construir una configuración segura de reporte dinámico.

---

## 2. Objetivo

Implementar un endpoint IA para interpretar órdenes de reportes expresadas en lenguaje natural.

Ejemplo de entrada:

```text
Muéstrame las consultas de octubre con fecha, paciente,
oftalmólogo y motivo, ordenadas de más reciente a más antigua.
```

Respuesta conceptual:

```json
{
  "dataset": "consultas_clinicas",
  "columnas": [
    "fecha_consulta",
    "paciente",
    "oftalmologo",
    "motivo_consulta"
  ],
  "filtros": [
    {
      "campo": "fecha_consulta",
      "operador": "between",
      "valor": ["2026-10-01", "2026-10-31"]
    }
  ],
  "orden": [
    {
      "campo": "fecha_consulta",
      "direccion": "desc"
    }
  ],
  "limit": 50,
  "requiere_aclaracion": false,
  "pregunta_aclaracion": null,
  "accion_sugerida": "previsualizar",
  "formato_sugerido": null
}
```

La IA NO ejecuta SQL.
La IA NO genera reportes directamente.
La IA NO exporta automáticamente.
La IA solo propone una configuración válida.

Después, el frontend reutilizará el motor de reportes existente para previsualizar o exportar.

---

## 3. Infraestructura ya disponible

### DeepSeek

Ya existe:

```text
app/modules/integracion_ia/
```

con provider DeepSeek y configuración:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
DEEPSEEK_TIMEOUT_SECONDS
```

REUTILIZARLO.

No crear otro cliente DeepSeek.

### Reportes

Ya existe:

```text
app/modules/gestion_reportes/
```

con:

```text
registry/whitelist
datasets
campos permitidos
operadores
ordenamiento
schemas
preview
exportación
```

REUTILIZAR ese registry como fuente de verdad.

No hardcodear una segunda lista paralela de datasets/campos si puede obtenerse del registry existente.

---

## 4. Arquitectura

Flujo:

```text
Angular SpeechRecognition
        ↓
texto transcrito
        ↓
POST /ia/reportes/interpretar
        ↓
FastAPI
        ↓
catalogo/registry seguro
        ↓
DeepSeek
        ↓
JSON lógico
        ↓
validación Pydantic + registry
        ↓
configuración de reporte
        ↓
Angular
        ↓
motor existente /reportes/dinamicos/previsualizar
```

Nunca:

```text
voz → DeepSeek → SQL
```

---

## 5. Endpoint

Implementar:

```http
POST /ia/reportes/interpretar
```

Body:

```json
{
  "texto": "Muéstrame las consultas de octubre..."
}
```

Validación:

```text
texto:
- obligatorio
- trim
- no vacío
- longitud máxima razonable, sugerida 1000
- extra="forbid"
```

---

## 6. Seguridad

El endpoint es administrativo.

Debe exigir:

```text
JWT válido
rol Administrador
permiso "Generar reportes" con ACCION_LECTURA
```

NO reutilizar la restricción clínica de Oftalmólogo del endpoint de IA clínica.

No necesita un permiso nuevo en Supabase.

---

## 7. Catálogo enviado al modelo

El prompt debe incluir un resumen CONTROLADO del registry:

```text
datasets permitidos
campos permitidos
labels
tipos
operadores permitidos
campos ordenables
```

NO enviar:

```text
filas de BD
nombres de pacientes
diagnósticos almacenados
correos
CI
teléfonos
bitácora
password_hash
token_hash
```

El único contenido variable proveniente del usuario será el texto transcrito.

---

## 8. Privacidad

El sistema NO debe enviar registros de la clínica a DeepSeek para interpretar una orden.

Solo:

```text
texto del comando
+
metadata del catálogo
```

Agregar una advertencia en el system prompt:

```text
No necesitas datos reales de pacientes.
No inventes registros.
Solo transforma la intención del usuario a una configuración lógica.
```

Para la demo, evitar dictar nombres, CI, teléfonos o datos identificables.

---

## 9. Respuesta IA

Crear schema estable, por ejemplo:

```text
InterpretacionReporteIARespuesta
```

Campos:

```text
dataset: str
columnas: list[str]
filtros: list[FiltroReporte]
orden: list[OrdenReporte]
limit: int
requiere_aclaracion: bool
pregunta_aclaracion: str | None
accion_sugerida: Literal["previsualizar", "exportar"]
formato_sugerido: Literal["xlsx", "pdf", "csv", "html"] | None
```

`accion_sugerida` es solo una sugerencia.

El backend NO debe ejecutar la exportación.

Si la intención no es suficientemente clara:

```json
{
  "requiere_aclaracion": true,
  "pregunta_aclaracion": "¿Qué conjunto de datos quieres consultar?",
  "...": "configuración mínima segura"
}
```

La forma exacta puede ajustarse al código local, pero debe permanecer estable y validada.

---

## 10. Regla crítica: doble validación

No confiar en el JSON del modelo.

Flujo obligatorio:

```text
DeepSeek JSON
→ json.loads
→ Pydantic
→ validar dataset contra registry
→ validar columnas
→ validar filtros
→ validar operador por campo
→ validar orden
→ validar máximo 3 órdenes
→ validar limit 1..200
→ respuesta
```

Si la IA propone:

```text
dataset inexistente
columna inválida
operador inválido
orden inválido
```

NO corregir silenciosamente inventando.

Opciones permitidas:

1. devolver una respuesta con `requiere_aclaracion=true`; o
2. devolver error controlado 502 por respuesta inválida del proveedor.

Preferencia: si el modelo devolvió JSON válido pero configuración inválida, convertirlo en una aclaración segura si resulta sencillo.

---

## 11. Fechas relativas

Interpretar expresiones como:

```text
hoy
ayer
este mes
octubre
este año
```

DeepSeek puede proponer fechas ISO.

El prompt debe incluir la FECHA ACTUAL como contexto, obtenida en backend.

No usar una fecha hardcodeada.

No pedir a la IA que suponga timezone si no es necesario.

Para rangos usar:

```text
between
```

con valores ISO:

```text
YYYY-MM-DD
```

---

## 12. Acción y formato sugerido

Ejemplos:

```text
"muéstrame..." → accion_sugerida = previsualizar

"exporta en PDF..." → accion_sugerida = exportar
formato_sugerido = pdf
```

Aun así:

- backend NO exporta;
- frontend debe pedir confirmación o ejecutar explícitamente según UX definida después.

---

## 13. Prompt del sistema

Debe incluir reglas como:

```text
Eres un traductor de lenguaje natural a configuración de reportes.

NO generes SQL.
NO inventes datasets ni campos.
Solo puedes usar las claves proporcionadas en el catálogo.
Usa únicamente operadores permitidos.
Máximo 3 criterios de orden.
Máximo limit 200.
Devuelve exclusivamente JSON válido.
Si la intención es ambigua, marca requiere_aclaracion=true.
No necesitas datos reales de pacientes.
```

---

## 14. DeepSeek JSON Output

Reutilizar provider actual y:

```text
response_format={"type":"json_object"}
```

No llamar realmente a DeepSeek en tests.

---

## 15. Bitácora

Registrar solo si la interpretación finaliza correctamente:

```text
INTERPRETAR_REPORTE_IA
```

Entidad:

```text
reporte
```

Descripción genérica:

```text
Interpretación de consulta de reporte mediante IA
```

NO registrar:

```text
texto dictado
prompt
respuesta IA
dataset completo
filtros
datos clínicos
```

---

## 16. Manejo de errores

Reutilizar excepciones/mapper del módulo IA actual.

Esperado:

```text
sin DEEPSEEK_API_KEY   → 503
provider no disponible → 503
JSON inválido          → 502
config IA inválida     → 502 o aclaración segura
texto inválido         → 422
sin permiso            → 403
no autenticado         → 401/403 según patrón actual
```

No exponer respuesta raw.

---

## 17. Tests

Crear o extender tests, sugerencia:

```text
tests/test_ia_reportes_voz.py
```

NO llamar Internet.

Mock DeepSeek.

Cubrir mínimo:

1. comando simple de pacientes;
2. consultas con columnas;
3. filtro fecha `between`;
4. orden desc;
5. `accion_sugerida=previsualizar`;
6. petición "exporta PDF" produce formato sugerido pdf;
7. dataset inventado por IA no pasa;
8. columna inventada no pasa;
9. operador inválido no pasa;
10. >3 órdenes no pasa;
11. limit >200 no pasa;
12. JSON inválido → 502;
13. provider caído → 503;
14. texto vacío → 422;
15. extra field → 422;
16. no admin → 403;
17. sin permiso → 403;
18. JWT requerido;
19. bitácora correcta;
20. bitácora NO contiene texto dictado;
21. prompt NO contiene filas/datos reales;
22. registry real es fuente de verdad.

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ia_reportes_voz.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

---

## 18. No hacer

NO implementar todavía:

- captura de micrófono;
- SpeechRecognition;
- cambios Angular;
- audio upload;
- speech-to-text backend;
- TTS;
- ejecución automática del reporte;
- SQL generado por IA;
- backup;
- restore;
- cambios Supabase;
- tablas;
- migraciones;
- commit;
- push;
- merge.

---

## 19. Criterio de terminado

```text
[ ] endpoint /ia/reportes/interpretar
[ ] DeepSeek provider reutilizado
[ ] report registry reutilizado
[ ] no SQL IA
[ ] no datos de BD enviados al modelo
[ ] schema estable
[ ] fechas relativas contextualizadas
[ ] accion/formato sugeridos
[ ] doble validación
[ ] seguridad admin + Generar reportes
[ ] bitácora
[ ] tests mock
[ ] suite completa verde
[ ] reporte final
```
