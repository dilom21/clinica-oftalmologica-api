# Runbook operativo — Restore SaaS y bases retenidas

**Ámbito:** módulo `app/modules/administracion_saas/restore_service.py` y bases físicas
`tenant_*` del proyecto Clínica Oftalmológica.
**Audiencia:** SUPERADMIN SaaS y DBA/operador autorizado.
**Última revisión:** 2026-11-16.

> **Advertencia absoluta:** este runbook no autoriza a ensayar, renombrar, eliminar ni
> restaurar sobre las siete bases productivas. Cualquier prueba debe hacerse en una
> instancia PostgreSQL aislada y desechable con credenciales exclusivas. Las acciones
> de este documento se ejecutan solo durante un incidente real, con las autorizaciones
> de la sección 9 y evidencia registrada.

---

## 1. Modelo mental del restore

El restore valida primero una base temporal y después hace un intercambio por
renombrados. PostgreSQL **no** ofrece atomicidad entre dos `ALTER DATABASE ... RENAME`;
por eso el protocolo es recuperable, no transaccional.

Secuencia normal con swap habilitado (`SAAS_RESTORE_ALLOW_DATABASE_SWAP=1` y rol con
capacidad de crear bases):

1. Se reserva un único restore por tenant.
2. Se materializa y verifica el archivo (tamaño y SHA-256) y la versión de esquema.
3. Se crea un backup PRE_RESTORE obligatorio.
4. Se crea una base temporal `restore_<uuid>` y se restaura/valida allí.
5. La base canónica `tenant_*` se renombra a `restore_backup_<uuid>`.
6. La temporal se renombra al nombre canónico `tenant_*`.
7. Se validan estructura, health, ledger y metadatos del nuevo destino.
8. Solo entonces se elimina `restore_backup_<uuid>`.
9. Si el `DROP` del respaldo falla, el destino queda activo y el respaldo se conserva:
   el restore termina `COMPLETADO` con etapa `COMPLETADO_RESPALDO_RETENIDO`.

Si no hay swap (sin opt-in o sin capacidad probada), el restore es in-place con
`restore_exact` y rollback desde el PRE_RESTORE. En ese modo no se crean bases
`restore_*` ni `restore_backup_*`.

### 1.1 Estados y etapas

`restore_tenant.estado` (CHECK de la tabla):

| Estado | Significado |
|---|---|
| `PENDIENTE` | Restore reservado; aún no hay ventana destructiva. |
| `EN_PROCESO` | Restore en curso; el tenant está `RESTAURANDO`. |
| `ROLLBACK_EN_PROCESO` | Falló el in-place y se está restaurando el PRE_RESTORE. |
| `COMPLETADO` | Destino validado y tenant reactivado. Puede conservar respaldo. |
| `ERROR` | Falló; revisar `etapa`, `mensaje_error` y bases retenidas. |

`restore_tenant.etapa` es texto libre (`VARCHAR(50)`) y describe el último paso:

| Etapa | Interpretación operativa |
|---|---|
| `VALIDACION` | Falló la lectura/verificación del archivo. Nada se modificó. |
| `VALIDACION_VERSION` | Versión de esquema desconocida o incompatible. Nada se modificó. |
| `PRE_RESTORE` | Falló el backup previo obligatorio. Nada se modificó. |
| `PREFLIGHT` | El esquema in-place no era reseteable con seguridad. Nada se modificó. |
| `PREPARACION` | Falló el cierre de sesiones, la sonda de capacidades o cualquier paso previo sin mutar la base. La reserva se cerró en `ERROR` y el tenant sigue `ACTIVA`. |
| `RESTORE` | Marca de inicio de la ventana destructiva. |
| `CREACION_TEMPORAL` | No se pudo crear la temporal; la canónica sigue intacta. |
| `VALIDACION_TEMPORAL` | La temporal no validó; se intentó eliminarla; la canónica sigue intacta. |
| `RESERVA_RESPALDO` | No se pudo reservar nombre de respaldo; temporal retenida. |
| `RENOMBRE_ACTIVA` | No se renombró la canónica; canónica y temporal retenidas. |
| `RENOMBRE_TEMPORAL` | Falló la promoción; se recuperó el nombre canónico y se conserva la temporal. |
| `RECUPERACION_RENOMBRE` | Fallaron promoción y recuperación; canónica puede faltar. Retener todo. |
| `VALIDACION_DESTINO` | El destino nuevo no validó; se recuperó la original y se retiene la nueva. |
| `RECUPERACION_DESTINO` | Falló validación y recuperación; retener todo. |
| `METADATOS` | Falló la actualización de Control Plane; se recuperó la original. |
| `RECUPERACION_METADATOS` | Fallaron metadatos y recuperación; retener todo. |
| `COMPLETADO` | Restore y metadatos correctos. |
| `COMPLETADO_RESPALDO_RETENIDO` | Destino correcto; el respaldo anterior sigue en disco. |
| `ABORTADO` | El tenant dejó de estar disponible antes del cutover. |
| `ROLLBACK`, `ROLLBACK_COMPLETADO`, `ROLLBACK_ERROR` | Rollback in-place desde PRE_RESTORE. |

