# CONTEXTO DEL PROYECTO — BACKEND

## 1. Proyecto

**Nombre:** Centro Oftalmológico Visión Clara  
**Backend:** FastAPI  
**ORM:** SQLAlchemy  
**Driver PostgreSQL:** Psycopg  
**Base de datos:** PostgreSQL alojado en Supabase  
**Frontend:** Angular  
**Móvil:** Flutter

Este archivo sirve como contexto para la IA utilizada dentro de VS Code.

La IA debe respetar la arquitectura modular existente y no crear una estructura alternativa sin justificación.

---

## 2. Arquitectura general

```text
Angular / Flutter
       |
       | HTTP/JSON
       v
FastAPI
       |
       v
Services
       |
       v
Repositories
       |
       v
SQLAlchemy
       |
       v
PostgreSQL / Supabase
```

La lógica de seguridad y negocio se ejecuta en FastAPI.

---

## 3. Estructura actual

```text
app/
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── dependencies.py
│   ├── exceptions.py
│   └── security.py
│
├── database/
│   ├── __init__.py
│   ├── base.py
│   ├── connection.py
│   └── session.py
│
├── modules/
│   ├── gestion_agenda_citas/
│   ├── gestion_historial_clinico/
│   ├── gestion_inventario_proveedores/
│   ├── gestion_pacientes/
│   ├── gestion_pagos/
│   ├── gestion_usuarios_seguridad/
│   ├── notificaciones_interaccion_chatbot/
│   └── reportes_panel_administrativo/
│
├── shared/
├── __init__.py
└── main.py
```

Cada módulo utiliza una estructura similar:

```text
modulo/
├── api/
├── casos_uso/
├── models/
├── repositories/
├── schemas/
├── services/
└── __init__.py
```

Respetar esta estructura.

---

## 4. Responsabilidad de cada capa

### `api/`

Define endpoints FastAPI.

Debe:

- recibir requests
- usar `Depends(get_db)`
- invocar services
- devolver responses

No debe contener SQL ni lógica compleja.

### `schemas/`

DTO/Pydantic.

Debe validar:

- datos de entrada
- datos de salida
- tipos
- campos opcionales

### `models/`

Modelos SQLAlchemy que representan tablas PostgreSQL.

### `repositories/`

Acceso directo a datos.

Debe:

- hacer SELECT
- INSERT
- UPDATE
- consultas SQLAlchemy

No debe decidir reglas complejas de negocio.

### `services/`

Lógica del caso de uso.

Debe:

- validar reglas
- coordinar repositories
- registrar bitácora
- manejar transacciones
- hacer `commit()` / `rollback()`

### `casos_uso/`

Reservado para organización adicional de lógica específica de casos de uso cuando sea necesario.

---

## 5. Conexión a Supabase

La conexión ya fue comprobada correctamente.

Variable:

```env
DATABASE_URL=postgresql+psycopg://...
```

Nunca subir `.env` al repositorio.

`.gitignore` debe incluir:

```gitignore
.env
__pycache__/
*.pyc
```

Archivos principales:

```text
app/core/config.py
app/database/connection.py
app/database/session.py
app/database/base.py
```

`connection.py` crea el engine SQLAlchemy.

`session.py` contiene `SessionLocal` y `get_db()`.

`base.py` contiene `Base(DeclarativeBase)`.

---

## 6. Flujo de transacciones

Los repositories NO deben hacer `commit()`.

Ejemplo correcto:

```text
Service
 |
 +--> repository.crear_paciente()
 |
 +--> repository.registrar_bitacora()
 |
 +--> db.commit()
```

Si ocurre un error:

```python
db.rollback()
```

Esto permite que una operación compuesta sea atómica.

---

## 7. Casos de uso iniciales

```text
CU01 - Iniciar sesión
CU02 - Cerrar sesión
CU03 - Recuperar contraseña
CU04 - Gestionar usuarios
CU05 - Gestionar roles y permisos
CU06 - Consultar bitácora del sistema
CU07 - Gestionar pacientes
```

El desarrollo actual está centrado en estos siete CU.

---

## 8. Tablas PostgreSQL actuales para CU01-CU07

