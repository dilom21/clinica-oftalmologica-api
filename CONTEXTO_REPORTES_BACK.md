# CONTEXTO_REPORTES_BACK.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Bloque:** Reportes estáticos y dinámicos  
**Capa:** Backend FastAPI  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

> Este documento define el contexto funcional y técnico del motor de reportes. La implementación debe inspeccionar primero el estado LOCAL del repositorio. No asumir que GitHub refleja exactamente el código local.

## 2. Objetivo del bloque

Implementar un módulo backend de reportes que permita:

1. Reportes estáticos predefinidos.
2. Reportes dinámicos personalizables.
3. Vista previa JSON antes de exportar.
4. Filtros.
5. Selección de columnas.
6. Ordenamiento.
7. Exportación en:
   - Excel `.xlsx`
   - PDF `.pdf`
   - CSV `.csv`

Este bloque NO implementa frontend todavía.

## 3. Requisito académico que se busca cubrir

El sistema debe permitir:

- reportes predefinidos;
- reportes construidos por el usuario;
- seleccionar columnas;
- definir criterios/filtros;
- ordenar resultados;
- previsualizar antes de generar;
- exportar a diferentes formatos.

En una etapa posterior se agregarán HTML y envío por email. CSV se implementa ahora como formato adicional.

## 4. Arquitectura actual

```text
FastAPI
→ Router
→ Service
→ Repository
→ SQLAlchemy
→ PostgreSQL/Supabase
```

Seguridad:

```text
JWT
→ Usuario activo
→ Rol
→ Permisos dinámicos
```

Ya existen usuarios, roles, funciones, acciones, permisos, bitácora, pacientes, citas, historial clínico, consultas clínicas y diagnósticos.

No crear consultas SQL arbitrarias a partir de texto del usuario.

## 5. Principio de seguridad crítico

El motor dinámico NO debe aceptar nombres de tabla/columna arbitrarios, fragmentos SQL ni ORDER BY/WHERE escritos por el cliente.

Debe existir un registro/whitelist de datasets y campos permitidos:

```text
request lógico
→ validar dataset
→ validar columnas
→ validar filtros
→ validar orden
→ construir consulta SQLAlchemy segura
→ ejecutar
→ serializar
```

Nunca usar SQL dinámico concatenado con input del cliente.

## 6. Nuevo módulo sugerido

```text
app/modules/gestion_reportes/
├── __init__.py
├── api/
│   ├── __init__.py
│   └── router.py
├── schemas/
│   ├── __init__.py
│   └── schemas.py
├── services/
│   ├── __init__.py
│   ├── service.py
│   └── exporters/
│       ├── __init__.py
│       ├── excel_exporter.py
│       ├── pdf_exporter.py
│       └── csv_exporter.py
├── repositories/
│   ├── __init__.py
│   └── repository.py
└── registry/
    ├── __init__.py
    └── datasets.py
```

No crear tablas nuevas para reportes en este bloque.

## 7. Datasets iniciales

Implementar como mínimo:

```text
pacientes
citas
consultas_clinicas
diagnosticos
usuarios
```

No incluir bitácora en el motor general de reportes.

No exponer jamás:

```text
password_hash
token_hash
API keys
secretos
```

## 8. Registro de datasets

Cada dataset debe declarar:

```text
key
label
descripcion
campos permitidos
tipo de dato por campo
operadores permitidos por campo
campos ordenables
columnas por defecto
```

Los aliases visibles al cliente pueden diferir de nombres físicos de BD.

## 9. Campos sugeridos

### pacientes
```text
paciente_id
nombres
apellidos
ci
sexo
fecha_nacimiento
telefono
fecha_registro
estado
```

### citas
```text
cita_id
fecha
hora_inicio
hora_fin
paciente
oftalmologo
motivo
estado
canal
fecha_registro
```

### consultas_clinicas
```text
consulta_id
fecha_consulta
paciente
oftalmologo
motivo_consulta
anamnesis
observaciones
estado
cita_id
```

### diagnosticos
```text
diagnostico_id
consulta_id
fecha_diagnostico
paciente
oftalmologo
nombre
descripcion
estado
```

### usuarios
```text
usuario_id
correo
rol
estado
fecha_creacion
```

NO incluir `password_hash`.

## 10. Operadores permitidos

Texto:

```text
eq
contains
starts_with
```

Números:

```text
eq
gt
gte
lt
lte
```

Fecha/datetime:

```text
eq
gte
lte
between
```

Booleanos:

```text
eq
```

Enums/estados:

```text
eq
in
```

El registry define qué operador aplica a cada campo.

## 11. Ordenamiento

Permitir solo:

```text
asc
desc
```

y solo sobre campos whitelist. Máximo 3 criterios.

## 12. Reportes estáticos

Implementar mínimo:

