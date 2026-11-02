# CU32/CU33 — planes de pago y cuotas

Implementación backend para FastAPI y bases PostgreSQL tenant. La migración fue
ejecutada el 2026-10-31 sobre las siete bases de clínicas alojadas en Supabase,
que quedaron en `v4-sprint3-cu32-cu33`. El código de este repositorio todavía no
ha sido desplegado.

## Estado de despliegue

Los siete tenants están migrados y verificados:

| Tenant | Versión previa | Versión actual |
|---|---|---|
| `tenant_vision_clara` | `v4-sprint3-cu32-cu33` (piloto) | `v4-sprint3-cu32-cu33` |
| `tenant_instituto_vision` | `v3-sprint3-cu26-cu27-cu29` | `v4-sprint3-cu32-cu33` |
| `tenant_medico_ocular` | `v3-sprint3-cu26-cu27-cu29` | `v4-sprint3-cu32-cu33` |
| `tenant_oftalmo_norte` | `v3-sprint3-cu26-cu27-cu29` | `v4-sprint3-cu32-cu33` |
| `tenant_oftalmocare` | `v3-sprint3-cu26-cu27-cu29` | `v4-sprint3-cu32-cu33` |
| `tenant_vista_sur` | `v3-sprint3-cu26-cu27-cu29` | `v4-sprint3-cu32-cu33` |
| `tenant_visual_oriental` | `v3-sprint3-cu26-cu27-cu29` | `v4-sprint3-cu32-cu33` |

Cada migración se ejecutó de forma secuencial con `--plan`, confirmación del
hash del plan, `--execute --confirm-plan <hash>` y verificación posterior. Antes
de aplicar se generó un respaldo `pg_dump` custom con `public` y
`app_migrations` en el mismo snapshot MVCC, con SHA-256 y manifiesto; esos
respaldos se guardan fuera del repositorio, en la carpeta hermana
`private-backups/`. El ensayo de restauración de cada respaldo se ejecutó en un
PostgreSQL 17 descartable.

Resultado medido en los siete tenants después de migrar:

- Cuatro tablas nuevas (`plan_pago`, `plan_pago_servicio`, `cuota`,
  `intento_pago_cuota`) creadas y vacías.
- Ledger `app_migrations.schema_migration` con `20261031000000` y checksum
  `625fd5caef7f39bea228e7019145826839891ebfec00c8c07b91c6742e01e312`, igual al
  SHA-256 del artefacto en el manifiesto.
- RLS habilitado en las cuatro tablas, sin `FORCE ROW LEVEL SECURITY` y sin
  privilegios para `anon` ni `authenticated`.
- RBAC: dos funciones (`Gestionar planes de pago`, `Gestionar cuotas`) y cuatro
  asignaciones en `rol_funcion` para administrador y recepción.
- Conteos finales 42 tablas, 41 secuencias, 303 columnas y 123 índices en los
  siete tenants existentes.
- Las 38 tablas previas conservan su cantidad de filas; la única diferencia son
  las dos funciones y las cuatro asignaciones que inserta la propia migración.

## Decisiones financieras

- Un plan reserva servicios de un paciente y una consulta mediante
  `plan_pago_servicio.activo`.
- La reserva permanece activa al completar el plan; solamente una cancelación
  sin cobros ni intentos potencialmente cobrables la libera.
- `monto_total`, importes financiados y cuotas se calculan y comparan con
  `Decimal` a dos decimales. El cliente no envía el total.
- Una cuota se paga completa. No existen abonos parciales, intereses ni mora.
- `VENCIDA` se calcula con `APP_TIMEZONE`; no se almacena en PostgreSQL.
- Cada pago de cuota reutiliza `pago`, `pago_detalle`, comprobante PDF y outbox
  Resend de CU31. La asignación entre servicios es determinista por
  `servicio_realizado_id` ascendente.
- Un servicio se considera pagado únicamente cuando la suma aprobada alcanza su
  precio. La reserva impide cobrarlo por el flujo normal CU31 mientras el plan
  siga comprometido.

## Rutas

Todas conservan el prefijo `/pagos`.

| Método | Ruta | Actor / permiso |
|---|---|---|
| GET | `/planes/servicios-disponibles` | Admin/Recepción, lectura de `Gestionar planes de pago` |
| POST | `/planes` | Admin/Recepción, escritura, `Idempotency-Key` |
| GET | `/planes` | Admin/Recepción, lectura |
| GET | `/planes/{plan_id}` | Admin/Recepción, lectura |
| POST | `/planes/{plan_id}/cancelar` | Admin/Recepción, escritura |
| GET | `/mis-planes` | Paciente titular |
| GET | `/mis-planes/{plan_id}` | Paciente titular |
| POST | `/cuotas/{cuota_id}/stripe/intencion` | Paciente titular |
| POST | `/cuotas/{cuota_id}/pagos` | Admin/Recepción, escritura de `Gestionar cuotas` |
| POST | `/cuotas/{cuota_id}/pagos/{pago_id}/confirmar` | Admin/Recepción, escritura |
| POST | `/cuotas/{cuota_id}/pagos/{pago_id}/rechazar` | Admin/Recepción, escritura |
| GET | `/cuotas/{cuota_id}/intentos` | Paciente titular o personal con lectura |

