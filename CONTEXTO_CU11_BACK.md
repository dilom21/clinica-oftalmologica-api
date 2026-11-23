# CONTEXTO_CU11_BACK.md

# CU11 — Configurar disponibilidad del oftalmólogo

## Proyecto
Repositorio backend oficial:
https://github.com/dilom21/clinica-oftalmologica-api.git

Stack:
- Python 3.12
- FastAPI
- SQLAlchemy
- Pydantic
- PostgreSQL/Supabase
- JWT

Arquitectura:
Router/API → Service → Repository → SQLAlchemy → PostgreSQL

Este contexto es exclusivo para CU11 Web. No desarrollar CU10 ni móvil.

## Estado previo obligatorio
CU09 ya está implementado y corregido. Antes de tocar código:
1. leer MASTER_PROMPT.md;
2. revisar git status;
3. inspeccionar cambios locales no commiteados;
4. preservar CU09;
5. no asumir que GitHub contiene todo lo local.

CU10 lo desarrolla otro integrante. Minimizar cambios innecesarios en archivos compartidos y aislar CU11 cuando sea práctico.

## Módulo existente
app/modules/gestion_agenda_citas/

CU09 ya usa:
- api/router.py
- models/models.py
- repositories/repository.py
- schemas/schemas.py
- services/disponibilidad.py
- services/service.py

Prefijo real:
`/agenda-citas`

No romper ni duplicar CU09.

## Seguridad existente
El backend ya usa:
rol → rol_funcion → funcion → accion

Acciones:
- LECTURA
- ESCRITURA
- AMBAS

requerir_permiso(nombre_funcion, nombre_accion) ya soporta:
- LECTURA acepta LECTURA o AMBAS.
- ESCRITURA acepta ESCRITURA o AMBAS.
- AMBAS acepta solo AMBAS.

Constantes:
- ACCION_LECTURA
- ACCION_ESCRITURA
- ACCION_AMBAS

Helper:
- nombre_rol_actual(usuario)

No crear otro sistema de permisos.

## Función de CU11 en Supabase
Módulo 2: Agenda y Citas
Función 12: Configurar disponibilidad del oftalmólogo
Estado: activa

Configuración actual:
- Administrador → AMBAS

Configuración final deseada:
- Administrador → AMBAS
- Oftalmólogo → AMBAS
- Recepcionista → sin permiso
- Paciente → sin permiso

No modificar Supabase durante el desarrollo. Al terminar reportar SQL idempotente para configurar solo funcion_id=12.

## Reglas por rol

### Administrador
Puede consultar y configurar horarios/bloqueos de cualquier oftalmólogo activo.

### Oftalmólogo
Puede consultar y configurar únicamente su propia disponibilidad.
Validar propiedad con:
`oftalmologo.usuario_id == usuario_autenticado.id`

Nunca confiar en oftalmologo_id enviado por frontend sin validar.

### Recepcionista
Sin acceso a CU11. Usa CU09 y CU10.

### Paciente
Sin acceso a CU11.

## Tablas reales relevantes

### oftalmologo
- id
- usuario_id
- matricula
- nombres
- apellidos
- especialidad
- estado
- fecha_registro

### horario_oftalmologo
- id
- oftalmologo_id
- dia_semana (1=Lunes ... 7=Domingo)
- hora_inicio
- hora_fin
- estado

Representa horario semanal habitual. Puede haber varios intervalos por día.

### bloqueo_horario
- id
- oftalmologo_id
- fecha
- hora_inicio
- hora_fin
- motivo
- estado
- fecha_registro

Representa excepciones específicas.

### cita
- id
- oftalmologo_id
- fecha
- hora_inicio
- hora_fin
- estado

Estados:
PENDIENTE, CONFIRMADA, REPROGRAMADA, CANCELADA, ATENDIDA

Para conflictos:
- CANCELADA no ocupa horario;
- resto se considera según la misma regla usada por CU09.

No exponer datos de paciente en conflictos.

Estado actual de datos:
- oftalmologo: 1
- horario_oftalmologo: 0
- bloqueo_horario: 0
- cita: 0

No insertar datos permanentes de prueba.

## Objetivo CU11
Administrar:
1. horario semanal habitual;
2. bloqueos/excepciones.

CU11 alimenta a CU09:
CU11 configura → CU09 consulta → CU09 calcula disponibilidad.

## Endpoints sugeridos
Respetar `/agenda-citas`.

Consulta:
- GET /agenda-citas/configuracion/oftalmologos
- GET /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}

Horarios:
- POST /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}/horarios
- PUT /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}/horarios/{horario_id}
- PATCH /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}/horarios/{horario_id}/estado

Bloqueos:
- POST /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}/bloqueos
- PUT /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}/bloqueos/{bloqueo_id}
- PATCH /agenda-citas/configuracion/oftalmologos/{oftalmologo_id}/bloqueos/{bloqueo_id}/estado

Se pueden adaptar nombres a las convenciones reales, pero no mezclar CU09/CU10.

## Permisos por endpoint
Función:
`Configurar disponibilidad del oftalmólogo`

GET:
acción mínima LECTURA.

