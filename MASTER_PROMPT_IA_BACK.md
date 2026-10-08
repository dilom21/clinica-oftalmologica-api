# MASTER_PROMPT_IA_BACK.md

## 📍 DÓNDE EJECUTAR

**Repositorio:** `clinica-oftalmologica-api`  
**Ruta exacta:** `C:\SI2_Proyecto\clinica-oftalmologica-api`  
**Ubicación:** RAÍZ DEL REPOSITORIO BACKEND

NO ejecutar desde:

```text
C:\SI2_Proyecto
C:\SI2_Proyecto\clinica-oftalmologica-web
C:\SI2_Proyecto\clinica-oftalmologica-mobile
```

# IMPLEMENTACIÓN — IA BACKEND CON DEEPSEEK

Quiero que implementes el **primer bloque de Inteligencia Artificial del Sprint 2** usando la API de DeepSeek.

Antes de modificar código debes leer COMPLETOS:

1. `CONTEXTO_IA_BACK.md`
2. `MASTER_PROMPT_IA_BACK.md`
3. código LOCAL actual del repositorio

No asumas que GitHub está sincronizado con el estado local.

---

## 1. OBJETIVO EXACTO

Implementar solamente:

```text
A) Analizar consulta clínica existente con DeepSeek
B) Mejorar redacción de una descripción diagnóstica con DeepSeek
```

NO implementar frontend, voz, reportes o backup en este bloque.

---

## 2. REGLAS NO NEGOCIABLES

NO:

- modificar Supabase;
- ejecutar migraciones;
- crear tablas;
- modificar `.env`;
- escribir una API key real;
- hardcodear secretos;
- tocar frontend;
- tocar móvil;
- hacer commit;
- hacer push;
- hacer merge;
- reescribir CU15;
- reescribir CU16;
- registrar automáticamente un diagnóstico;
- generar recetas;
- prescribir tratamientos;
- enviar datos personales del paciente a DeepSeek;
- llamar realmente a DeepSeek desde los tests.

Mantener los cambios pequeños, auditables y coherentes con el backend actual.

---

## 3. INSPECCIÓN OBLIGATORIA

Antes de programar inspecciona, como mínimo:

```text
app/main.py
app/core/config.py
app/core/dependencies.py

app/modules/gestion_historial_clinico/models/models.py
app/modules/gestion_historial_clinico/schemas/schemas.py
app/modules/gestion_historial_clinico/repositories/repository.py
app/modules/gestion_historial_clinico/services/service.py
app/modules/gestion_historial_clinico/api/router.py

app/modules/gestion_agenda_citas/repositories/repository.py

app/modules/gestion_usuarios_seguridad/models/models.py
app/modules/gestion_usuarios_seguridad/repositories/repository.py

tests/test_cu15_consulta_clinica.py
tests/test_cu16_registrar_diagnostico.py

requirements.txt
```

Busca también cualquier implementación local previa que contenga:

```text
DeepSeek
deepseek
integracion_ia
IA
AI
```

Si existe algo parcial, reutiliza/corrige. No crees implementaciones paralelas.

---

## 4. ARQUITECTURA

Crear:

```text
app/modules/integracion_ia/
├── __init__.py
├── api/
│   ├── __init__.py
│   └── router.py
├── schemas/
│   ├── __init__.py
│   └── schemas.py
├── services/
│   ├── __init__.py
│   └── service.py
└── providers/
    ├── __init__.py
    └── deepseek_provider.py
```

Flujo:

```text
Router
→ IA Service
→ DeepSeek Provider
```

Para información clínica:

```text
IA Service
→ repository existente de historial clínico
```

Para perfil del oftalmólogo:

```text
IA Service
→ repository existente de agenda/citas
```

Para bitácora:

```text
IA Service
→ registrar_bitacora existente
```

No crear repository de IA si no existe persistencia propia.

---

## 5. CONFIGURACIÓN DEEPSEEK

Agregar en `app/core/config.py` soporte para:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
DEEPSEEK_TIMEOUT_SECONDS
```

Defaults:

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT_SECONDS=30
```

CRÍTICO:

```text
DEEPSEEK_API_KEY
```

puede ser `None`.

NO lanzar `RuntimeError` global si falta.

Los demás módulos deben iniciar normalmente sin clave.

Cuando se invoque IA sin clave:

```text
503
Servicio de IA no configurado
```

---

## 6. DEPENDENCIA

Agregar a `requirements.txt` el SDK Python `openai`, necesario para consumir la API compatible de DeepSeek.

Respeta el estilo de versiones del archivo actual.

No actualices masivamente otras librerías.

Si necesitas conocer la versión instalada luego de instalarla, usa el entorno virtual local y deja constancia en el reporte.

---