Estados del tenant: `ACTIVA`, `RESTAURANDO`, `ERROR`.

---

## 2. Identificar las bases

| Patrón | Origen | Uso |
|---|---|---|
| `tenant_<empresa>` | `saas_control.tenant_database.database_name` | Base canónica. Es la única que debe estar operativa. |
| `restore_<32 hex>` | Temporal de validación | Candidata a promoción; si el restore falló puede contener la copia nueva validada. |
| `restore_backup_<32 hex>` | Respaldo de la canónica anterior | Copia de seguridad del estado previo al cutover. |

No confunda `restore_probe_*` (ensayos del rehearsal, fuera de este flujo) con
`restore_*` del restore real.

### 2.1 Inventario de solo lectura

Ejecute estas consultas contra el **Control Plane / base administrativa**, nunca
contra una base tenant, con un rol de solo lectura cuando sea posible:

```sql
-- Bases físicas candidatas
-- (pg_database_size exige privilegio CONNECT sobre cada base; si el rol de solo
--  lectura no lo tiene, ejecute la misma consulta sin la columna size_bytes)
SELECT datname, pg_database_size(datname) AS size_bytes, datdba::regrole AS owner,
       datallowconn, datconnlimit
FROM pg_database
WHERE datname LIKE 'tenant\_%' ESCAPE '\'
   OR datname LIKE 'restore\_%' ESCAPE '\'
   OR datname LIKE 'restore\_backup\_%' ESCAPE '\'
ORDER BY datname;

-- Últimos restores y nombres retenidos (R-2)
SELECT id, empresa_id, tenant_database_id, backup_id, estado, etapa,
       fecha_inicio, fecha_fin, mensaje_error
FROM saas_control.restore_tenant
ORDER BY id DESC
LIMIT 50;

-- Auditoría correlacionada
SELECT id, fecha_hora, accion, id_registro_afectado, resultado, descripcion
FROM saas_control.saas_bitacora
WHERE entidad_afectada = 'restore_tenant'
  AND accion IN ('ERROR_RESTORE', 'RETENER_RESPALDO_RESTORE',
                 'COMPLETAR_RESTORE', 'COMPLETAR_ROLLBACK_RESTORE',
                 'ERROR_ROLLBACK_RESTORE')
ORDER BY id DESC
LIMIT 100;

-- Sesiones conectadas a una base concreta (solo lectura)
SELECT pid, usename, application_name, client_addr, state, query_start
FROM pg_stat_activity
WHERE datname = '<nombre_base>'
ORDER BY query_start;
```

Los campos `mensaje_error` y `saas_bitacora.descripcion` guardan, desde la remediación
R-2, una nota con este formato:

```
restore_id=<id>; tenant_database_id=<id>; base_canonica=<tenant_x>;
base_temporal=<restore_uuid>; base_respaldo=<restore_backup_uuid>;
etapa=<ETAPA>; motivo=<motivo_operativo>
```

En el caso `COMPLETADO_RESPALDO_RETENIDO`, el campo puede aparecer como
`base_temporal_promovida=<restore_uuid>`: esa temporal ya fue renombrada al nombre
canónico y **no** debe tratarse como una base existente. `base_respaldo` sí sigue en
disco. En los fallos donde el respaldo ya volvió al nombre canónico, la nota omite
`base_respaldo` para no señalar una base inexistente.