```text
usuario
rol
usuario_rol
modulo
funcion
accion
rol_funcion
token_recuperacion
bitacora
paciente
```

---

## 9. Tabla `usuario`

Campos principales:

```text
id
correo
password_hash
estado
fecha_creacion
```

Reglas:

- correo único ignorando mayúsculas/minúsculas
- contraseña nunca se almacena en texto plano
- desactivar usuarios preferentemente mediante `estado = false`
- evitar DELETE físico salvo que esté justificado

---

## 10. Roles y permisos

Estructura:

```text
usuario
   |
usuario_rol
   |
  rol
   |
rol_funcion
   |       \
 funcion   accion
   |
 modulo
```

Un usuario puede tener uno o varios roles.

Los permisos se modelan mediante:

```text
rol + funcion + accion
```

Acciones recomendadas:

```text
LECTURA
ESCRITURA
```

Si un rol tiene ambas capacidades, recibe ambas acciones; no es necesario crear una acción `AMBAS`.

---

## 11. Recuperación de contraseña

Tabla:

```text
token_recuperacion
```

Campos:

```text
id
usuario_id
token_hash
fecha_creacion
fecha_expiracion
usado
```

Reglas:

- no guardar el token real
- almacenar un hash
- validar expiración
- marcar `usado = true` después de restablecer la contraseña
- invalidar/rechazar tokens ya utilizados

CU03 todavía debe completarse.

---

## 12. Bitácora

Tabla:

```text
bitacora
```

Campos:

```text
id
usuario_id
fecha_hora
ip
accion
entidad_afectada
id_registro_afectado
descripcion
```

Debe registrar acciones relevantes, por ejemplo:

```text
LOGIN
LOGOUT
CREAR_USUARIO
ACTUALIZAR_USUARIO
ASIGNAR_ROL
CREAR_PACIENTE
ACTUALIZAR_PACIENTE
DESACTIVAR_PACIENTE
```

Los services coordinan la operación de negocio y el registro en bitácora antes del `commit()`.

---

## 13. Paciente

Tabla:

```text
paciente
```

Campos:

```text
id
usuario_id
nombres
apellidos
ci
fecha_nacimiento
sexo
telefono
contacto_emergencia
fecha_registro
direccion
estado
```

Reglas:

- `usuario_id` es opcional
- un paciente puede existir sin una cuenta de usuario
- `ci` debe ser único cuando no sea NULL
- eliminación preferentemente lógica (`estado = false`)

---

## 14. Índices definidos

Se han considerado índices para acelerar consultas frecuentes.

### Usuario

```text
LOWER(correo)
```

### Usuario-Rol

```text
rol_id
```

### Función

```text
modulo_id
```

### Rol-Función

```text
rol + funcion + accion
funcion_id
accion_id
```

### Recuperación

```text
token_hash
usuario_id + fecha_expiracion para tokens no usados
```

### Bitácora

```text
fecha_hora
usuario_id + fecha_hora
accion + fecha_hora
entidad_afectada + id_registro_afectado
```

### Paciente

```text
ci
LOWER(apellidos) + LOWER(nombres)
telefono
```

No crear índices indiscriminadamente.

---

## 15. Supabase y RLS

Actualmente la arquitectura usa:

```text
Cliente -> FastAPI -> PostgreSQL/Supabase
```

Por esta razón las tablas fueron creadas sin RLS para el acceso PostgreSQL directo del backend.

No exponer:

- `DATABASE_URL`
- contraseña PostgreSQL
- service role
- credenciales administrativas

en Angular o Flutter.

---

## 16. Seguridad de contraseñas

Actualmente se utiliza `pwdlib` con el algoritmo recomendado (Argon2).

`app/core/security.py` contiene funciones conceptualmente equivalentes a:

```python
hash_password(password)
verificar_password(password, password_guardada)
```

Nunca retornar `password_hash` en schemas públicos.

---

## 17. JWT

CU01 requiere completar autenticación JWT.

Flujo esperado:

```text
POST login
    |
buscar usuario por correo
    |
verificar estado
    |
verificar password
    |
generar access_token
    |
retornar JWT
```

