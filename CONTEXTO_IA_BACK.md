# CONTEXTO_IA_BACK.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Integración:** Inteligencia Artificial clínica con DeepSeek  
**Capa:** Backend API  
**Tecnología:** FastAPI + SQLAlchemy + Pydantic + PostgreSQL/Supabase + DeepSeek API  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

> Este documento define el contexto funcional y técnico del primer bloque de IA. La implementación debe inspeccionar primero el estado LOCAL del repositorio. No asumir que GitHub refleja exactamente el código local.

---

## 2. Objetivo del bloque

Integrar DeepSeek en el backend para agregar asistencia clínica controlada sobre los casos de uso ya implementados:

- **CU15 – Registrar consulta clínica**
- **CU16 – Registrar diagnóstico**

La IA NO reemplaza al oftalmólogo y NO debe registrar diagnósticos, tratamientos, recetas ni decisiones clínicas automáticamente.

En este bloque se implementarán únicamente dos funciones:

1. **Analizar una consulta clínica existente**
   - resumen clínico;
   - hallazgos relevantes;
   - aspectos que el profesional puede revisar;
   - hipótesis orientativas;
   - advertencia de uso profesional.

2. **Mejorar la redacción de una descripción diagnóstica**
   - recibe el nombre y una descripción escrita por el oftalmólogo;
   - devuelve solamente una versión mejor redactada de la descripción;
   - NO cambia el nombre del diagnóstico;
   - NO registra nada en `diagnostico`;
   - NO inventa un diagnóstico nuevo.

La integración de voz se hará en un bloque posterior.

---

## 3. Estado previo confirmado del backend

Arquitectura actual:

```text
Router
→ Service
→ Repository
→ SQLAlchemy
→ PostgreSQL/Supabase
```

Autenticación/autorización:

```text
JWT
→ obtener_usuario_actual
→ requerir_permiso(...)
→ Usuario
→ perfil Oftalmólogo activo
```

CU15 ya implementado:

```text
POST /historial-clinico/consultas
```

Reglas relevantes de CU15:

- solo rol Oftalmólogo;
- `oftalmologo_id` se deriva del usuario autenticado;
- valida historial;
- valida paciente activo;
- valida cita opcional;
- consulta + actualización de cita + bitácora en una transacción.

CU16 ya implementado:

```text
POST /historial-clinico/consultas/{consulta_id}/diagnosticos
GET  /historial-clinico/consultas/{consulta_id}/diagnosticos
```

Reglas relevantes de CU16:

- solo rol Oftalmólogo;
- consulta activa;
- consulta debe pertenecer al oftalmólogo autenticado;
- diagnóstico + bitácora en una transacción.

La IA debe reutilizar estas reglas y NO duplicar seguridad de forma inconsistente.

---

## 4. Archivos actuales importantes

Inspeccionar como mínimo:

```text
app/main.py

app/core/config.py
app/core/dependencies.py
app/core/security.py

app/database/session.py

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
```

No reescribir CU15 ni CU16.

---

## 5. Proveedor de IA

Proveedor:

```text
DeepSeek API
```

Base URL:

```text
https://api.deepseek.com
```

Modelo por defecto:

```text
deepseek-flash
```

Debe quedar configurable por variable de entorno.

La integración preferida es mediante el SDK Python compatible con OpenAI:

```python
from openai import OpenAI

client = OpenAI(
    api_key=...,
    base_url="https://api.deepseek.com",
)
```

No poner la API key en:

- código;
- frontend Angular;
- repositorio;
- tests;
- logs;
- mensajes de error;
- bitácora.

---

## 6. Variables de entorno

Agregar soporte en `app/core/config.py` para:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
DEEPSEEK_TIMEOUT_SECONDS
```

Valores por defecto permitidos:

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_TIMEOUT_SECONDS=30
```

`DEEPSEEK_API_KEY` NO debe causar un `RuntimeError` al importar toda la aplicación.

Motivo:

- los tests generales no deben romperse si no tienen clave;
- otros módulos deben seguir funcionando sin IA;
- la falta de clave debe producir un error controlado solamente al invocar endpoints de IA.

Respuesta sugerida si falta configuración:

```text
503 Service Unavailable
```

Detalle genérico:

```text
Servicio de IA no configurado
```

No revelar secretos.

---

## 7. Dependencia Python

El proyecto actual no incluye el SDK `openai`.

Agregar la dependencia necesaria a `requirements.txt` respetando el estilo actual del proyecto.

No eliminar ni actualizar masivamente otras dependencias.

---

## 8. Nuevo módulo

Crear un módulo separado y coherente con la arquitectura:

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

No es necesario crear una tabla nueva para IA en este bloque.

No crear repository de IA si no existe persistencia propia.

Para recuperar la consulta clínica, reutilizar el repository existente de historial clínico.