## 7. PROVIDER DEEPSEEK

Implementar en:

```text
app/modules/integracion_ia/providers/deepseek_provider.py
```

Usar conceptualmente:

```python
from openai import OpenAI

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=DEEPSEEK_TIMEOUT_SECONDS,
)
```

Modelo:

```text
DEEPSEEK_MODEL
```

Default:

```text
deepseek-flash
```

Para respuestas estructuradas usar Chat Completions y:

```python
response_format={"type": "json_object"}
```

El prompt debe mencionar explícitamente `json` y describir la forma esperada.

Configurar un `max_tokens` razonable.

No guardar en logs:

```text
API key
Authorization
prompt completo
respuesta raw completa
```

---

## 8. ERRORES DEL PROVIDER

Encapsula los errores.

El router no debe conocer clases internas del SDK.

Crea excepciones propias simples si ayudan, por ejemplo:

```text
IAConfiguracionError
IAProveedorNoDisponibleError
IARespuestaInvalidaError
```

Mapeo deseado:

```text
sin API key               → 503
timeout/conexión           → 503
rate limit/servicio        → 503
JSON vacío/inválido        → 502
estructura inválida        → 502
```

No exponer detalles sensibles.

---

## 9. SCHEMAS

Crear schemas Pydantic separados.

### Respuesta análisis

```text
AnalisisConsultaIARespuesta
```

Campos:

```text
resumen_clinico: str
hallazgos_relevantes: list[str]
aspectos_a_evaluar: list[str]
hipotesis_orientativas: list[str]
advertencia: str
```

Evitar listas absurdamente grandes. Validar que la estructura sea utilizable.

### Request mejora diagnóstica

```text
MejorarRedaccionDiagnosticoIARequest
```

Campos únicamente:

```text
nombre
descripcion
```

`extra="forbid"`.

`nombre`:

```text
trim
1..150
```

`descripcion`:

```text
trim
obligatoria
no vacía
longitud razonable
```

### Respuesta mejora

```text
MejorarRedaccionDiagnosticoIARespuesta
```

Campos:

```text
nombre
descripcion_original
descripcion_mejorada
advertencia
```

---

## 10. SEGURIDAD COMÚN

Reutiliza el patrón probado en CU15/CU16.

Para AMBOS endpoints:

```text
usuario autenticado
→ permiso
→ rol Oftalmólogo
→ perfil Oftalmólogo activo
→ consulta activa
→ consulta pertenece al Oftalmólogo
```

Usa:

```text
nombre_rol_actual(usuario)
```

y el repository ya utilizado por CU15:

```text
obtener_oftalmologo_activo_por_usuario_id
```

Para consulta:

```text
obtener_consulta_activa_por_id
```

No recibas `oftalmologo_id`.

---

## 11. PERMISO

Preparar:

```python
requerir_permiso(
    "Usar asistencia clínica IA",
    ACCION_LECTURA,
)
```

No crear la función en Supabase desde el agente.

Los tests deben crear el permiso necesario en su BD SQLite.

---

## 12. ENDPOINT ANALIZAR

Implementar:

```http
POST /ia/consultas/{consulta_id}/analizar
```

Response model:

```text
AnalisisConsultaIARespuesta
```

Status:

```text
200
```

No aceptar datos clínicos del body.

Recuperar desde BD:

```text
motivo_consulta
anamnesis
observaciones
```

Enviar SOLO esos campos al provider.

No recuperar ni enviar PII.

---

## 13. PROMPT DEL ANÁLISIS

System prompt:

```text
Eres un asistente de documentación y apoyo clínico para un oftalmólogo.
No sustituyes el criterio profesional.
No confirmes diagnósticos.
No prescribas medicamentos ni tratamientos.
No inventes datos.
Si la información es insuficiente, indícalo.
Devuelve exclusivamente json válido con la estructura solicitada.
```

User prompt debe contener:

```text
motivo_consulta
anamnesis
observaciones
```

y una estructura JSON de ejemplo con:

```text
resumen_clinico
hallazgos_relevantes
aspectos_a_evaluar
hipotesis_orientativas
advertencia
```

La advertencia debe dejar claro que el contenido necesita validación del oftalmólogo.

---

## 14. ENDPOINT MEJORAR REDACCIÓN

Implementar:

```http
POST /ia/consultas/{consulta_id}/mejorar-redaccion-diagnostico
```

Body:

```json
{
  "nombre": "...",
  "descripcion": "..."
}
```

Usar `consulta_id` para validar ownership antes de llamar al proveedor.

La IA debe:

```text
- conservar el nombre exactamente;
- reescribir solamente la descripción;
- no inventar hallazgos;
- no agregar diagnóstico;
- no agregar tratamiento;
- no registrar nada.
```

---