POST/PUT/PATCH:
acción mínima ESCRITURA.

Administrador y Oftalmólogo tendrán AMBAS, por lo que satisfacen ambas.

## Reglas de horario semanal
- dia_semana 1..7.
- hora_inicio < hora_fin.
- no duplicados activos.
- no solapamientos activos del mismo oftalmólogo/día.
- intervalos contiguos pueden permitirse.
- no fusionarlos automáticamente salvo convención existente.

### Modificar/desactivar horario con citas futuras
No permitir que una modificación/desactivación deje una cita futura activa fuera de todos los horarios activos resultantes.

Ejemplo:
Horario lunes 08:00-12:00
Cita futura lunes 10:00-10:30
Desactivar horario dejando la cita sin cobertura
→ 409 Conflict.

Aplicar también a cambio de día o reducción de horas.

No cancelar ni reprogramar citas automáticamente. Eso corresponde a CU10.

## Reglas de bloqueos
- hora_inicio < hora_fin.
- no duplicado activo.
- no solapamiento innecesario con otro bloqueo activo del mismo médico/fecha.
- nuevo bloqueo no puede estar en fecha pasada.
- al actualizar fecha/hora, repetir validaciones.
- debe existir al menos un horario activo de esa fecha con el que el bloqueo tenga intersección.
- puede atravesar más de un intervalo horario.

### Conflicto con citas
Antes de crear/modificar/reactivar bloqueo:
buscar citas del mismo oftalmólogo/fecha con estado != CANCELADA y solapamiento temporal.

Si existe:
→ 409 Conflict.

No cancelar/reprogramar/modificar la cita.
CU10 resolverá después.

## Estado lógico
Preferir `estado=false` en lugar de delete físico.

Al reactivar horario/bloqueo repetir validaciones de conflictos.

## Transacciones
- commit solo si todo es válido;
- rollback ante error;
- no dejar cambios parciales.

## Bitácora
Revisar infraestructura existente y reutilizarla si corresponde.

Acciones sugeridas:
- CREAR_HORARIO_OFTALMOLOGO
- ACTUALIZAR_HORARIO_OFTALMOLOGO
- CAMBIAR_ESTADO_HORARIO_OFTALMOLOGO
- CREAR_BLOQUEO_HORARIO
- ACTUALIZAR_BLOQUEO_HORARIO
- CAMBIAR_ESTADO_BLOQUEO_HORARIO

Usar usuario autenticado y no registrar datos sensibles.

## Códigos HTTP
- 200 consulta/actualización
- 201 creación
- 400/422 validación
- 401 autenticación
- 403 autorización
- 404 recurso inexistente
- 409 conflicto de negocio

## Pruebas mínimas

Autorización:
1. Admin configura cualquiera.
2. Oftalmólogo configura propio.
3. Oftalmólogo no configura otro → 403.
4. Recepcionista → 403.
5. Paciente → 403.
6. GET requiere LECTURA.
7. escritura requiere ESCRITURA.
8. AMBAS satisface ambas.

Horarios:
9. crear válido.
10. hora_fin <= hora_inicio inválido.
11. día inválido.
12. solapamiento rechazado.
13. contiguos permitidos.
14. actualizar válido.
15. desactivar sin citas futuras.
16. rechazar cambio que deje citas futuras fuera.
17. reactivar validando.

Bloqueos:
18. crear válido.
19. pasado rechazado.
20. sin horario activo rechazado.
21. solapamiento con bloqueo rechazado.
22. solapamiento con cita no CANCELADA rechazado.
23. cita CANCELADA no bloquea.
24. actualizar válido.
25. desactivar válido.
26. reactivar validando.

Regresión:
27. CU09 sigue funcionando.
28. CU09 descuenta bloqueos CU11.
29. todos los tests existentes siguen OK.

Usar unittest si pytest no está instalado.

## Verificación final
Ejecutar:
- python -m unittest discover -s tests -p "test_*.py" -v
- python -m compileall app tests
- import from app.main import app
- app.openapi()

Confirmar endpoints CU09 y CU11.

Smoke contra Supabase solo lectura; no escribir datos permanentes.

## Restricciones absolutas
NO:
- modificar .env
- cambiar tablas
- migraciones
- RLS
- fixtures permanentes
- desarrollar CU10
- desarrollar móvil
- rehacer CU09
- commit
- push
- merge

## Archivos IA — NO VERSIONAR
No incluir en ningún commit:
- MASTER_PROMPT.md
- CONTEXTO_CU11_BACK.md
- cualquier CONTEXTO_CU*.md
- archivos equivalentes de prompt/contexto IA

No borrarlos localmente. Solo mantenerlos fuera del staging.

## Reporte final requerido
1. Archivos modificados/creados.
2. Endpoints CU11.
3. Autorización final.
4. Validaciones horarios.
5. Validaciones bloqueos.
6. Conflictos con citas.
7. Bitácora.
8. Tests y resultado.
9. Regresión CU09.
10. Pendientes.
11. SQL idempotente propuesto para funcion_id=12:
   - Administrador → AMBAS
   - Oftalmólogo → AMBAS
12. Confirmar que CONTEXTO/MASTER_PROMPT no están staged.
