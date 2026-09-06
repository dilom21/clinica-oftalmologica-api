# CONTEXTO CU09 — BACKEND

## Proyecto
Sistema de Información Web y Móvil para una Clínica Oftalmológica.

## Caso de uso
**CU09 — Consultar agenda y disponibilidad médica**

Este contexto es exclusivamente para el desarrollo del **backend** del CU09.
No modificar documentación académica, diagramas, Product Backlog, Sprint Backlog ni archivos ajenos al desarrollo.

## Stack y arquitectura existente
- Backend: **FastAPI**
- ORM: **SQLAlchemy**
- Validación/esquemas: **Pydantic**
- Base de datos: **PostgreSQL en Supabase**
- Arquitectura existente del proyecto:
  - Router/API
  - Service / casos de uso
  - Repository
  - SQLAlchemy Models
  - Schemas
- Autenticación existente mediante JWT.
- Se debe respetar la estructura, nombres, patrones, dependencias y manejo de errores ya usados por el repositorio.

## Base de datos
Proyecto Supabase:
- Nombre: `clinica-oftalmologica`
- Project ref: `ypnkjfsymxrukjdyhyyc`

### Tablas relevantes para CU09

#### `oftalmologo`
- `id bigint PK`
- `usuario_id bigint UNIQUE FK -> usuario.id`
- `matricula varchar UNIQUE`
- `nombres varchar`
- `apellidos varchar`
- `especialidad varchar nullable`
- `estado boolean`
- `fecha_registro timestamptz`

#### `horario_oftalmologo`
- `id bigint PK`
- `oftalmologo_id bigint FK -> oftalmologo.id`
- `dia_semana smallint` (1 a 7)
- `hora_inicio time`
- `hora_fin time`
- `estado boolean`

Representa el horario semanal configurado del oftalmólogo.

#### `bloqueo_horario`
- `id bigint PK`
- `oftalmologo_id bigint FK -> oftalmologo.id`
- `fecha date`
- `hora_inicio time`
- `hora_fin time`
- `motivo varchar nullable`
- `estado boolean`
- `fecha_registro timestamptz`

Representa excepciones o periodos en los que el oftalmólogo no está disponible.

#### `cita`
- `id bigint PK`
- `paciente_id bigint FK -> paciente.id`
- `oftalmologo_id bigint FK -> oftalmologo.id`
- `fecha date`
- `hora_inicio time`
- `hora_fin time`
- `motivo varchar nullable`
- `observaciones text nullable`
- `estado varchar`
  - `PENDIENTE`
  - `CONFIRMADA`
  - `REPROGRAMADA`
  - `CANCELADA`
  - `ATENDIDA`
- `canal varchar nullable`
  - `WEB`
  - `MOVIL`
- `creado_por_usuario_id bigint nullable`
- `fecha_registro timestamptz`
- `fecha_actualizacion timestamptz`

## Estado actual de datos
Actualmente:
- Existe al menos un oftalmólogo activo.
- `horario_oftalmologo` puede estar vacío.
- `bloqueo_horario` puede estar vacío.
- `cita` puede estar vacío.

El CU09 debe responder correctamente aunque todavía no existan horarios configurados.

## Funcionalidad registrada en seguridad
Existe:
- Módulo: **Agenda y Citas**
- Función: **Consultar agenda y disponibilidad médica**

No modificar roles, permisos ni registros de seguridad en base de datos durante este CU.
Primero reutilizar el sistema de autenticación/autorización ya implementado en el backend.

## Objetivo funcional del CU09
Permitir consultar la agenda y disponibilidad de los oftalmólogos por fecha.

El backend debe permitir como mínimo:

1. Consultar oftalmólogos activos.
2. Consultar la agenda de un oftalmólogo para una fecha.
3. Determinar los intervalos disponibles de un oftalmólogo para una fecha.
4. Considerar:
   - horario semanal activo,
   - bloqueos activos de esa fecha,
   - citas existentes de esa fecha.