Esa nota no contiene credenciales ni cadenas de conexión. Los nombres físicos solo se
muestran a **SUPERADMIN** en los endpoints SaaS administrativos (`/saas/restores`,
`/saas/bitacora`, `/saas/tenants`, `/saas/empresas`). OPERADOR conserva la lectura SaaS
general, pero esos campos llegan redactados o nulos; los JWT tenant y los usuarios
clínicos nunca reciben nombres físicos ni metadatos operativos de bases. Para obtener
los nombres completos, use el acceso SUPERADMIN o esta guía con las consultas SQL de
solo lectura.

### 2.2 Esquema real del ledger de migraciones

El ledger no es una tabla `app_migrations`: `app_migrations` es el **esquema** y la
tabla es `app_migrations.schema_migration`
(`database/tenant_migrations/bootstrap_ledger.sql`), con estas columnas:

| Columna | Tipo | Uso |
|---|---|---|
| `version` | `varchar(20)` PK | Versión aplicada (p. ej. `20261031000000`). |
| `nombre` | `varchar(150)` | Nombre de la migración. |
| `checksum_sha256` | `char(64)` | Checksum verificado por el runner. |
| `aplicada_en` | `timestamptz` | Fecha/hora de aplicación. |
| `duracion_ms` | `bigint` | Duración de la aplicación. |
| `runner_version` | `varchar(50)` | Versión del runner que la aplicó. |

Consulte el ledger **solo** con `SELECT`; nunca lo modifique a mano para "cuadrar" una
versión. Las consultas de las secciones 2.1 y 5 son de solo lectura y se ejecutan contra
la base indicada en cada caso (Control Plane para metadatos, base retenida para el
ledger físico).

---

## 3. Reglas para NO ejecutar `DROP DATABASE`

**Nunca** ejecute `DROP DATABASE` cuando se cumpla cualquiera de estas condiciones:

1. La base canónica `tenant_*` no existe o no puede identificarse con certeza.
2. `restore_tenant.estado` es `PENDIENTE`, `EN_PROCESO` o `ROLLBACK_EN_PROCESO`.
3. El tenant está `RESTAURANDO` o `ERROR` y no se demostró qué base es la válida.
4. El restore terminó en `ERROR` con etapa `RECUPERACION_RENOMBRE`,
   `RECUPERACION_DESTINO` o `RECUPERACION_METADATOS`.
5. La etapa es `RENOMBRE_ACTIVA`, `RENOMBRE_TEMPORAL`, `RESERVA_RESPALDO` o
   `VALIDACION_TEMPORAL` y no se inspeccionó el contenido de la temporal.
6. No hay una nota R-2 que correlacione el nombre con `restore_id`, `tenant_id` y etapa.
7. La base es la única copia disponible de datos del tenant.
8. No existe autorización doble y evidencia de la sección 9.
9. La sesión administrativa está conectada a la base que se pretende eliminar.
10. Se pretende ensayar el procedimiento sobre una de las siete bases productivas.

Tampoco use `DROP DATABASE ... WITH (FORCE)` como sustituto de `terminate_sessions` en
un procedimiento de restore. `WITH (FORCE)` es una herramienta de recuperación
excepcional, no un paso rutinario.

---

## 4. Árbol de decisión ante un restore fallido o interrumpido

Primero determine, con catálogo y notas R-2, qué bases existen:

```
¿Existe tenant_* (canónica)?
├─ Sí
│  ├─ ¿restore_tenant.estado = COMPLETADO?
│  │  ├─ Sí → tenant ACTIVA. Si etapa = COMPLETADO_RESPALDO_RETENIDO,
│  │  │        conservar restore_backup_* hasta autorización de retiro.
│  │  └─ No → ¿etapa de fallo?
│  │        ├─ VALIDACION_DESTINO / METADATOS → original recuperada; conservar la
│  │        │  base nueva bajo restore_* y el respaldo; validar antes de decidir.
│  │        └─ RENOMBRE_TEMPORAL → canónica original recuperada; temporal retenida.
│  └─ ¿tenant.estado = ACTIVA?
│     ├─ Sí → servicio operativo; no tocar bases retenidas sin validación.
│     └─ No → tratar como incidente; no eliminar nada.
└─ No
   ├─ ¿Existe restore_backup_<uuid>?
   │  ├─ Sí → la copia anterior está en el respaldo. ¿Existe restore_<uuid>?
   │  │        ├─ Sí → hay dos candidatas; validar ambas (sección 6) antes de elegir.
   │  │        └─ No → el respaldo es la única copia conocida; NO borrar.
   │  └─ No → ¿existe restore_<uuid>?
   │           ├─ Sí → es la única candidata; validarla; NO borrar.
   │           └─ No → pérdida física evidente; escalar de inmediato, no improvisar.
```

