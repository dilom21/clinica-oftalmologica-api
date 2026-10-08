# CONTEXTO_SAAS_8A_BACKUP_MANUAL_TENANT.md

## Proyecto
Clínica Oftalmológica — SI2

## Repositorio objetivo
`C:\SI2_Proyecto\clinica-oftalmologica-api`

## Estado previo confirmado
El PASO 7 SaaS + Multitenant está cerrado:

- 7 empresas.
- 7 tenants físicos.
- 7 tenant_database ACTIVA.
- 7 provisionamientos COMPLETADO.
- Login tenant real.
- Aislamiento cross-tenant.
- SaaS Admin real.
- Dashboard SaaS real.
- Suspensión/reactivación.
- Selector multiempresa.
- Cambio seguro de empresa con nuevo login/JWT.
- "Abrir empresa" desde SaaS sin impersonación.

No hay que modificar el diseño multitenant existente.

---

# PASO 8 — BACKUP / RESTORE POR TENANT

Se divide en:

- 8A: Backup manual real por tenant.
- 8B: Backup automático programado.
- 8C: Restore seguro por tenant.
- 8D: Frontend SaaS + E2E backup/restore.

Este documento cubre SOLO 8A.

---

# 1. Objetivo 8A

Implementar backup MANUAL real de una base tenant utilizando PostgreSQL:

`pg_dump -Fc`

El backup debe ser por empresa/tenant, trazable, verificable y aislado.

NO es válido:
- exportar JSON;
- copiar tablas con ORM;
- generar SQL parcial;
- respaldar Control Plane en lugar del tenant;
- usar `public` de la base principal como si fuera un tenant.

---

# 2. Origen del backup

El SaaS Admin selecciona una empresa.

Backend:

empresa
→ saas_control.tenant_database
→ database_name validado
→ conexión administrativa segura
→ `pg_dump -Fc <tenant_database>`

Nunca aceptar `database_name` arbitrario desde el cliente.

El cliente debe enviar `empresa_id` o identificador seguro de Control Plane.

---

# 3. Seguridad

Obligatorio:

- solo JWT `token_type=saas_admin`;
- tenant JWT rechazado;
- legacy JWT rechazado;
- database_name obtenido del Control Plane;
- validar nombre con el helper estricto ya existente;
- no imprimir DATABASE_URL;
- no imprimir password;
- no pasar password por CLI si puede evitarse;
- usar variables de entorno del subprocess de forma segura;
- no devolver rutas internas completas al frontend;
- no exponer contenido del dump.

---

# 4. Formato

Usar:

`pg_dump -Fc`

Opciones recomendadas:

- `--format=custom`
- `--no-owner`
- `--no-privileges`

El dump debe incluir el tenant completo.

No usar `--schema-only`.

No usar dumps de otro tenant.

---

# 5. Staging

El dump puede generarse primero en un directorio temporal seguro.

Ejemplo conceptual:

`tempfile.TemporaryDirectory()`

Nombre lógico:

`<empresa_codigo>_<timestamp>.dump`

No confiar en nombres enviados por el cliente.

Después:
- calcular tamaño;
- SHA-256;
- validar que el archivo existe y no está vacío.

No conservar archivos temporales tras fallo.

---

# 6. Persistencia del backup

Diseñar abstracción de almacenamiento para no acoplar backup a filesystem local.

Ejemplo:

`BackupStorage`

Métodos:
- put(...)
- get(...)
- delete(...)
- exists(...)

Implementar inicialmente un provider local seguro para desarrollo si hace falta, PERO dejar el contrato preparado para almacenamiento privado de producción.

NO usar el bucket público `product-images`.

El storage productivo final será privado.

No crear todavía bucket público.

---

# 7. Metadata Control Plane

Agregar metadata de backups en `saas_control`.

Crear migración SQL nueva, sin alterar scripts ya ejecutados.

Nombre sugerido:

`database/saas_control/005_create_backup_tenant.sql`

Tabla sugerida:

`saas_control.backup_tenant`

Campos mínimos:

- id BIGINT
- empresa_id FK
- tenant_database_id FK
- tipo (`MANUAL`, luego `AUTOMATICO`, `PRE_RESTORE`)
- estado (`PENDIENTE`, `EN_PROCESO`, `COMPLETADO`, `ERROR`)
- storage_key
- nombre_archivo
- formato
- size_bytes
- sha256
- version_schema
- fecha_inicio
- fecha_fin
- creado_por_saas_usuario_id
- mensaje_error saneado