5. No considerar una cita `CANCELADA` como ocupación de horario.
6. Manejar correctamente días sin horario configurado.
7. No exponer información innecesaria de pacientes cuando el objetivo sea solamente consultar disponibilidad.

## Regla central de disponibilidad

La disponibilidad se obtiene conceptualmente así:

`Horario configurado - Bloqueos activos - Citas que ocupan horario = Intervalos disponibles`

Ejemplo:

Horario:
- 08:00 - 12:00

Cita:
- 08:30 - 09:00

Bloqueo:
- 10:00 - 11:00

Disponibilidad:
- 08:00 - 08:30
- 09:00 - 10:00
- 11:00 - 12:00

## Importante: NO inventar duración fija de turnos
La base de datos no define que una cita dure 15, 20, 30 o 60 minutos.

Por lo tanto:
- No dividir arbitrariamente la agenda en slots de 30 minutos.
- Trabajar inicialmente con **intervalos libres reales**.
- Si en el código existente ya existe una regla explícita de duración, primero verificarla antes de usarla.

## Reglas técnicas
- Trabajar sobre la arquitectura existente.
- Antes de crear archivos nuevos, inspeccionar si el módulo `gestion_agenda_citas` o equivalente ya existe.
- Reutilizar modelos, dependencias JWT, helpers, excepciones y respuestas existentes.
- No duplicar código.
- No hacer SQL directo desde router si la arquitectura usa Repository/Service.
- No acceder a Supabase mediante cliente JS desde el backend; usar la conexión PostgreSQL/SQLAlchemy existente.
- No cambiar `.env`.
- No modificar tablas ni crear migraciones.
- No insertar datos de prueba permanentes en Supabase.
- No cambiar RLS.
- No tocar CU10 ni CU11 salvo reutilización mínima de código compartido ya existente.
- No hacer commit, merge ni push.

## Contrato API esperado
Antes de implementar, inspeccionar las convenciones actuales del proyecto.

El resultado funcional debe cubrir endpoints equivalentes a:

- `GET /agenda/oftalmologos`
- `GET /agenda/disponibilidad?oftalmologo_id={id}&fecha={YYYY-MM-DD}`
- `GET /agenda/oftalmologos/{id}/agenda?fecha={YYYY-MM-DD}`

Los nombres exactos pueden adaptarse a las convenciones reales del repositorio.

### Respuesta sugerida para disponibilidad
Ejemplo conceptual:

```json
{
  "oftalmologo": {
    "id": 1,
    "nombres": "Nombre",
    "apellidos": "Apellido",
    "especialidad": "Oftalmología General"
  },
  "fecha": "2026-09-10",
  "tiene_horario": true,
  "intervalos_disponibles": [
    {
      "hora_inicio": "08:00:00",
      "hora_fin": "09:00:00"
    }
  ]
}
```

No es obligatorio copiar exactamente este formato si el proyecto ya usa otra convención consistente.

## Casos mínimos que deben probarse
1. Oftalmólogo activo con horario y sin citas ni bloqueos.
2. Día sin horario configurado.
3. Horario con una cita en medio.
4. Horario con un bloqueo en medio.
5. Varias citas y bloqueos.
6. Cita `CANCELADA` no ocupa disponibilidad.
7. Oftalmólogo inexistente.
8. Oftalmólogo inactivo.
9. Parámetro de fecha inválido.
10. Intervalos que tocan los límites del horario.

## Definition of Done técnica del backend
El backend del CU09 queda listo cuando:
- compila/importa sin errores;
- respeta la arquitectura del proyecto;
- endpoints funcionan;
- reglas de disponibilidad están implementadas;
- casos borde básicos están cubiertos;
- pruebas existentes siguen pasando;
- no se modificó la BD;
- no se modificó `.env`;
- no se hicieron commits ni pushes.