Posteriormente los endpoints protegidos deben utilizar una dependency para identificar al usuario autenticado y validar permisos.

No confiar únicamente en guards de Angular.

---

## 18. CU02 Cerrar sesión

Con access tokens JWT simples, cerrar sesión inicialmente puede realizarse eliminando el token del cliente.

Si el proyecto requiere revocación inmediata, refresh tokens o múltiples sesiones, implementar una tabla/control de sesiones en un cambio posterior y documentado.

No inventar tablas adicionales sin justificar la necesidad.

---

## 19. Endpoints actualmente preparados

```text
GET    /seguridad/usuarios
POST   /seguridad/usuarios
GET    /seguridad/usuarios/{usuario_id}

GET    /seguridad/roles
POST   /seguridad/roles
POST   /seguridad/usuarios/asignar-rol

GET    /seguridad/bitacora

GET    /pacientes
POST   /pacientes
GET    /pacientes/{paciente_id}
PUT    /pacientes/{paciente_id}
DELETE /pacientes/{paciente_id}
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

Los endpoints existen como base inicial y deben probarse contra Supabase.

---

## 20. Estado actual de desarrollo

Ya se ha comprobado:

```text
FastAPI inicia correctamente
Supabase/PostgreSQL conecta correctamente
SQLAlchemy funciona
Models importan correctamente
Schemas importan correctamente
Repositories importan correctamente
Services importan correctamente
Routers aparecen en Swagger
```

Pendiente inmediato:

```text
probar endpoints reales
completar CU01 con JWT
definir CU02 según estrategia de sesión
implementar CU03 recuperación de contraseña
terminar CRUD/reglas de CU04-CU07
aplicar autorización por roles/permisos
```

---

## 21. Comandos útiles

Activar entorno:

```bash
conda activate clinica-api
```

Instalar dependencias:

```bash
pip install -r requirements.txt
```

Levantar backend:

```bash
uvicorn app.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

---

## 22. Reglas para la IA

La IA debe:

1. Revisar archivos existentes antes de crear nuevos.
2. Mantener la arquitectura modular.
3. No mover lógica SQL a routers.
4. Mantener reglas de negocio en services.
5. Mantener acceso a datos en repositories.
6. Usar schemas Pydantic para entrada/salida.
7. Usar models SQLAlchemy para persistencia.
8. No hacer `commit()` en repositories.
9. Registrar en bitácora operaciones importantes.
10. Usar eliminación lógica cuando corresponda.
11. No almacenar contraseñas en texto plano.
12. No exponer `password_hash`.
13. No hardcodear secretos.
14. Mantener compatibilidad PostgreSQL/Supabase.
15. No crear tablas con `Base.metadata.create_all()` si la estructura física ya está gestionada en Supabase mediante SQL.
16. No modificar otros módulos si el caso de uso no los necesita.
17. Mantener nombres de tablas y columnas existentes.
18. Antes de cambiar una FK o tabla, explicar el impacto.
19. No inventar campos que no estén respaldados por el diseño o requerimiento sin avisar.
20. Implementar de forma incremental y probar cada capa.

---

## 23. Proceso recomendado para implementar un caso de uso

```text
1. Leer el requerimiento/CU
2. Identificar tablas implicadas
3. Revisar models existentes
4. Crear/ajustar schemas
5. Crear/ajustar repository
6. Implementar service
7. Registrar bitácora si corresponde
8. Crear router/endpoint
9. Añadir seguridad/autorización
10. Probar en Swagger
11. Verificar datos en Supabase
12. Integrar con frontend
```

---

## 24. Instrucción final para la IA

Cuando el usuario pida desarrollar un caso de uso:

1. No empezar escribiendo código inmediatamente.
2. Indicar qué tablas y archivos existentes participan.
3. Indicar qué archivos se modificarán o crearán.
4. Explicar brevemente el flujo.
5. Implementar respetando las capas.
6. No afectar casos de uso ya funcionales.
7. Dar comandos/pruebas para verificar el resultado.

Si falta información funcional del CU, solicitar únicamente la información realmente necesaria antes de cambiar el modelo de datos.