Si no puede demostrar cuál base es válida, deje el tenant en `ERROR`, bloquee el
acceso y escale. Es preferible un tenant detenido con dos bases íntegras que un tenant
activo apuntando a la base equivocada.

---

## 5. Inspección de una base retenida

Conéctese **solo** a la base retenida, nunca a la canónica si está en duda, y ejecute
consultas de lectura:

```sql
SELECT current_database(), version();

-- Inventario de esquemas/tablas
SELECT table_schema, count(*)
FROM information_schema.tables
WHERE table_type = 'BASE TABLE'
GROUP BY table_schema
ORDER BY table_schema;

-- Ledger de migraciones (si existe)
SELECT * FROM app_migrations.schema_migration ORDER BY version;

-- Conteos mínimos de sanidad (ajustar a tablas reales del tenant)
SELECT 'usuario' AS tabla, count(*) FROM public.usuario
UNION ALL SELECT 'paciente', count(*) FROM public.paciente
UNION ALL SELECT 'cita', count(*) FROM public.cita;
```

Compruebe además el endpoint de salud del tenant cuando el servicio esté disponible.
No use estas consultas para escribir, migrar ni corregir datos.

---

## 6. Recuperación manual supervisada

Solo con autorización de la sección 9 y preferentemente en una ventana de
mantenimiento. Los renombrados exigen que **nadie** esté conectado a la base que se
renombra; la sesión administrativa debe estar conectada a otra base (por ejemplo
`postgres`).

### 6.1 Terminar sesiones (paso previo, reversible)

```sql
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '<nombre_base>'
  AND pid <> pg_backend_pid();
```

Repita la consulta de `pg_stat_activity` hasta confirmar cero sesiones. No termine
sesiones de replicación ni del pooler sin identificar su origen.

### 6.2 Devolver el respaldo al nombre canónico

Caso: la canónica no existe, existe `restore_backup_<uuid>` y se decidió recuperar el
estado anterior.

```sql
ALTER DATABASE restore_backup_<uuid> RENAME TO tenant_<empresa>;
```

Después valide estructura, ledger, health y metadatos. Si el rename falla por sesión
activa, vuelva a 6.1; no borre nada.

### 6.3 Promover una temporal validada

Caso: la canónica no existe y `restore_<uuid>` es la copia nueva validada.

```sql
ALTER DATABASE restore_<uuid> RENAME TO tenant_<empresa>;
```

Solo hágalo si la validación de la sección 5 fue satisfactoria y existe evidencia de
que esa temporal corresponde al `restore_id` y `tenant_database_id` correctos.

### 6.4 Respaldo retenido tras un restore completado

Si `estado=COMPLETADO` y `etapa=COMPLETADO_RESPALDO_RETENIDO`, el destino ya está
activo. **No** promueva ni renombre el respaldo. Conserve `restore_backup_<uuid>`
hasta completar la sección 9.

### 6.5 Colisión de nombre

Si el nombre destino ya existe, `ALTER DATABASE ... RENAME` fallará. No fuerce ni
elimine el destino para “hacer espacio”. Determine si el destino es la canónica
válida; si lo es, el restore ya está aplicado. Si no lo es, escale con el inventario
completo.

---

## 7. Timeout, worker interrumpido y restores `EN_PROCESO`

Un timeout del cliente o un `SIGKILL` del worker **no** significa que PostgreSQL no
haya ejecutado el rename. Después de cualquier excepción de rename:

1. Consulte `pg_database` y `saas_control.restore_tenant`.
2. Reconcilie el nombre canónico, `restore_*` y `restore_backup_*` antes de decidir
   si la original está disponible.
3. No asuma que `tenant.estado` refleja la realidad física.
4. No ejecute un nuevo restore si el anterior quedó `EN_PROCESO` o `PENDIENTE`: el
   índice único parcial lo bloqueará. Primero cierre el incidente.

