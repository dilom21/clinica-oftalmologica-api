# MASTER_PROMPT_CU16_BACK.md

## 📍 DÓNDE EJECUTAR

**Repositorio:** `clinica-oftalmologica-api`  
**Ruta:** `C:\SI2_Proyecto\clinica-oftalmologica-api`  
**Ubicación:** RAÍZ DEL REPOSITORIO BACKEND

NO ejecutar desde:

- `C:\SI2_Proyecto`
- `clinica-oftalmologica-web`
- `clinica-oftalmologica-mobile`

# IMPLEMENTACIÓN BACKEND — CU16 REGISTRAR DIAGNÓSTICO

Quiero que implementes completamente el backend del **CU16 – Registrar diagnóstico**.

CU15 ya está implementado y probado.

Antes de modificar código debes leer:

1. `CONTEXTO_CU16_BACK.md`
2. este archivo `MASTER_PROMPT_CU16_BACK.md`
3. el código LOCAL actual

No asumas que GitHub refleja exactamente el estado local.

## 1. REGLAS PRINCIPALES

NO modificar:

- Supabase;
- frontend;
- móvil;
- `.env`;
- despliegues;
- credenciales;
- otros casos de uso salvo código compartido estrictamente necesario.

NO:

- hacer commit;
- hacer push;
- hacer merge;
- crear migraciones;
- crear SQL;
- inventar columnas;
- agregar CIE-10;
- agregar `tipo` de diagnóstico;
- crear otra tabla de diagnóstico;
- reescribir CU15;
- refactorizar masivamente la arquitectura.

## 2. SUPABASE YA ESTÁ PREPARADO

No debes conectarte ni modificar Supabase.

Tabla real existente:

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

FK:

```text
consulta_clinica_id
→ consulta_clinica.id
ON DELETE CASCADE
```

Índice:

```text
idx_diagnostico_consulta
```

Permiso existente:

```text
Registrar diagnóstico
```

Roles:

```text
Oftalmólogo   → ESCRITURA
Administrador → AMBAS
```

NO crear nada en la BD.

## 3. INSPECCIÓN OBLIGATORIA

Antes de programar, lee como mínimo:

```text
app/modules/gestion_historial_clinico/models/models.py
app/modules/gestion_historial_clinico/schemas/schemas.py
app/modules/gestion_historial_clinico/repositories/repository.py
app/modules/gestion_historial_clinico/services/service.py
app/modules/gestion_historial_clinico/api/router.py

app/modules/gestion_agenda_citas/models/models.py
app/modules/gestion_agenda_citas/repositories/repository.py

app/modules/gestion_usuarios_seguridad/repositories/repository.py

app/core/dependencies.py

tests/test_cu15_consulta_clinica.py
```

Busca también cualquier archivo local ya relacionado con:

```text
CU16
diagnostico
Diagnostico
```

Si existe una implementación parcial, reutilízala y corrígela; NO crees una implementación paralela.

## 4. MANTENER ARQUITECTURA

Seguir:

```text
Router
→ Service
→ Repository
→ SQLAlchemy
→ PostgreSQL
```

Implementar CU16 dentro de:

```text
app/modules/gestion_historial_clinico/
```

No mover CU15.

No llenar `casos_uso/` solo por existir si el resto del módulo no la utiliza.

## 5. MODELO DIAGNOSTICO

Si no existe, crear `Diagnostico` en el archivo de modelos del historial clínico.

Debe mapear EXACTAMENTE:

```text
id
consulta_clinica_id
nombre
descripcion
fecha_diagnostico
estado
```

Respetar:

```text
nombre varchar(150) NOT NULL
consulta_clinica_id FK NOT NULL
```

No agregar:

```text
codigo_cie10
tipo
oftalmologo_id
paciente_id
```

Agregar relationships solamente si ayudan y son coherentes con los modelos existentes.

## 6. SCHEMAS

Crear, si no existen:

```text
DiagnosticoCrear
DiagnosticoRespuesta
```

Preferencia para `DiagnosticoCrear`:

```text
nombre
descripcion
```

Validaciones:

- trim;
- nombre mínimo 1;
- nombre máximo 150;
- descripcion opcional;
- `extra="forbid"`.

Si `consulta_id` se toma de la URL, el body NO debe aceptar:

```text
consulta_clinica_id
```

Tampoco debe aceptar:

```text
oftalmologo_id
usuario_id
estado
fecha_diagnostico
```

## 7. ENDPOINT PRINCIPAL

Si no existe un patrón local más apropiado, implementar:

```http
POST /historial-clinico/consultas/{consulta_id}/diagnosticos
```

Response:

```text
DiagnosticoRespuesta
```

Status:

```text
201
```

Proteger con:

```text
requerir_permiso("Registrar diagnóstico", ACCION_ESCRITURA)
```

## 8. ENDPOINT DE LECTURA DE APOYO

Si encaja con el patrón existente, implementar:

```http
GET /historial-clinico/consultas/{consulta_id}/diagnosticos
```

para recuperar diagnósticos activos de una consulta.

Debe usar permiso de lectura coherente con historial clínico, preferentemente:

```text
Consultar historial clínico
LECTURA
```

No crear un nuevo permiso de lectura si el existente cumple la responsabilidad.

## 9. RESOLVER OFTALMÓLOGO

Reutilizar el mecanismo ya probado en CU15:

```text
Usuario autenticado
→ validar rol/actor según patrón CU15
→ obtener_oftalmologo_activo_por_usuario_id(usuario.id)
```