## 15. PROMPT DE MEJORA

System prompt similar a:

```text
Eres un asistente de redacción clínica.
Debes mejorar claridad, gramática y terminología de la descripción proporcionada.
No cambies el diagnóstico nombrado por el profesional.
No agregues información clínica que no exista en el texto original.
No diagnostiques.
No prescribas.
Devuelve exclusivamente json válido.
```

El JSON debe contener:

```text
descripcion_mejorada
```

El service construirá la respuesta final incluyendo:

```text
nombre original
descripcion original
descripcion mejorada
advertencia
```

No confiar en el modelo para devolver/modificar el nombre.

---

## 16. VALIDAR RESPUESTA DEL MODELO

Nunca retornar directamente:

```python
response.choices[0].message.content
```

Hacer:

```text
content
→ comprobar no vacío
→ json.loads
→ schema Pydantic
→ respuesta estable
```

Si falla:

```text
502
```

No propagar raw response.

---

## 17. BITÁCORA

Después de obtener y validar una respuesta IA exitosa:

### análisis

```text
accion = ANALIZAR_CONSULTA_IA
entidad_afectada = consulta_clinica
id_registro_afectado = consulta.id
descripcion = Análisis asistido por IA solicitado sobre consulta clínica
```

### redacción

```text
accion = MEJORAR_REDACCION_DIAGNOSTICO_IA
entidad_afectada = consulta_clinica
id_registro_afectado = consulta.id
descripcion = Redacción diagnóstica asistida por IA generada
```

NO guardar:

```text
prompt
respuesta IA
datos clínicos
API key
```

Usar `registrar_bitacora` existente.

Hacer commit de la bitácora solo después de IA exitosa.

---

## 18. NO MODIFICAR DATOS CLÍNICOS

Los endpoints de IA son auxiliares.

Debe demostrarse por tests que:

```text
consulta_clinica no cambia
diagnostico no se crea
diagnostico no se actualiza
```

El usuario decide posteriormente qué guardar mediante CU15/CU16.

---

## 19. TESTS

Crear:

```text
tests/test_integracion_ia_deepseek.py
```

NO usar la API real.

Mockear el provider.

Como mínimo cubrir:

```text
1. análisis exitoso
2. JSON estable
3. bitácora análisis
4. consulta inexistente/inactiva → 404
5. consulta ajena → 403
6. no oftalmólogo → 403
7. oftalmólogo sin perfil → 403
8. sin permiso → 403
9. provider no disponible → 503
10. provider devuelve respuesta inválida → 502

11. mejora redacción exitosa
12. conserva nombre
13. no crea diagnóstico
14. no modifica consulta
15. descripción vacía → 422
16. campos extra → 422
17. consulta ajena → 403
18. bitácora redacción
19. JWT requerido
```

No hagas asserts dependientes de una API externa.

---

## 20. REGISTRAR ROUTER

Agregar router IA en:

```text
app/main.py
```

Sin eliminar routers existentes.

Tags sugeridos:

```text
Inteligencia Artificial
```

Prefix:

```text
/ia
```

---

## 21. PRUEBAS

Primero:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_integracion_ia_deepseek.py -q
```

Luego:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Si una prueba anterior falla por un cambio tuyo, corrige la regresión.

No "arregles" tests existentes debilitando asserts.

---

## 22. NO PROBAR CON API REAL TODAVÍA

En este bloque NO consumas saldo/tokens de DeepSeek.

La prueba real con `DEEPSEEK_API_KEY` se hará después de que yo revise tu implementación.

---

## 23. REPORTE OBLIGATORIO AL FINAL

Cuando termines, NO hagas commit.

Devuélveme un reporte con:

```text
1. ESTADO: COMPLETADO / BLOQUEADO

2. ARCHIVOS CREADOS

3. ARCHIVOS MODIFICADOS

4. DEPENDENCIAS AGREGADAS
   - nombre
   - versión

5. ENDPOINTS IMPLEMENTADOS

6. SEGURIDAD
   - JWT
   - permiso
   - rol
   - ownership

7. DEEPSEEK
   - modelo configurado
   - base URL
   - JSON Output
   - manejo de errores
   - confirmar que NO se usó una API key real en tests

8. DATOS ENVIADOS AL MODELO
   - listar exactamente qué campos se envían
   - confirmar qué PII NO se envía

9. BITÁCORA
   - acciones implementadas

10. TESTS
    - comando
    - cantidad passed/failed

11. SUITE COMPLETA
    - cantidad passed/failed

12. DEUDAS O DECISIONES
    - cualquier diferencia respecto al prompt

13. GIT STATUS
    - resumen, sin commit/push
```

Si encuentras una incompatibilidad real con el código local, detente antes de hacer un cambio destructivo y repórtala.