```text
pacientes_activos
citas_por_fecha
consultas_clinicas
diagnosticos_registrados
usuarios_por_rol
```

Cada plantilla define dataset, columnas fijas, orden por defecto y filtros base opcionales. El usuario puede añadir filtros válidos, pero NO cambiar columnas.

## 13. Reportes dinámicos

El usuario puede indicar:

```text
dataset
columnas
filtros
orden
```

Validar todo con Pydantic + registry.

## 14. Endpoints sugeridos

```http
GET /reportes/catalogo

POST /reportes/dinamicos/previsualizar
POST /reportes/dinamicos/exportar/{formato}

POST /reportes/estaticos/{reporte_key}/previsualizar
POST /reportes/estaticos/{reporte_key}/exportar/{formato}
```

`formato`: `xlsx`, `pdf`, `csv`.

## 15. Vista previa

Respuesta conceptual:

```json
{
  "columnas": [{"key":"paciente","label":"Paciente"}],
  "filas": [{"paciente":"..."}],
  "total": 123,
  "limit": 50
}
```

Límites:

```text
default 50
max 200
```

## 16. Exportación

Máximo inicial sugerido:

```text
5000 filas
```

Si se supera, responder `422`.

Content-Type:

```text
XLSX: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
PDF: application/pdf
CSV: text/csv; charset=utf-8
```

Agregar `Content-Disposition` con filename seguro.

## 17. Excel

Usar `openpyxl`.

Debe incluir:

```text
título
fecha/hora de generación
encabezados
datos
anchos razonables
fechas legibles
```

## 18. PDF

Usar preferentemente `reportlab`.

Debe incluir:

```text
título
fecha/hora
tabla
paginación
```

Usar landscape cuando corresponda y evitar cortes ilegibles.

## 19. CSV

Usar `csv` de stdlib.

Generar UTF-8 con BOM para compatibilidad con Excel en Windows. Escapar correctamente comas, comillas y saltos.

## 20. Seguridad y roles

En este bloque, el motor de reportes será de uso administrativo.

Requerir:

```text
rol Administrador
```

y permiso:

```text
Generar reportes
```

con:

```text
ACCION_LECTURA
```

No modificar Supabase desde el agente.

## 21. Bitácora

Registrar exportaciones exitosas:

```text
EXPORTAR_REPORTE_XLSX
EXPORTAR_REPORTE_PDF
EXPORTAR_REPORTE_CSV
```

Entidad:

```text
reporte
```

Descripción genérica con dataset/reporte key.

No guardar filas, archivo, filtros completos ni contenido clínico.

## 22. Dependencias

Inspeccionar `requirements.txt`.

Agregar solo si faltan:

```text
openpyxl
reportlab
```

CSV usa stdlib.

## 23. Tests

Crear:

```text
tests/test_gestion_reportes.py
```

Cubrir mínimo:

1. catálogo devuelve datasets;
2. no expone password_hash;
3. operadores válidos;
4. preview dinámico exitoso;
5. selección de columnas;
6. filtro texto;
7. filtro fecha;
8. orden asc/desc;
9. dataset inválido → 422;
10. columna inválida → 422;
11. operador inválido → 422;
12. orden inválido → 422;
13. limit > max → 422;
14. usuario no admin → 403;
15. sin permiso → 403;
16. metadata de reportes estáticos;
17. preview estático exitoso;
18. reporte inexistente → 404;
19. XLSX con content-type correcto y bytes válidos;
20. PDF devuelve `%PDF`;
21. CSV contiene encabezados/datos;
22. exportación > máximo → 422;
23. bitácora XLSX;
24. bitácora PDF;
25. bitácora CSV;
26. password_hash nunca aparece;
27. token_hash nunca aparece;
28. bitácora no está disponible como dataset general.

Ejecutar:

```text
pytest tests/test_gestion_reportes.py -q
pytest -q
```

## 24. No hacer en este bloque

NO implementar:

- frontend Angular;
- voz;
- IA para construir reportes por voz;
- HTML;
- email;
- backup;
- restore;
- bitácora confidencial con llave de desarrollador;
- SaaS/suscripciones;
- tablas nuevas;
- migraciones;
- cambios directos en Supabase;
- commit;
- push;
- merge.

## 25. Criterio de terminado

```text
[ ] módulo gestion_reportes
[ ] registry/whitelist segura
[ ] 5 datasets
[ ] 5 reportes estáticos
[ ] catálogo
[ ] preview dinámico
[ ] preview estático
[ ] filtros
[ ] selección de columnas
[ ] orden
[ ] XLSX
[ ] PDF
[ ] CSV
[ ] límite preview
[ ] límite exportación
[ ] rol Administrador
[ ] permiso Generar reportes
[ ] bitácora de exportación
[ ] no expone hashes/secretos
[ ] tests del bloque pasan
[ ] suite completa pasa
[ ] reporte final del agente
```