---

## 9. Endpoint A — Analizar consulta con IA

Contrato preferido:

```http
POST /ia/consultas/{consulta_id}/analizar
```

No necesita body clínico.

El backend debe recuperar la consulta por ID desde PostgreSQL y usar exclusivamente:

```text
motivo_consulta
anamnesis
observaciones
```

No enviar a DeepSeek:

```text
nombres del paciente
apellidos
CI
correo
teléfono
dirección
usuario_id
paciente_id
historial_clinico_id
oftalmologo_id
cita_id
```

No consultar datos personales solo para enriquecer el prompt.

### Validaciones

Antes de llamar a DeepSeek:

1. JWT válido;
2. permiso correspondiente;
3. usuario con rol Oftalmólogo;
4. perfil de oftalmólogo activo;
5. consulta existente y activa;
6. consulta pertenece al oftalmólogo autenticado.

Si la consulta no pertenece al usuario:

```text
403
```

Si no existe o está inactiva:

```text
404
```

---

## 10. Respuesta estructurada del análisis

La respuesta HTTP debe ser validada por Pydantic y tener una estructura estable similar a:

```json
{
  "resumen_clinico": "Texto...",
  "hallazgos_relevantes": [
    "Hallazgo 1",
    "Hallazgo 2"
  ],
  "aspectos_a_evaluar": [
    "Aspecto 1",
    "Aspecto 2"
  ],
  "hipotesis_orientativas": [
    "Hipótesis 1"
  ],
  "advertencia": "Contenido generado por IA para apoyo profesional. Debe ser validado por el oftalmólogo."
}
```

La IA NO debe afirmar que un diagnóstico está confirmado.

Las hipótesis deben estar presentadas como posibilidades para valoración profesional.

No generar:

- dosis;
- recetas;
- órdenes médicas automáticas;
- tratamiento automático;
- diagnóstico definitivo;
- decisiones autónomas.

---

## 11. DeepSeek JSON Output

Para obtener una respuesta estructurada:

```text
response_format = {"type": "json_object"}
```

El prompt debe pedir explícitamente una respuesta en **json** y mostrar la forma esperada.

Luego:

```text
DeepSeek response
→ contenido string JSON
→ json.loads(...)
→ Pydantic
→ respuesta API
```

No devolver directamente texto sin validar proveniente del modelo.

Si DeepSeek devuelve:

- contenido vacío;
- JSON inválido;
- estructura inválida;

el backend debe devolver un error controlado, preferentemente:

```text
502 Bad Gateway
```

sin exponer respuesta interna completa del proveedor.

---

## 12. Prompt clínico del endpoint A

El system prompt debe imponer, como mínimo:

```text
- Eres un asistente de documentación clínica para un oftalmólogo.
- Tu función es apoyar y resumir, no sustituir al profesional.
- No presentes diagnósticos como confirmados.
- No prescribas tratamientos ni medicamentos.
- Trabaja solo con la información recibida.
- Si faltan datos, indícalo.
- Devuelve exclusivamente JSON válido.
```

El user prompt debe contener solamente los campos clínicos permitidos.

Evitar datos identificables.

---

## 13. Endpoint B — Mejorar redacción de diagnóstico

Contrato preferido:

```http
POST /ia/consultas/{consulta_id}/mejorar-redaccion-diagnostico
```

Body conceptual:

```json
{
  "nombre": "Miopía",
  "descripcion": "paciente ve borroso de lejos ambos ojos"
}
```

Validaciones sugeridas:

```text
nombre:
- obligatorio
- trim
- 1..150

descripcion:
- obligatorio
- trim
- no vacío
- longitud razonable

extra="forbid"
```

No aceptar:

```text
diagnostico_id
consulta_clinica_id
oftalmologo_id
usuario_id
estado
fecha_diagnostico
```

El `consulta_id` viene de la URL.

---

## 14. Regla crítica del endpoint B

La IA solamente puede **mejorar la redacción** de la descripción proporcionada.

No debe:

- cambiar `nombre`;
- proponer otro diagnóstico;
- inventar signos;
- inventar resultados de exámenes;
- agregar medicamentos;
- agregar tratamientos;
- registrar el resultado en la BD.

Respuesta conceptual:

```json
{
  "nombre": "Miopía",
  "descripcion_original": "paciente ve borroso de lejos ambos ojos",
  "descripcion_mejorada": "Paciente refiere visión borrosa de lejos en ambos ojos.",
  "advertencia": "Borrador generado por IA. Debe ser revisado por el oftalmólogo antes de registrarse."
}
```

El frontend decidirá posteriormente si usa o descarta el borrador.

---

## 15. Seguridad y ownership

Reutilizar el patrón de CU15/CU16:

