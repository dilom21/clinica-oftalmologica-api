# MASTER_PROMPT_REPORTES_BACK.md

## UBICACIÓN EXACTA

Trabaja EXCLUSIVAMENTE en:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Estamos en el **PASO 3 — Backend de reportes estáticos y dinámicos**.

Antes de tocar código:

1. Lee COMPLETO `CONTEXTO_REPORTES_BACK.md`.
2. Lee COMPLETO `MASTER_PROMPT_REPORTES_BACK.md`.
3. Inspecciona el código LOCAL.
4. No asumas que GitHub está actualizado.

# OBJETIVO

Implementar backend para:

```text
- reportes estáticos
- reportes dinámicos
- filtros
- selección de columnas
- ordenamiento
- vista previa
- Excel XLSX
- PDF
- CSV
```

No implementar frontend todavía.

# REGLA CRÍTICA

NO construir SQL con strings provenientes del cliente.

Prohibido:

```python
text(f"SELECT {columnas} FROM {tabla} WHERE {filtro}")
```

Implementa un `registry/whitelist` de datasets y campos. El cliente usa claves lógicas y el backend las traduce a expresiones SQLAlchemy conocidas.

# INSPECCIÓN

Revisa como mínimo:

```text
app/main.py
app/core/dependencies.py
app/core/config.py

app/modules/gestion_usuarios_seguridad/
app/modules/gestion_pacientes/
app/modules/gestion_agenda_citas/
app/modules/gestion_historial_clinico/

requirements.txt
tests/
```

Busca cualquier módulo de reportes previo. Si existe algo parcial, reutilízalo/corrígelo.

# MÓDULO

Crear, si no existe:

```text
app/modules/gestion_reportes/
├── api/router.py
├── schemas/schemas.py
├── services/service.py
├── services/exporters/excel_exporter.py
├── services/exporters/pdf_exporter.py
├── services/exporters/csv_exporter.py
├── repositories/repository.py
└── registry/datasets.py
```

Agrega `__init__.py` necesarios. No crear modelos/tablas nuevas.

# DATASETS

Implementa mínimo:

```text
pacientes
citas
consultas_clinicas
diagnosticos
usuarios
```

NO incluir:

```text
bitacora
token_recuperacion
password_hash
token_hash
```

# CAMPOS

Usa aliases lógicos seguros y ajusta solo si el modelo SQLAlchemy LOCAL tiene un nombre diferente. Documenta cualquier ajuste.

# FILTROS

Soporta:

```text
eq
contains
starts_with
gt
gte
lt
lte
between
in
```

No todos aplican a todos los tipos. El registry define qué operador está permitido. Valor inválido → `422`.

# ORDEN

Solo:

```text
asc
desc
```

sobre campos whitelist. Máximo 3 criterios.

# REPORTES ESTÁTICOS

Implementa mínimo:

```text
pacientes_activos
citas_por_fecha
consultas_clinicas
diagnosticos_registrados
usuarios_por_rol
```

El usuario puede añadir filtros válidos, pero NO cambiar columnas.

# ENDPOINTS

Implementar:

```http
GET  /reportes/catalogo

POST /reportes/dinamicos/previsualizar
POST /reportes/dinamicos/exportar/{formato}

POST /reportes/estaticos/{reporte_key}/previsualizar
POST /reportes/estaticos/{reporte_key}/exportar/{formato}
```

`formato`: `xlsx`, `pdf`, `csv`.

Formato inválido → `422`.
Reporte estático inexistente → `404`.

# PREVIEW

Default `50`, máximo `200`. No cargar toda la tabla solo para preview.

# EXPORTACIÓN

Máximo inicial `5000 filas`. Si supera → `422`.

Headers correctos:

```text
XLSX: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
PDF: application/pdf
CSV: text/csv; charset=utf-8
```

Agregar `Content-Disposition`.

# XLSX

Usar `openpyxl`. Incluir título, fecha/hora, encabezados, filas, fechas legibles y anchos razonables.

# PDF

Usar `reportlab`. Incluir título, fecha/hora, tabla y paginación. Usar landscape cuando corresponda.

# CSV

Usar `csv` de Python. Generar UTF-8 con BOM.

# SEGURIDAD

Este bloque será ADMINISTRATIVO.

Exigir:

```text
rol == Administrador
```

y:

```python
requerir_permiso("Generar reportes", ACCION_LECTURA)
```

No cambies Supabase. Los tests pueden crear permiso localmente.

# BITÁCORA

Registrar exportaciones exitosas:

```text
EXPORTAR_REPORTE_XLSX
EXPORTAR_REPORTE_PDF
EXPORTAR_REPORTE_CSV
```

Entidad `reporte`. No guardar filas, archivo, filtros completos ni contenido clínico.

# DEPENDENCIAS

Inspecciona `requirements.txt`. Agrega solo si faltan:

```text
openpyxl
reportlab
```

# REGISTRO ROUTER

Registrar en `app/main.py` sin eliminar routers existentes.

Prefix:

```text
/reportes
```

Tag:

```text
Reportes
```

# TESTS

Crear:

```text
tests/test_gestion_reportes.py
```

Cubrir catálogo, whitelist, filtros, orden, columnas, datasets inválidos, no exposición de hashes, no exposición de bitácora, reportes estáticos, XLSX, PDF, CSV, límites, rol admin, permiso y bitácora.

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gestion_reportes.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

No debilites tests existentes.

# NO HACER

NO:

```text
frontend
voz
IA para reportes
HTML
email
backup
restore
SaaS
migraciones
Supabase
commit
push
merge
```

No tocar `.env`.

# REPORTE FINAL OBLIGATORIO

No hagas commit.

Entrega:

```text
1. ESTADO
2. ARCHIVOS CREADOS
3. ARCHIVOS MODIFICADOS
4. DEPENDENCIAS AGREGADAS
5. DATASETS IMPLEMENTADOS
6. REPORTES ESTÁTICOS
7. ENDPOINTS
8. SEGURIDAD
9. MOTOR DINÁMICO
10. EXPORTADORES
11. BITÁCORA
12. TESTS DEL BLOQUE
13. SUITE COMPLETA
14. DEUDAS / DECISIONES
15. GIT STATUS
```

Si encuentras una incompatibilidad significativa con los modelos locales, no improvises un cambio destructivo: detente y repórtala.