NO recibir `oftalmologo_id` desde body/path/query.

## 10. VALIDAR CONSULTA

Obtener una consulta clínica activa por `consulta_id`.

Si no existe o está inactiva:

```text
404
```

Luego verificar:

```text
consulta.oftalmologo_id == oftalmologo.id
```

Si no pertenece al oftalmólogo:

```text
403
```

## 11. MÚLTIPLES DIAGNÓSTICOS

Debe soportar:

```text
Consulta 100
├── Diagnóstico A
├── Diagnóstico B
└── Diagnóstico C
```

NO agregar `UNIQUE(consulta_clinica_id)`.

NO imponer diagnóstico principal/secundario.

## 12. REPOSITORY

Implementar/reutilizar equivalentes a:

```text
obtener_consulta_activa_por_id
crear_diagnostico
listar_diagnosticos_por_consulta
obtener_diagnostico_por_id (solo si se necesita)
```

Repositories:

- no hacen commit;
- usan flush/refresh cuando corresponda;
- no conocen HTTP.

## 13. SERVICE

Crear equivalente a:

```text
registrar_diagnostico(db, consulta_id, datos, usuario)
```

Flujo:

```text
validar usuario/oftalmólogo
→ obtener consulta activa
→ validar propiedad del oftalmólogo
→ crear diagnóstico
→ registrar bitácora
→ commit
→ refresh
→ response
```

Ante error después de comenzar escrituras:

```text
rollback
```

Un solo commit lógico.

## 14. BITÁCORA

Usar el patrón actual:

```text
accion = "REGISTRAR_DIAGNOSTICO"
entidad_afectada = "diagnostico"
id_registro_afectado = diagnostico.id
descripcion = "Diagnóstico registrado"
```

La bitácora y el diagnóstico deben persistirse en la misma transacción.

## 15. FECHA Y ESTADO

Al crear:

```text
fecha_diagnostico = fecha/hora UTC actual
estado = true
```

Seguir el patrón real del proyecto.

No crear endpoints de desactivación.

## 16. NO INVENTAR REGLAS

NO bloquear por:

- diagnóstico parecido;
- diagnóstico repetido;
- cantidad máxima;
- cita asociada/no asociada;
- CIE-10;
- principal/secundario.

## 17. TESTS OBLIGATORIOS

Crear/completar:

```text
tests/test_cu16_registrar_diagnostico.py
```

Cubrir como mínimo:

1. POST válido → 201;
2. asociación con consulta;
3. fecha generada;
4. estado true;
5. descripcion null válida;
6. varios diagnósticos por consulta;
7. consulta inexistente → 404;
8. consulta inactiva → 404;
9. consulta de otro oftalmólogo → 403;
10. usuario no oftalmólogo → rechazado;
11. oftalmólogo sin perfil activo → rechazado;
12. rol sin permiso → 403;
13. nombre vacío → 422;
14. nombre solo espacios → 422;
15. nombre > 150 → 422;
16. `oftalmologo_id` extra → 422;
17. `consulta_clinica_id` extra → 422 si id va en path;
18. `estado` extra → 422;
19. bitácora correcta;
20. rollback si falla bitácora;
21. repository no hace commit;
22. GET lista diagnósticos si se implementó;
23. GET consulta inexistente según diseño;
24. CU15 sigue operativo.

No eliminar tests existentes.

## 18. PRUEBAS

Primero:

```text
pytest tests/test_cu16_registrar_diagnostico.py -v
```

Después:

```text
pytest -q
```

o el comando equivalente con el `.venv` real.

Si falla algo, corregir y volver a ejecutar.

## 19. RESTRICCIONES FINALES

NO modificar Supabase.

NO modificar Angular.

NO modificar móvil.

NO implementar CU17.

NO implementar IA.

NO implementar reportes.

NO agregar dependencias sin necesidad.

NO hacer git commit/push/merge.

# REPORTE FINAL OBLIGATORIO

Al terminar entrega exactamente:

# Reporte CU16 Backend

## 1. Archivos creados

Ruta | Propósito

## 2. Archivos modificados

Ruta | Cambio

## 3. Modelo de datos

Explica `Diagnostico` y relación con `ConsultaClinica`.

## 4. Flujo final de CU16

```text
JWT
→ Usuario
→ Oftalmólogo
→ Consulta clínica
→ Diagnóstico
→ Bitácora
→ Commit
```

## 5. Endpoints

Método | Ruta | Permiso | Request | Response

## 6. Validaciones implementadas

Lista completa.

## 7. Seguridad

Confirmar:

- oftalmólogo sale del JWT/usuario;
- no se acepta `oftalmologo_id`;
- consultas ajenas se rechazan.

## 8. Transacción y bitácora

Commit/rollback.

## 9. Múltiples diagnósticos

Confirmar relación 1:N.

## 10. Tests CU16

Cantidad, comando y resultado.

## 11. Suite general

Comando y resultado.

## 12. Deuda técnica / bloqueos

Solo problemas comprobados fuera de alcance.

## 13. Estado final

```text
CU16 backend: COMPLETO
```

o

```text
CU16 backend: INCOMPLETO
```

Si es incompleto, indicar exactamente qué falta.

## REGLA FINAL

Primero inspeccionar.

Después implementar.

Después probar.

Después corregir.

Finalmente entregar el reporte.

No finalizar solo porque el código compila.