```text
usuario autenticado
→ nombre_rol_actual(usuario) == "oftalmologo"
→ obtener_oftalmologo_activo_por_usuario_id(usuario.id)
→ obtener_consulta_activa_por_id(consulta_id)
→ consulta.oftalmologo_id == oftalmologo.id
```

No aceptar el identificador del oftalmólogo desde el cliente.

No permitir analizar consultas de otro oftalmólogo.

---

## 16. Permiso funcional

Preparar el backend para una función de permisos llamada:

```text
Usar asistencia clínica IA
```

Acción mínima:

```text
LECTURA
```

Endpoint protegido con el patrón actual:

```python
requerir_permiso("Usar asistencia clínica IA", ACCION_LECTURA)
```

La función/permisos reales de Supabase se configurarán y verificarán fuera del agente después de revisar este bloque.

Los tests pueden crear sus datos de permiso localmente.

No modificar Supabase automáticamente.

---

## 17. Bitácora

Registrar únicamente cuando la operación de IA finalice correctamente.

Acciones:

```text
ANALIZAR_CONSULTA_IA
MEJORAR_REDACCION_DIAGNOSTICO_IA
```

Entidad:

```text
consulta_clinica
```

`id_registro_afectado`:

```text
consulta_id
```

Descripción:

- genérica;
- no incluir anamnesis;
- no incluir diagnóstico;
- no incluir prompt;
- no incluir respuesta del modelo;
- no incluir API key.

Ejemplos:

```text
Análisis asistido por IA solicitado sobre consulta clínica
Redacción diagnóstica asistida por IA generada
```

La bitácora debe usar el mecanismo existente `registrar_bitacora`.

---

## 18. Manejo de errores del proveedor

La capa `deepseek_provider.py` debe encapsular errores externos.

Diferenciar al menos:

```text
configuración ausente       → 503
timeout / conexión          → 503
rate limit / indisponible   → 503
respuesta inválida          → 502
```

No retornar traceback al cliente.

No exponer:

- API key;
- headers de autorización;
- prompt clínico completo;
- respuesta raw completa.

---

## 19. Transacciones

La IA no modifica consulta ni diagnóstico.

La única escritura del endpoint exitoso es la bitácora.

Flujo:

```text
validaciones
→ llamada DeepSeek
→ validar respuesta
→ registrar bitácora
→ commit
→ devolver resultado IA
```

Si falla la bitácora:

```text
rollback
→ error
```

No alterar la consulta clínica.

No crear diagnóstico.

---

## 20. Tests

Crear:

```text
tests/test_integracion_ia_deepseek.py
```

Los tests NO deben llamar a Internet ni consumir tokens reales.

Mockear el provider de DeepSeek.

Cubrir como mínimo:

### Análisis

1. análisis exitoso;
2. respuesta JSON estable;
3. bitácora correcta;
4. consulta inexistente/inactiva → 404;
5. consulta de otro oftalmólogo → 403;
6. usuario sin perfil → 403;
7. actor no oftalmólogo → 403;
8. respuesta inválida del provider → 502;
9. provider indisponible → 503.

### Mejorar redacción

10. éxito;
11. no modifica/crea diagnóstico en BD;
12. mantiene el nombre enviado;
13. body rechaza campos extra;
14. descripción vacía → 422;
15. ownership de consulta → 403.

### Seguridad

16. endpoint requiere JWT;
17. endpoint requiere permiso;
18. no se recibe `oftalmologo_id`.

Al terminar:

```text
pytest tests/test_integracion_ia_deepseek.py -q
pytest -q
```

La suite existente debe seguir pasando.

---

## 21. Registro del router

Registrar el nuevo router en:

```text
app/main.py
```

Mantener los routers existentes.

No cambiar CORS salvo que haya una necesidad demostrable.

---

## 22. No hacer en este bloque

NO implementar todavía:

- frontend Angular de IA;
- voz;
- reportes;
- Excel;
- PDF;
- CSV;
- HTML;
- email;
- backup;
- restore;
- análisis de imágenes médicas;
- diagnóstico automático;
- chatbot libre;
- RAG;
- fine-tuning;
- tablas nuevas;
- migraciones;
- cambios en Supabase;
- commit;
- push;
- merge.

---

## 23. Criterio de terminado

Este bloque queda listo cuando:

```text
[ ] módulo integracion_ia creado
[ ] DeepSeek encapsulado en provider
[ ] API key solo backend
[ ] análisis consulta funciona
[ ] mejora redacción funciona
[ ] JSON del modelo validado con Pydantic
[ ] ownership validado
[ ] permiso integrado
[ ] bitácora integrada
[ ] errores externos controlados
[ ] tests sin llamadas reales a DeepSeek
[ ] tests del bloque pasan
[ ] suite completa pasa
[ ] app inicia sin clave IA, pero endpoints IA responden 503 si se usan sin configuración
[ ] reporte final del agente entregado
```
