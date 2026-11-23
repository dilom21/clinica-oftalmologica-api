# CONTEXTO_CU15_FRONT.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Sprint:** Sprint 2  
**Caso de uso:** CU15 – Registrar consulta clínica  
**Capa:** Frontend Web  
**Tecnología:** Angular  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-web`

> Este documento describe el contexto funcional y técnico que debe respetarse al implementar el frontend del CU15. Antes de programar, la IA debe inspeccionar el estado LOCAL del repositorio porque puede contener cambios todavía no enviados a GitHub.

## 2. Objetivo del CU15

Permitir que un **oftalmólogo autenticado y autorizado** registre una consulta clínica de un paciente, vinculándola a su historial clínico y, cuando corresponda, a una cita existente.

El frontend debe facilitar el proceso, pero **no debe implementar reglas de seguridad que correspondan al backend como única barrera**. El backend sigue siendo la autoridad para permisos, identidad del oftalmólogo y validaciones de integridad.

## 3. Actor principal

**Oftalmólogo**

El usuario debe haber iniciado sesión.

El frontend puede ocultar o deshabilitar opciones según el menú dinámico y el estado de la interfaz, pero la autorización definitiva la controla FastAPI mediante:

- JWT
- usuario autenticado
- rol/permisos
- función `Registrar consulta clínica`
- acción `ESCRITURA`

## 4. Estado confirmado del backend CU15

El backend CU15 se considera **COMPLETO** y pasó:

- 51 tests específicos de CU15.
- 296 tests de la suite general.

El flujo backend final es:

```text
POST /historial-clinico/consultas
  → JWT
  → usuario autenticado
  → permiso "Registrar consulta clínica" / ESCRITURA
  → resolver oftalmólogo por usuario autenticado
  → validar historial clínico
  → validar paciente activo
  → si cita_id != null:
       validar cita
       validar mismo oftalmólogo
       validar mismo paciente
       validar estado de cita
       validar ausencia de consulta previa
  → crear consulta
  → si hay cita: cita.estado = ATENDIDA
  → registrar bitácora
  → COMMIT
  → 201
```

Estados de cita válidos para iniciar una consulta:

- `PROGRAMADA`
- `CONFIRMADA`
- `EN_ESPERA`

Estados que el backend rechaza:

- `ATENDIDA`
- `CANCELADA`
- `NO_ASISTIO`

La consulta también puede registrarse **sin cita**.

## 5. Regla crítica de seguridad

El frontend **NUNCA** debe enviar:

```text
oftalmologo_id
```

El backend obtiene el oftalmólogo desde el usuario autenticado:

```text
JWT → usuario.id → oftalmologo.usuario_id → oftalmologo.id
```

El request del frontend debe contener únicamente los datos funcionales de la consulta.

## 6. Contrato principal del CU15

### Endpoint de creación

```http
POST /historial-clinico/consultas
```

Payload conceptual:

```json
{
  "historial_clinico_id": 1,
  "cita_id": 15,
  "motivo_consulta": "Disminución de visión",
  "anamnesis": "Paciente refiere...",
  "observaciones": "..."
}
```

`cita_id` puede ser `null`.

No enviar propiedades adicionales.

### Respuesta esperada

La respuesta real debe tomarse de los modelos/interfaces que se construyan a partir del contrato del backend local. Conceptualmente incluye:

- `id`
- `historial_clinico_id`
- `cita_id`
- `oftalmologo`
- `fecha_consulta`
- `motivo_consulta`
- `anamnesis`
- `observaciones`
- `estado`

No inventar campos si no existen en el response real.

## 7. Otros endpoints relacionados conocidos

Existen endpoints del módulo de historial clínico como:

```text
GET /historial-clinico/{paciente_id}
GET /historial-clinico/consultas
GET /historial-clinico/consultas/{consulta_id}
```

También existen endpoints de agenda/citas ya utilizados por otros CU.

La implementación frontend debe **inspeccionar y reutilizar** los services y contratos existentes de:

- gestión de pacientes
- historial clínico
- agenda/citas
- autenticación/seguridad
- menú dinámico

No inventar endpoints de citas o pacientes si ya existen equivalentes.

## 8. Flujo UX objetivo

La interfaz no debe obligar al oftalmólogo a memorizar IDs.

Flujo esperado:

```text
Oftalmólogo autenticado
        ↓
