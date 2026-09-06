# PROMPT MAESTRO — CU09 CONSULTAR AGENDA Y DISPONIBILIDAD MÉDICA

## PROYECTO

Estás trabajando en el proyecto **Clínica Oftalmológica** de Sistemas de Información II.

Repositorios esperados:

- `clinica-oftalmologica-api` — FastAPI + SQLAlchemy 2 + Psycopg 3
- `clinica-oftalmologica-web` — Angular
- `clinica-oftalmologica-mobile` — Flutter

Base de datos: PostgreSQL en Supabase, proyecto `clinica-oftalmologica`, ref `ypnkjfsymxrukjdyhyyc`.

Debes implementar exclusivamente:

**CU09 — Consultar agenda y disponibilidad médica (Web / Móvil)**

Antes de hacer cualquier cambio, lee completo `contexto.md` y úsalo como fuente de verdad operacional.

---

# IMPORTANTE

Quiero que trabajes como agente de desarrollo sobre los archivos reales del proyecto, no que me devuelvas solamente ejemplos de código.

Debes:

1. inspeccionar primero los repositorios;
2. comprender la arquitectura existente;
3. identificar componentes reutilizables;
4. modificar archivos reales;
5. probar cada bloque importante;
6. corregir los errores causados por tus cambios;
7. detenerte cuando CU09 quede implementado y validado.

NO hagas commit ni push.

---

# RESTRICCIONES ABSOLUTAS

- NO desarrollar CU10.
- NO desarrollar CU11.
- NO tocar documentación académica.
- NO modificar `.env`.
- NO mostrar ni cambiar secretos.
- NO cambiar Supabase, esquema, tablas, constraints, índices o datos.
- NO crear migraciones.
- NO usar `create_all`.
- NO insertar datos de prueba en Supabase.
- NO cambiar RLS.
- NO modificar permisos de `rol_funcion`.
- NO acceder a Supabase directamente desde Angular o Flutter.
- NO hacer commit.
- NO hacer push.
- NO cambiar de rama automáticamente.
- NO ejecutar reset/clean destructivo.
- NO eliminar cambios del usuario.
- NO reescribir módulos funcionales existentes si se pueden extender.
- NO crear una segunda arquitectura paralela.

Mantén estrictamente el patrón backend existente:

`Router/API -> Service -> Repository -> SQLAlchemy -> PostgreSQL/Supabase`

---

# FASE 0 — INSPECCIÓN OBLIGATORIA

Antes de escribir código, inspecciona y resume internamente:

## Backend

- `git status`
- rama actual
- árbol de `app/`
- `app/main.py`
- configuración de routers
- autenticación/JWT
- dependencias de autorización
- models SQLAlchemy existentes
- schemas existentes
- repositories/services existentes
- módulos de Agenda/Citas si existen
- tests existentes
- manejo de excepciones
- bitácora

Busca especialmente referencias a:

- `Oftalmologo`
- `HorarioOftalmologo`
- `BloqueoHorario`
- `Cita`
- `agenda`
- `disponibilidad`
- `funcion_id`
- `rol_funcion`

## Angular

Inspecciona:

- estructura `src/app`
- core/services/interceptors/guards
- rutas
- layouts
- menú dinámico
- features ya implementadas
- modelos/interfaces
- servicio de autenticación
- patrón de llamadas HTTP
- manejo visual de loading/error/empty

## Flutter

Inspecciona:

- `lib/`
- arquitectura de features
- cliente HTTP
- autenticación/token
- rutas/navegación
- modelos
- manejo de errores
- componentes reutilizables

### Regla

No inventes nombres de carpetas si el repo ya posee una convención. Adáptate a lo existente.

Si `git status` muestra cambios del usuario, consérvalos. Nunca los descartes.

---

# ESTADO ACTUAL DE BASE DE DATOS

La base real ya fue inspeccionada.

Tablas relevantes existentes:

- `oftalmologo`
- `horario_oftalmologo`
- `bloqueo_horario`
- `cita`
- `paciente`
- `usuario`
- `rol`
- `modulo`
- `funcion`
- `accion`
- `rol_funcion`
- `bitacora`

Estado actual relevante:

- existe 1 oftalmólogo activo;
- `horario_oftalmologo` tiene 0 filas;
- `bloqueo_horario` tiene 0 filas;
- `cita` tiene 0 filas;
- módulo 2 = `Agenda y Citas`;
- función 10 = `Consultar agenda y disponibilidad médica`;
- roles: Administrador, Oftalmólogo, Recepcionista, Paciente;
- actualmente la función 10 está asignada en `rol_funcion` solamente a Administrador con acción `AMBAS`.

No alteres estos datos.

Si la configuración de permisos impide probar otros roles, no hagas bypass: informa al finalizar.

---

# OBJETIVO FUNCIONAL

CU09 debe permitir consultar la agenda y calcular la disponibilidad de un oftalmólogo para una fecha.

La regla central es:

`horario base activo - bloqueos activos - citas no canceladas = intervalos disponibles`

CU09 NO crea ni modifica datos.

---

# REGLAS DE NEGOCIO A IMPLEMENTAR

1. Solo listar oftalmólogos con `estado = true`.
2. Para la fecha seleccionada, obtener su día de semana.
3. Reutilizar una convención existente para `dia_semana`; si no existe, usar ISO `1=Lunes ... 7=Domingo` de forma centralizada y no duplicada.
4. Consultar todos los horarios base activos de ese día.
5. Consultar bloqueos activos del oftalmólogo para esa fecha.
6. Consultar citas del oftalmólogo para esa fecha.
7. Una cita con estado `CANCELADA` no ocupa disponibilidad.
8. Los demás estados almacenados ocupan el rango de `hora_inicio` a `hora_fin`, salvo que el proyecto ya tenga una regla más específica.
9. Restar bloqueos y citas de cada bloque base.
10. Manejar solapamientos correctamente.
11. Nunca devolver intervalos negativos ni invertidos.
12. Ordenar intervalos cronológicamente.
13. Si no hay horario configurado, devolver respuesta válida con disponibilidad vacía.
14. Si todo está ocupado, devolver disponibilidad vacía.
15. No generar slots de 30 minutos ni ninguna duración artificial: devolver rangos libres porque la BD no define duración fija.
16. No exponer datos personales de otros pacientes en la respuesta de disponibilidad para móvil/paciente.

---

# IMPLEMENTACIÓN BACKEND

## Paso 1 — Modelos

Reutiliza models existentes para:

- oftalmólogo
- horario
- bloqueo
- cita
- paciente

Si ya existen, NO los dupliques.

Si falta un mapping SQLAlchemy pero la tabla existe, agrega solamente el mapping mínimo siguiendo exactamente las convenciones del proyecto y sin modificar la BD.

## Paso 2 — Repository

Implementa/extiende repositorios para consultas de lectura:

- listar oftalmólogos activos;
- obtener oftalmólogo activo por id;
- obtener horarios activos por oftalmólogo y día de semana;
- obtener bloqueos activos por oftalmólogo y fecha;
- obtener citas del oftalmólogo por fecha excluyendo `CANCELADA` para cálculo de disponibilidad;
- obtener agenda del día para personal autorizado si corresponde al flujo real del proyecto.

Usa SQLAlchemy 2.x y el manejo de sesiones existente.

## Paso 3 — Service

Implementa la lógica del CU09 en Service, no en Router.

Crea funciones pequeñas y testeables para:

- normalizar intervalos ocupados;
- intersectar/recortar intervalos con un horario base;
- unir intervalos solapados cuando sea necesario;
- restar intervalos ocupados de horarios base;
- construir la respuesta final.

Evita una función monolítica difícil de probar.

## Paso 4 — Schemas

Crea/reutiliza schemas Pydantic para:

- oftalmólogo resumido;
- intervalo horario;
- respuesta de disponibilidad;
- respuesta de agenda si se requiere.