No guardar:
- password;
- DATABASE_URL;
- host secreto;
- dump binario en PostgreSQL;
- tokens.

Agregar índices útiles.

SQL debe ser idempotente o tener verificación segura según patrón del proyecto.

---

# 8. Estado y transacción

Flujo manual:

1. validar SaaS Admin;
2. resolver empresa/tenant;
3. validar empresa ACTIVA;
4. validar tenant_database ACTIVA;
5. registrar backup EN_PROCESO;
6. ejecutar pg_dump;
7. verificar archivo;
8. calcular SHA-256;
9. almacenar;
10. marcar COMPLETADO;
11. registrar bitácora SaaS.

Si falla:

- limpiar temporal;
- marcar ERROR;
- guardar mensaje saneado;
- no cambiar estado del tenant;
- no dejar metadata como COMPLETADO.

El backup NO debe bloquear permanentemente el tenant.

---

# 9. Bitácora SaaS

Registrar acción:

`CREAR_BACKUP_MANUAL`

Metadata genérica:
- empresa;
- backup_id;
- resultado.

No guardar rutas sensibles, tokens ni credenciales.

---

# 10. Endpoints 8A

Agregar al módulo SaaS Admin, preferentemente:

`POST /saas/backups`

Body sugerido:

```json
{
  "empresa_id": 1
}
```

Respuesta segura:

- id
- empresa
- tipo
- estado
- nombre_archivo
- formato
- size_bytes
- sha256 (puede mostrarse completo)
- version_schema
- fecha_inicio
- fecha_fin

Además:

`GET /saas/backups`

Filtros opcionales:
- empresa_id
- estado
- tipo

Y:

`GET /saas/backups/{backup_id}`

NO implementar restore todavía.

NO implementar descarga pública sin autorización.

---

# 11. Concurrencia

Evitar dos backups manuales simultáneos del mismo tenant.

Puede usarse:
- lock en proceso;
- estado EN_PROCESO consultado de forma transaccional;
- advisory lock PostgreSQL si encaja.

No bloquear backups de tenants distintos si no es necesario.

---

# 12. PostgreSQL client

Reutilizar la infraestructura segura ya creada en:

`scripts/saas/provision_tenant.py`

especialmente:
- resolución de pg binaries;
- ejecución subprocess;
- timeout;
- URL segura;
- database identifier validation.

NO copiar enormes bloques si puede extraerse una utilidad reutilizable.

Debe soportar PostgreSQL client 18 local aunque servidor sea PostgreSQL 17.

---

# 13. Verificación del dump

Antes de COMPLETADO:

- archivo existe;
- size > 0;
- SHA-256 calculado;
- opcionalmente `pg_restore --list` debe poder leerlo.

Preferido:
`pg_restore --list <archivo>`

Si `pg_restore --list` falla:
backup ERROR.

Esto NO restaura nada.

---

# 14. Tests

Cubrir:

- SaaS JWT requerido;
- tenant JWT rechazado;
- legacy JWT rechazado;
- empresa inexistente;
- empresa suspendida;
- tenant inactivo;
- database_name inválido;
- `pg_dump -Fc` construido correctamente;
- no password en CLI/log;
- timeout;
- pg_dump error;
- archivo vacío;
- SHA-256;
- pg_restore --list validation;
- storage success;
- storage failure;
- metadata COMPLETADO;
- metadata ERROR;
- bitácora;
- no doble backup simultáneo del mismo tenant;
- tenants distintos sí pueden aislarse;
- listado backups;
- detalle;
- no secrets en responses.

---

# 15. Ejecución real

NO ejecutar backup real desde OpenCode sin instrucción posterior.

Primero:
- implementar;
- testear;
- auditar;
- entregar comando/endpoint exacto.

Después el usuario hará un backup manual REAL de un tenant de demostración.

Tenant recomendado:
`MEDICO-OCULAR`

porque ya se usó en pruebas E2E.

---

# 16. No hacer

NO:

- restore;
- backup automático;
- frontend;
- cron;
- tocar datos clínicos;
- modificar los 7 DBs;
- DROP;
- commit;
- push;
- merge;
- cambiar `.env` todavía;
- crear bucket público.

---

# 17. Criterio de cierre 8A

- tabla metadata backup;
- servicio manual;
- `pg_dump -Fc`;
- SHA-256;
- validación `pg_restore --list`;
- storage abstraction;
- metadata saneada;
- bitácora;
- endpoints SaaS;
- seguridad;
- tests;
- luego 1 backup real exitoso.