Menú → Registrar consulta clínica
        ↓
Buscar/seleccionar paciente
        ↓
Mostrar resumen del paciente
        ↓
Consultar historial clínico y antecedentes
        ↓
Elegir una cita válida cuando corresponda
        O
Continuar sin cita
        ↓
Completar:
- motivo de consulta
- anamnesis
- observaciones
        ↓
Revisar información
        ↓
Registrar consulta
        ↓
Mensaje de éxito
        ↓
Mostrar consulta registrada / limpiar o redirigir según patrón existente
```

La interfaz debe ser clara y usable tanto en escritorio como en pantallas pequeñas.

## 9. Selección de paciente

Reutilizar los patrones y servicios existentes del proyecto.

Debe evitarse un campo de texto donde el usuario escriba manualmente `paciente_id`.

La UI debería permitir buscar/seleccionar por los datos que el proyecto ya exponga, por ejemplo:

- nombre
- apellido
- CI

No crear nuevos endpoints solo para la interfaz si el backend existente ya permite obtener estos datos.

Una vez elegido el paciente, obtener su historial clínico.

Si el paciente no tiene historial disponible, informar claramente y bloquear el registro de la consulta.

## 10. Historial y antecedentes

El frontend debe mostrar, cuando estén disponibles:

- datos principales del paciente
- fecha de apertura del historial
- observaciones generales
- antecedentes clínicos existentes

El historial se usa para obtener:

```text
historial_clinico_id
```

Ese identificador puede manejarse internamente en el componente/service, pero no debe pedirse manualmente al usuario.

## 11. Selección de cita

Si existen citas del paciente accesibles desde los servicios actuales, mostrar únicamente o priorizar las que sean utilizables para CU15.

Estados iniciables:

```text
PROGRAMADA
CONFIRMADA
EN_ESPERA
```

La UI puede impedir seleccionar:

```text
ATENDIDA
CANCELADA
NO_ASISTIO
```

pero el backend seguirá validándolo.

Debe existir una opción explícita de:

```text
Registrar consulta sin cita
```

si el diseño actual del proyecto lo permite.

No crear una segunda lógica de negocio diferente a la del backend.

## 12. Campos de la consulta

### Motivo de consulta

- opcional según contrato backend actual
- máximo 255 caracteres si así lo define el schema
- mostrar contador si encaja con el estilo del proyecto

### Anamnesis

- textarea
- texto clínico libre

### Observaciones

- textarea
- opcional

Evitar formularios sobrecargados.

## 13. Estados de interfaz

La pantalla debe contemplar:

- carga inicial
- búsqueda de paciente
- paciente seleccionado
- historial cargando
- historial inexistente
- citas cargando
- sin citas disponibles
- envío en progreso
- registro exitoso
- error 400/401/403/404/409/422/500

Los mensajes deben ser comprensibles para un usuario clínico.

No mostrar al usuario final trazas, nombres internos de clases, SQL o errores técnicos crudos.

## 14. Manejo de errores HTTP

Respetar el patrón global del proyecto.

Casos relevantes:

- **401:** sesión inválida/expirada.
- **403:** usuario sin permiso o no autorizado como oftalmólogo.
- **404:** historial, cita u otro recurso no encontrado.
- **409:** conflicto de negocio.
- **422:** payload inválido.
- **500:** error interno.

No duplicar mensajes rígidos si el proyecto ya posee una utilidad común para extraer `detail` de FastAPI.

## 15. Menú dinámico y navegación

En Supabase ya existe la función:

```text
Registrar consulta clínica
```

Permisos:

```text
Oftalmólogo   → ESCRITURA
Administrador → AMBAS
```

El sidebar actual mapea nombres de funciones a rutas mediante `rutasFunciones`.

Debe agregarse un mapeo para:

```text
registrar consulta clínica
```

hacia la ruta seleccionada para CU15.

El frontend puede mostrar la opción según `/seguridad/menu`, pero **no reemplaza** la autorización del backend.

## 16. Ruta sugerida

Preferencia:

```text
/registrar-consulta-clinica
```

Antes de crearla, revisar las convenciones del `app.routes.ts` LOCAL.

Si el proyecto usa otra convención coherente, puede elegirse otra ruta, pero debe quedar documentada en el reporte.

## 17. Arquitectura frontend esperada

Mantener la arquitectura actual del repositorio y los componentes standalone si ese es el patrón vigente.

Estructura sugerida:

```text
src/app/features/gestion-historial-clinico/casos-uso/
└── cu15-registrar-consulta-clinica/
    ├── models/
    │   └── consulta-clinica.models.ts
    ├── services/
    │   └── consulta-clinica.service.ts
    ├── pages/
    │   └── registrar-consulta/
    │       ├── registrar-consulta.ts
    │       ├── registrar-consulta.html
    │       └── registrar-consulta.css
    └── components/
        └── ... solo si realmente mejora la separación