> Desde la corrección M-1, un fallo en la preparación (verificación de versión, sonda
> de capacidades, PRE_RESTORE o preflight) cierra la reserva en `ERROR` con etapa
> `VALIDACION*`/`PREFLIGHT`/`PREPARACION` y deja el tenant `ACTIVA`; el sistema ya no
> deja filas `PENDIENTE` huérfanas. Una fila `PENDIENTE` antigua solo puede provenir de
> un `SIGKILL` entre la reserva y la primera transición: trátela como incidente manual
> con el mismo procedimiento que un `EN_PROCESO` y no la borre sin evidencia.

Si el worker murió y el restore quedó `EN_PROCESO` con tenant `RESTAURANDO`:

- **No** elimine la fila de `restore_tenant` ni cambie estados a mano sin evidencia.
- Inventaríe bases y sesiones; determine si el cutover ocurrió.
- Si la canónica existe y valida, el servicio puede reactivarse solo con una
  corrección de metadatos autorizada y auditada.
- Si la canónica no existe, aplique 6.2/6.3.
- Registre la decisión en la bitácora y en el ticket.

La reconciliación automática de restores huérfanos es deuda pendiente (R-4); hasta
que exista un diseño aprobado, la recuperación es manual y supervisada.

---

## 8. Evidencia mínima de integridad

Antes de declarar un restore recuperado, conserve:

1. Salida de `pg_database` con las bases implicadas (nombre, tamaño, owner).
2. Fila de `restore_tenant` (`estado`, `etapa`, `mensaje_error`, `pre_restore_backup_id`).
3. Fila de `tenant_database` (`database_name`, `estado`, `version_schema`).
4. Entradas de `saas_bitacora` del restore y de la intervención manual.
5. Ledger `app_migrations.schema_migration` de la base promovida y versión declarada.
6. Conteos de tablas críticas y resultado de health check.
7. SHA-256 y tamaño del archivo usado, si aplica.
8. Comandos ejecutados, operador, fecha/hora y autorizaciones.
9. Confirmación de que no quedaron sesiones residuales sobre bases retenidas.

Los logs y capturas deben redactar credenciales, cadenas de conexión y datos clínicos.

---

## 9. Autorización y auditoría para retirar respaldos

Retirar una base `restore_*` o `restore_backup_*` es una operación destructiva:

1. **Quién autoriza:** SUPERADMIN SaaS **y** el propietario/DBA responsable de la
   empresa afectada. Una sola persona no puede autorizar y ejecutar.
2. **Requisitos previos:** restore `COMPLETADO`, tenant `ACTIVA`, destino validado,
   evidencia de la sección 8 y ventana de mantenimiento.
3. **Doble verificación:** dos operadores confirman por separado el nombre exacto y
   el `restore_id` correlacionado en la nota R-2.
4. **Registro:** dejar constancia en el ticket y en `saas_bitacora` de la decisión,
   del operador y de la base retirada. Si la aplicación no tiene una acción específica,
   registre la intervención administrativa con el mecanismo de auditoría aprobado.
5. **Comando:** ejecutar el `DROP DATABASE` solo sobre el nombre exacto verificado,
   después de terminar sesiones y con respaldo externo comprobado.
6. **Prohibido:** usar `*`, concatenar nombres desde variables no verificadas, borrar
   varias bases en un mismo comando o automatizar el retiro sin aprobación.

Si falta cualquier requisito, **no borre**. Deje la base retenida; el coste de
almacenamiento es menor que el riesgo de pérdida de datos.

---

## 10. Checklist de cierre de incidente

- [ ] Base canónica identificada y validada.
- [ ] Tenant en estado coherente con la realidad física.
- [ ] Restore cerrado (`COMPLETADO` o `ERROR` con causa documentada).
- [ ] Bases retenidas inventariadas y correlacionadas.
- [ ] Sesiones residuales verificadas.
- [ ] Evidencia de integridad archivada.
- [ ] Autorización doble registrada si se retiró algún respaldo.
- [ ] Bitácora y ticket actualizados.
- [ ] Lección aprendida y deuda técnica registrada (R-3, R-4, R-5, R-6 si aplica).