El webhook continúa siendo exclusivamente `POST /pagos/stripe/webhook`. Una
intención de cuota incluye `pago_id`, `consulta_clinica_id`, `plan_pago_id` y
`cuota_id` en metadata firmada, además de los marcadores tenant existentes.

### Crear plan

```json
{
  "paciente_id": 8,
  "consulta_clinica_id": 21,
  "servicio_realizado_ids": [41, 42],
  "cuotas": [
    {"numero_cuota": 1, "monto_programado": "100.00", "fecha_vencimiento": "2026-11-30"},
    {"numero_cuota": 2, "monto_programado": "100.00", "fecha_vencimiento": "2026-12-31"}
  ],
  "observaciones": "Acuerdo autorizado"
}
```

### Cobro administrativo de cuota

```json
{
  "metodo_pago": "TRANSFERENCIA",
  "referencia_bancaria": "REF-BANCO-001",
  "observaciones": "Pendiente de verificación"
}
```

La transferencia o QR permanece `PENDIENTE` hasta confirmar. Si no se recibió
el dinero, la ruta `/rechazar` exige un motivo y conserva el historial.

## Migración y versión

- Artefacto: `20261031000000_cu32_cu33_planes_cuotas.sql`.
- Versión destino: `v4-sprint3-cu32-cu33`.
- Fuente permitida: `v3-sprint3-cu26-cu27-cu29`. Un tenant en v2 se bloquea
  para impedir que omita la migracion externa CU26/CU27/CU29.
- Los tres artefactos históricos CU31 y sus SHA-256 permanecen intactos.
- El migrador rechaza versiones desconocidas para evitar un downgrade.
- RLS queda habilitado sin políticas Data API y se revocan privilegios de
  `PUBLIC`, `anon` y `authenticated`; FastAPI usa conexión PostgreSQL directa.

El ensayo autorizado en PostgreSQL descartable se completó por tenant antes de
cada aplicación: `--plan`, confirmación del hash, aplicación y verificación de
locks, índices parciales, RBAC y concurrencia. Los siete tenants se migraron uno
por uno con respaldo verificado. No debe actualizarse `version_schema` fuera del
migrador.

## Respaldo y recuperacion coherente

- Los backups nuevos incluyen `public` y `app_migrations` dentro del mismo
  archive custom de `pg_dump`; ambos esquemas pertenecen al mismo snapshot
  MVCC.
- `restore_exact()` elimina solamente los esquemas presentes en el archive y
  no crea `public` antes de `pg_restore`, porque el archive contiene su propio
  `CREATE SCHEMA`.
- Un backup antiguo que no contiene `app_migrations` conserva el ledger
  existente. No debe utilizarse como rollback de version sin una verificacion
  manual del ledger.
- Al completar un restore nuevo, el Control Plane asigna
  `tenant_database.version_schema` desde `backup_tenant.version_schema`, pero
  solo despues de validar estructura y salud. Si el restore falla y el
  PRE_RESTORE se recupera, se restablece tambien su version.
- El archive, su SHA-256, `backup_tenant.version_schema` y el tenant vinculado
  forman una sola unidad de recuperacion. Nunca se debe actualizar
  `version_schema` de forma independiente del restore verificado.

## Conteos canonicos del aprovisionador

El esquema fisico v3 de Vision Clara contiene 38 tablas, 38 secuencias de
`pg_class`, 277 columnas y 108 indices. Incluye la tabla historica
`servicios_oftalmologicos`, que se preserva en tenants existentes pero no forma
parte del esquema canonico para una clinica nueva. La fuente canonica v3 tiene
37/37/271/106. CU32/CU33 agrega cuatro tablas, tres secuencias identity, 26
columnas y 15 indices.

Por tanto existen dos resultados v4 correctos: un tenant existente termina en 42
tablas, 41 secuencias, 303 columnas y 123 indices; una clinica nueva canonica
debe tener 41/40/297/121 porque no recibe la tabla historica. Los siete tenants
existentes se midieron individualmente despues de migrar y todos coinciden en
42/41/303/123, con 160 constraints y 13 tablas con RLS.

Los valores anteriores 33/32/233/90 omitian ademas las ocho tablas vigentes de
CU26/CU27/CU29. Ese era el problema real. El aprovisionador ahora exige el v4
canonico 41/40/297/121 y falla cerrado si la fuente no lo contiene; no crea ni
elimina tablas para forzar los conteos.

## Límite explícito

CU32/CU33 no implementa reembolsos ni disputas. Si un pago aplicado entra en
`REEMBOLSADO` o es disputado, requiere una política posterior de conciliación;
no se libera automáticamente la reserva ni se presenta el plan como corregido.