```

No crear componentes vacíos o capas innecesarias.

## 18. Reutilización obligatoria

Antes de programar, revisar y reutilizar cuando sea razonable:

- services HTTP existentes
- environment/config de API
- interceptor JWT
- `authGuard`
- modelos de Paciente
- modelos de Cita
- services de CU07/CU10/CU13
- estilos/layout existentes
- patrones de loading/error
- componentes reutilizables
- menú dinámico
- sidebar

No duplicar código solo para aislar artificialmente CU15.

## 19. Validación del formulario

Preferir Reactive Forms si es el patrón predominante del proyecto.

Validar:

- paciente seleccionado
- historial válido
- motivo ≤ límite backend
- textos según contrato
- cita opcional
- evitar doble submit

Al enviar:

- deshabilitar botón durante la request
- no enviar `undefined` de forma innecesaria
- transformar campos vacíos de acuerdo con el contrato ya utilizado en el proyecto
- jamás enviar `oftalmologo_id`

## 20. Bitácora

El frontend **NO** registra bitácora directamente.

El backend realiza:

```text
REGISTRAR_CONSULTA_CLINICA
```

La interfaz solo realiza el POST correspondiente.

## 21. Supabase

NO acceder directamente a tablas de Supabase desde Angular para este CU.

Flujo correcto:

```text
Angular → FastAPI → PostgreSQL/Supabase
```

No colocar credenciales privadas, SQL o lógica de permisos de BD en Angular.

## 22. Fuera de alcance de CU15 Front

No implementar todavía:

- CU16 diagnóstico
- tratamientos
- recetas
- exámenes
- controles
- IA clínica
- reportes
- backup/restore
- SaaS
- móvil
- edición/eliminación de consulta clínica

## 23. Criterios de terminado

CU15 frontend se considera terminado cuando:

- [ ] existe una ruta accesible con `authGuard`
- [ ] sidebar reconoce `Registrar consulta clínica`
- [ ] paciente se selecciona sin escribir IDs manuales
- [ ] historial se consulta y muestra
- [ ] antecedentes se muestran cuando existen
- [ ] se puede elegir una cita válida si el flujo actual lo permite
- [ ] se puede registrar sin cita
- [ ] formulario valida correctamente
- [ ] no se envía `oftalmologo_id`
- [ ] POST usa `/historial-clinico/consultas`
- [ ] loading evita doble submit
- [ ] errores backend se muestran de forma comprensible
- [ ] 201 muestra éxito
- [ ] diseño es coherente con el sistema
- [ ] build Angular pasa
- [ ] tests existentes no se rompen

## 24. Regla final

Primero inspeccionar el repositorio LOCAL.

Después implementar únicamente lo necesario para CU15.

No modificar backend, móvil ni Supabase.
