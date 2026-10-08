# CONTEXTO_CU16_BACK.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Caso de uso:** CU16 – Registrar diagnóstico  
**Capa:** Backend API  
**Tecnología:** FastAPI + SQLAlchemy + Pydantic + PostgreSQL/Supabase  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

> Este documento define el contexto funcional y técnico para implementar CU16. La IA debe inspeccionar primero el estado LOCAL del repositorio, porque puede contener cambios todavía no enviados a GitHub.

## 2. Estado previo confirmado

CU15 – Registrar consulta clínica está cerrado en backend.

Flujo CU15 actual:

```text
JWT
→ Usuario
→ Oftalmólogo
→ Historial
→ Paciente activo
→ Cita opcional
→ Consulta clínica
→ Cita ATENDIDA
→ Bitácora
→ Commit
```

CU16 parte de una `consulta_clinica` ya existente.

No se debe duplicar lógica de CU15 ni crear otra consulta para registrar un diagnóstico.

## 3. Actor principal

**Oftalmólogo**

La autorización debe respetar el patrón actual del backend:

```text
JWT
→ obtener_usuario_actual
→ requerir_permiso(...)
→ Usuario
→ perfil Oftalmólogo activo
```

Para registrar un diagnóstico, además de poseer el permiso, el usuario debe corresponder al oftalmólogo de la consulta clínica.

## 4. Supabase ya preparado

Proyecto:

```text
clinica-oftalmologica
ref: ypnkjfsymxrukjdyhyyc
```

La tabla `diagnostico` ya existe.

Estructura real confirmada:

```text
diagnostico
--------------------------------
id                   bigint PK
consulta_clinica_id  bigint NOT NULL
nombre               varchar(150) NOT NULL
descripcion          text NULL
fecha_diagnostico    timestamptz NULL DEFAULT CURRENT_TIMESTAMP
estado               boolean NULL DEFAULT true
```

Relación real:

```text
diagnostico.consulta_clinica_id
→ consulta_clinica.id
ON DELETE CASCADE
```

Índice existente:

```text
idx_diagnostico_consulta (consulta_clinica_id)
```

Actualmente no hay diagnósticos registrados en la tabla.

No crear otra tabla de diagnóstico.

## 5. Permiso CU16 ya creado en Supabase

Función:

```text
Registrar diagnóstico
```

Módulo:

```text
Pacientes e Historial Clínico
```

Permisos configurados:

```text
Oftalmólogo   → ESCRITURA
Administrador → AMBAS
```

El backend debe proteger el endpoint con:

```text
requerir_permiso("Registrar diagnóstico", ACCION_ESCRITURA)
```

o el equivalente exacto del patrón local.

No modificar permisos ni Supabase durante la implementación.

## 6. Alcance funcional de CU16

CU16 debe permitir registrar **uno o más diagnósticos** asociados a una consulta clínica.

Relación:

```text
ConsultaClinica 1 ─── N Diagnostico
```

Una consulta puede tener varios diagnósticos.

No imponer `UNIQUE(consulta_clinica_id)`.

No impedir múltiples diagnósticos distintos dentro de una consulta.

## 7. Datos del diagnóstico

Campos funcionales actuales:

### nombre

- obligatorio
- texto no vacío
- máximo 150 caracteres

### descripcion

- opcional
- texto libre

El cliente NO debe enviar:

- `id`
- `fecha_diagnostico`
- `estado`
- `oftalmologo_id`
- `usuario_id`

La fecha debe establecerse en backend/BD.

`estado` debe quedar activo al crear el diagnóstico.

## 8. CIE-10 y tipos de diagnóstico

La tabla actual **NO posee**:

- `codigo_cie10`
- `tipo`
- `principal/secundario`
- `presuntivo/definitivo`

Por tanto CU16, en esta iteración, debe ajustarse a la tabla real:

```text
nombre
descripcion
```

No modificar Supabase ni agregar columnas durante este CU sin una instrucción posterior.

CIE-10 puede evaluarse después como ampliación para IA/reportes, pero no forma parte del contrato actual.

