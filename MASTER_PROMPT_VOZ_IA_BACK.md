# MASTER_PROMPT_VOZ_IA_BACK.md

## UBICACIÓN

Trabaja EXCLUSIVAMENTE en:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Estamos en el **PASO 6A — Backend para Reportes por Voz + IA**.

Lee COMPLETOS:

```text
CONTEXTO_VOZ_IA_BACK.md
MASTER_PROMPT_VOZ_IA_BACK.md
```

Después inspecciona el código LOCAL.

# OBJETIVO

Implementar:

```http
POST /ia/reportes/interpretar
```

Este endpoint recibe TEXTO ya transcrito por el navegador y lo convierte mediante DeepSeek en una configuración SEGURA de reporte dinámico.

NO recibe audio.

NO transcribe audio.

NO genera SQL.

NO ejecuta el reporte.

NO exporta automáticamente.

---

# REUTILIZAR

Debes reutilizar:

```text
app/modules/integracion_ia/
```

especialmente el provider DeepSeek existente.

Y:

```text
app/modules/gestion_reportes/
```

especialmente:

```text
registry/datasets.py
schemas
validaciones
```

No dupliques catálogo ni cliente IA.

---

# SEGURIDAD

Exigir:

```text
JWT válido
rol Administrador
permiso "Generar reportes"
ACCION_LECTURA
```

NO exigir rol Oftalmólogo.

---

# REQUEST

Body exacto:

```json
{
  "texto": "Muéstrame las consultas de octubre..."
}
```

`texto`:
- trim;
- obligatorio;
- no vacío;
- máximo 1000;
- `extra="forbid"`.

---

# RESPONSE

Crear una respuesta estable equivalente a:

```json
{
  "dataset": "consultas_clinicas",
  "columnas": [
    "fecha_consulta",
    "paciente",
    "oftalmologo",
    "motivo_consulta"
  ],
  "filtros": [],
  "orden": [],
  "limit": 50,
  "requiere_aclaracion": false,
  "pregunta_aclaracion": null,
  "accion_sugerida": "previsualizar",
  "formato_sugerido": null
}
```

Reutiliza schemas de filtros/orden de reportes cuando sea posible.

---

# PROMPT DEEPSEEK

Genera el catálogo permitido desde el registry REAL.

Incluye solo:

```text
dataset keys
field keys
labels
tipos
operadores permitidos
ordenables
```

NO envíes filas de BD.

NO consultes datos reales solo para enriquecer el prompt.

System prompt debe indicar:

```text
NO SQL.
NO inventar datasets.
NO inventar campos.
NO inventar operadores.
máximo 3 órdenes.
limit máximo 200.
JSON válido únicamente.
si es ambiguo → requiere_aclaracion=true.
```

Incluye fecha actual para resolver expresiones relativas.

---

# DOBLE VALIDACIÓN

Después de DeepSeek:

```text
json.loads
→ Pydantic
→ registry
```

Validar otra vez:

```text
dataset
columnas
filtros
operador por campo
orden
max 3
limit <=200
formato sugerido
```

Nunca confiar directamente en el modelo.

---

# ACCIÓN SUGERIDA

Interpretar:

```text
"muéstrame..." → previsualizar
"exporta..." → exportar
```

Formatos permitidos:

```text
xlsx
pdf
csv
html
```

Pero el endpoint NO exporta.

---

# PRIVACIDAD

Al modelo solo se envía:

```text
texto del comando
metadata del catálogo
fecha actual
```

NO enviar:

```text
filas
pacientes
diagnósticos reales
correos
CI
teléfonos
bitácora
hashes
```

---

# BITÁCORA

Éxito:

```text
INTERPRETAR_REPORTE_IA
```

Entidad:

```text
reporte
```

Descripción genérica.

NO guardar texto dictado, prompt, filtros o respuesta IA.

---

# TESTS

Crear:

```text
tests/test_ia_reportes_voz.py
```

Mock DeepSeek.

Cubrir:

```text
comando simple
fechas
between
orden
exportar PDF
dataset inválido
columna inválida
operador inválido
más de 3 órdenes
limit >200
JSON inválido
provider 503
texto vacío
extra field
no admin
sin permiso
JWT
bitácora
no texto dictado en bitácora
no datos BD en prompt
registry como fuente de verdad
```

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ia_reportes_voz.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

---

# NO HACER

NO:

```text
Angular
micrófono
audio
speech-to-text backend
SQL IA
ejecutar reportes
exportar reportes
Supabase
migraciones
backup
commit
push
merge
```

---

# REPORTE FINAL

Entrega:

```text
1. ESTADO
2. ARCHIVOS CREADOS
3. ARCHIVOS MODIFICADOS
4. ENDPOINT
5. REQUEST/RESPONSE
6. DEEPSEEK
7. REGISTRY REUTILIZADO
8. DOBLE VALIDACIÓN
9. SEGURIDAD
10. PRIVACIDAD
11. BITÁCORA
12. TESTS DEL BLOQUE
13. SUITE COMPLETA
14. DEUDAS / DECISIONES
15. GIT STATUS
```

Confirma:
- no SQL generado por IA;
- no audio backend;
- no filas BD enviadas a DeepSeek;
- no cambios Supabase;
- no commit/push/merge.