No devuelvas campos sensibles.

## Paso 5 — Router

Integra endpoints de consulta en el router/prefijo real de Agenda y Citas.

Capacidades requeridas:

- listar oftalmólogos activos;
- consultar disponibilidad por oftalmólogo y fecha;
- consultar agenda del día para personal autorizado, si el diseño existente del módulo lo contempla.

Usa las dependencias existentes de autenticación/autorización.

No hardcodees rol mediante strings si el proyecto ya tiene un sistema central de permisos.

## Paso 6 — Registro

Asegura que el router esté incluido en la aplicación sin duplicar includes.

---

# CONTRATO DE DISPONIBILIDAD

Adapta nombres al estilo real del proyecto, pero conserva semántica equivalente.

La respuesta debe poder expresar:

- oftalmólogo consultado;
- fecha;
- horarios base;
- intervalos disponibles.

Ejemplo conceptual:

```json
{
  "oftalmologo": {
    "id": 1,
    "nombres": "Salet",
    "apellidos": "Ejemplo",
    "especialidad": "Oftalmología General"
  },
  "fecha": "2026-09-10",
  "horarios_base": [],
  "intervalos_disponibles": []
}
```

Con la BD real actual, una respuesta vacía de horarios/disponibilidad es válida porque todavía no hay registros en `horario_oftalmologo`.

---

# PRUEBAS BACKEND

Añade pruebas siguiendo el framework y estructura ya utilizados en el repo.

Prueba como mínimo:

1. `08:00-12:00` sin ocupación -> `08:00-12:00`.
2. bloqueo `10:00-11:00` -> `08:00-10:00` y `11:00-12:00`.
3. cita `08:30-09:00` -> `08:00-08:30` y `09:00-12:00`.
4. cita cancelada -> no reduce disponibilidad.
5. cita + bloqueo solapados -> resultado correcto.
6. múltiples horarios base -> resultado ordenado.
7. sin horario -> lista vacía.
8. todo ocupado -> lista vacía.
9. oftalmólogo inexistente -> error esperado.
10. endpoint de disponibilidad no filtra datos sensibles de pacientes.

No inserts datos de test en Supabase compartido.

Después del backend:

- ejecuta tests relevantes;
- ejecuta `python -m compileall app`;
- prueba import de `app.main:app`;
- confirma que los endpoints existentes siguen registrándose.

Corrige solo errores relacionados con tus cambios.

---

# IMPLEMENTACIÓN ANGULAR WEB

Solo después de que el backend esté estable.

## Inspección previa

Reutiliza:

- layouts actuales;
- sidebar/navbar;
- rutas existentes;
- guards;
- interceptor JWT;
- servicios base;
- componentes/form controls;
- estilos del sistema.

## Feature CU09

Implementa una vista de **Agenda y disponibilidad médica** que permita:

1. cargar oftalmólogos activos;
2. seleccionar oftalmólogo;
3. seleccionar fecha;
4. consultar backend;
5. mostrar intervalos disponibles;
6. mostrar agenda/ocupación solo si el endpoint y permisos del usuario lo permiten;
7. mostrar estados loading/error/empty;
8. funcionar correctamente si la base no tiene horarios configurados.

No agregar acciones de reservar, confirmar, reprogramar ni cancelar cita.

No llamar Supabase directamente.

No hardcodear URL de API si ya existe configuración de environment/service.

## Validación Web

- ejecutar `npm run build`;
- corregir errores TypeScript/Angular causados por CU09;
- no hacer refactors masivos no relacionados.

---

# IMPLEMENTACIÓN FLUTTER MÓVIL

Solo después de backend y Web estables, salvo que la estructura del proyecto aconseje otra secuencia.

## Feature CU09 móvil

Implementa una pantalla para consultar disponibilidad que:

1. cargue oftalmólogos activos desde FastAPI;
2. permita elegir uno;
3. permita seleccionar fecha;
4. llame al endpoint de disponibilidad;
5. muestre únicamente intervalos disponibles;
6. muestre estado vacío si no hay horarios;
7. maneje loading/error;
8. no exponga nombres ni datos de pacientes con citas existentes;
9. reutilice autenticación y cliente HTTP actuales.

NO implementar reserva de cita.

## Validación móvil

- ejecutar `flutter analyze`;
- ejecutar tests relevantes si existen;
- realizar la validación de build disponible en el entorno sin cambiar toolchains globales.

---

# SEGURIDAD Y PERMISOS

Respeta el sistema actual.

La función de BD para CU09 ya existe como función 10.

No insertes nuevas filas en `rol_funcion`.

No hagas bypass de seguridad para permitir acceso artificialmente.

Si solo Administrador puede acceder debido a los datos actuales, deja el código compatible con el sistema de permisos y reporta al final:

`BLOQUEO DE CONFIGURACIÓN: función 10 actualmente solo asignada a Administrador en rol_funcion.`

No lo soluciones sin autorización del usuario.

---

# RLS

Supabase actualmente reporta RLS deshabilitado en las 15 tablas `public`.

NO cambies RLS en este CU.

NO migres el frontend a Supabase directo.

Mantén el acceso Frontend -> FastAPI -> SQLAlchemy -> PostgreSQL.

---

# BITÁCORA

Inspecciona cómo se utiliza actualmente.

- Si el proyecto audita consultas equivalentes, reutiliza el mecanismo existente.
- Si no las audita, no agregues eventos nuevos arbitrarios para CU09.

Nunca escribas bitácora desde el frontend.

---

# MANEJO DE ERRORES

Usa el patrón del proyecto.

Debes manejar al menos:

- oftalmólogo inexistente/inactivo;
- fecha inválida;
- sin horario configurado;
- sin disponibilidad;
- error de red en Web/Móvil;
- error de backend sin filtrar stack traces al usuario.

Una falta de horario no es un 500.

---

# DISCIPLINA DE EDICIÓN

Antes de editar un archivo existente:

1. léelo;
2. entiende su propósito;
3. identifica imports y dependencias;
4. conserva comportamiento existente;
5. realiza el cambio mínimo necesario.

Si un parche falla:

- vuelve a leer el archivo;
- adapta el parche al contenido real;
- no reemplaces el archivo completo sin necesidad.

No generes archivos duplicados con sufijos como `_new`, `_final`, `_v2`.

---

# CHECKPOINTS DE TRABAJO

Trabaja en este orden y valida antes de seguir:

### Checkpoint A — Backend lectura

- repositorios listos;
- schemas listos;
- endpoints listos.

### Checkpoint B — Algoritmo

- cálculo de disponibilidad implementado;
- tests del algoritmo aprobados.

### Checkpoint C — Backend completo

- compileall OK;
- import app OK;
- tests relevantes OK.

### Checkpoint D — Angular

- integración API OK;
- build OK.

### Checkpoint E — Flutter

- integración API OK;
- analyze/tests OK.

No continúes a CU10/CU11.

---

# SALIDA FINAL QUE QUIERO DE TI

Cuando termines, responde con un resumen técnico compacto que contenga:

1. **Backend realizado**
   - archivos creados/modificados;
   - endpoints finales;
   - algoritmo implementado.

2. **Web realizado**
   - archivos creados/modificados;
   - ruta/pantalla final.

3. **Móvil realizado**
   - archivos creados/modificados;
   - pantalla/flujo final.

4. **Pruebas ejecutadas**
   - comandos;
   - resultado.

5. **Problemas o bloqueos reales**
   - solo si existen.

6. **Git status final**
   - sin commit ni push.

7. Confirma explícitamente:
   - no se modificó Supabase;
   - no se modificó `.env`;
   - no se hicieron migraciones/DDL;
   - no se hizo commit/push;
   - no se implementó CU10/CU11.

No me des una explicación teórica de Scrum ni documentación académica. Quiero implementación real y validada de CU09.