## 9. Consulta clínica válida

Antes de registrar un diagnóstico debe validarse:

1. la consulta existe;
2. la consulta está activa;
3. el usuario autenticado es un oftalmólogo activo;
4. la consulta pertenece a ese oftalmólogo.

No confiar en un `oftalmologo_id` enviado por el cliente.

La seguridad debe derivarse de:

```text
JWT → Usuario → Oftalmólogo
```

y luego comparar:

```text
consulta_clinica.oftalmologo_id == oftalmologo.id
```

## 10. Consulta sin cita

Una consulta clínica puede provenir de CU15 sin cita.

Eso NO afecta CU16.

El diagnóstico se asocia a:

```text
consulta_clinica_id
```

y no depende directamente de `cita_id`.

## 11. Contrato HTTP preferido

Si no existe todavía un patrón local contradictorio, usar:

```http
POST /historial-clinico/consultas/{consulta_id}/diagnosticos
```

Body:

```json
{
  "nombre": "Miopía",
  "descripcion": "Miopía bilateral leve."
}
```

Respuesta conceptual:

```json
{
  "id": 1,
  "consulta_clinica_id": 10,
  "nombre": "Miopía",
  "descripcion": "Miopía bilateral leve.",
  "fecha_diagnostico": "...",
  "estado": true
}
```

Status:

```text
201 Created
```

También es útil disponer de lectura para el flujo frontend:

```http
GET /historial-clinico/consultas/{consulta_id}/diagnosticos
```

Si ya existe una convención equivalente en el módulo local, seguir la convención real y documentarla.

## 12. Arquitectura backend

Mantener:

```text
Router
→ Service
→ Repository
→ SQLAlchemy
→ PostgreSQL/Supabase
```

CU16 debe integrarse al módulo actual:

```text
app/modules/gestion_historial_clinico/
```

No crear un módulo backend paralelo solo para diagnóstico.

La carpeta `casos_uso/`, si sigue vacía, no debe provocar una refactorización masiva.

## 13. Modelo SQLAlchemy

Se espera mapear la tabla real mediante una clase:

```text
Diagnostico
```

en el módulo de historial clínico.

Debe coincidir con la BD actual.

No agregar columnas inexistentes.

Se puede agregar relationship con `ConsultaClinica` si mejora el modelo y no rompe la arquitectura.

## 14. Schemas Pydantic

Como mínimo:

```text
DiagnosticoCrear
DiagnosticoRespuesta
```

`DiagnosticoCrear`:

- nombre obligatorio
- strip whitespace
- no permitir cadena vacía
- max_length = 150
- descripcion opcional
- `extra="forbid"`

Si el `consulta_id` va en la URL, NO incluir `consulta_clinica_id` en el body.

Esto evita inconsistencias entre path y body.

## 15. Repository

Responsabilidades esperadas:

- obtener consulta clínica activa por id;
- crear diagnóstico;
- obtener diagnóstico por id si es necesario;
- listar diagnósticos activos de una consulta.

Los repositorios:

- no hacen `commit()`;
- pueden usar `flush()` / `refresh()` según patrón actual.

## 16. Service

Responsabilidades mínimas:

1. validar actor/rol cuando corresponda al patrón actual;
2. resolver oftalmólogo activo desde usuario;
3. obtener consulta activa;
4. validar propiedad de la consulta;
5. crear diagnóstico;
6. registrar bitácora;
7. commit único;
8. rollback ante fallo.

Conceptualmente:

```text
try:
    crear diagnóstico
    registrar bitácora
    commit
except:
    rollback
    raise
```

## 17. Autorización

El permiso de función NO sustituye la validación clínica de propiedad.

Un usuario con permiso no debe poder registrar diagnóstico sobre la consulta de otro oftalmólogo.

El frontend tampoco debe elegir el oftalmólogo.

## 18. Bitácora

Seguir las convenciones reales del proyecto.

Valor esperado:

```text
accion = REGISTRAR_DIAGNOSTICO
entidad_afectada = diagnostico
id_registro_afectado = diagnostico.id
descripcion = Diagnóstico registrado
```

Si el proyecto usa otra redacción consistente, mantener el patrón real.

La bitácora forma parte de la misma transacción.

## 19. Duplicados

La BD NO define una restricción UNIQUE para nombres de diagnóstico por consulta.

No inventar una restricción estructural.

En esta versión, una consulta puede registrar múltiples diagnósticos.

Si se decide detectar duplicados exactos por UX, debe justificarse y hacerse sin impedir escenarios clínicamente válidos.

Por defecto, NO implementar una regla de duplicado que no está definida.

## 20. Estados y borrado

CU16 es:

```text
Registrar diagnóstico
```

No implementar en esta tarea:

- editar diagnóstico;
- eliminar diagnóstico;
- desactivar diagnóstico;
- restaurar diagnóstico.

El campo `estado` existe porque forma parte del modelo global, pero no requiere endpoints adicionales en CU16.

## 21. Errores esperados

Utilizar códigos coherentes con el proyecto.

Casos mínimos:

### 403
- usuario no es oftalmólogo;
- perfil oftalmólogo inexistente/inactivo;
- consulta pertenece a otro oftalmólogo.

### 404
- consulta inexistente o inactiva.

### 422
- nombre vacío;
- nombre > 150;
- campos extra;
- payload inválido.

### 500
- fallo inesperado, con rollback.

No exponer detalles de SQL o stack traces al usuario.

## 22. Tests

CU16 debe tener una suite específica.

Archivo sugerido:

```text
tests/test_cu16_registrar_diagnostico.py
```

Casos mínimos:

1. registra diagnóstico correctamente → 201;
2. persiste `consulta_clinica_id`;
3. fecha se genera correctamente;
4. estado queda activo;
5. descripción puede ser null;
6. permite varios diagnósticos en una consulta;
7. consulta inexistente → 404;
8. consulta inactiva → 404;
9. consulta de otro oftalmólogo → 403;
10. usuario no oftalmólogo → rechazado;
11. oftalmólogo sin perfil activo → rechazado;
12. sin permiso → 403;
13. nombre vacío → 422;
14. nombre > 150 → 422;
15. campo extra como `oftalmologo_id` → 422;
16. campo extra `consulta_clinica_id` en body → 422 si el id está en path;
17. bitácora correcta;
18. rollback si falla bitácora;
19. repository no hace commit;
20. listado devuelve diagnósticos de la consulta si se implementa GET.

No eliminar pruebas válidas de CU15.

## 23. Regresión

Después de CU16 deben seguir pasando:

- CU15;
- historial clínico;
- agenda/citas;
- pacientes;
- autenticación/seguridad.

Ejecutar suite general al final.

## 24. Fuera de alcance

No implementar todavía:

- CU17 tratamientos/recetas;
- CU18 exámenes;
- CU19 controles;
- IA;
- CIE-10;
- reportes;
- frontend;
- móvil;
- cambios Supabase;
- migraciones;
- SaaS;
- backup.

## 25. Criterios de terminado

CU16 backend termina cuando:

- [ ] existe modelo Diagnostico;
- [ ] schema de creación valida nombre;
- [ ] body no elige oftalmólogo;
- [ ] consulta se identifica por ruta o patrón coherente;
- [ ] consulta debe existir/estar activa;
- [ ] consulta pertenece al oftalmólogo autenticado;
- [ ] se pueden registrar múltiples diagnósticos;
- [ ] diagnóstico se persiste;
- [ ] fecha/estado se generan correctamente;
- [ ] bitácora se registra;
- [ ] existe un único commit lógico;
- [ ] rollback ante fallo;
- [ ] endpoint está protegido por `Registrar diagnóstico` + ESCRITURA;
- [ ] tests CU16 pasan;
- [ ] suite general pasa;
- [ ] no se modifica Supabase/frontend/móvil.

## 26. Regla final

Primero inspeccionar el repositorio LOCAL.

Después implementar solamente CU16.

No ampliar el esquema de datos sin autorización.
