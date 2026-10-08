# CONTEXTO_SAAS_7E_SEGUNDO_TENANT_AISLAMIENTO.md

## 1. Estado previo

Repositorio:
`C:\SI2_Proyecto\clinica-oftalmologica-api`

Completado:

```text
7A ✅ Control Plane SaaS
7B ✅ Tenant Connection Router
7C ✅ Auth/JWT tenant-aware
7D ✅ Primer tenant físico + login real
```

Primer tenant:

```text
empresa_codigo = VISION-CLARA
database_name = tenant_vision_clara
estado = ACTIVA
provisionamiento = COMPLETADO
login tenant = HTTP 200
```

Segundo tenant del Control Plane:

```text
empresa_codigo = OFTALMO-NORTE
database_name = tenant_oftalmo_norte
estado esperado actual = PENDIENTE
```

---

# 2. Objetivo 7E

Crear el segundo tenant físico:

```text
tenant_oftalmo_norte
```

con:

```text
MISMO ESQUEMA
DATOS CLÍNICOS VACÍOS
CATÁLOGOS DE SEGURIDAD BASE
ADMIN CLÍNICO PROPIO
```

y demostrar aislamiento real entre:

```text
VISION-CLARA
vs
OFTALMO-NORTE
```

No copiar pacientes, citas, historiales, consultas, diagnósticos, recetas, bitácora ni usuarios de VISION-CLARA.

---

# 3. Principio de provisión limpia

El primer tenant fue migración de un sistema single-tenant existente.

El segundo y los siguientes son tenants NUEVOS.

Por tanto, para OFTALMO-NORTE NO usar dump con datos completos.

Usar:

```text
schema-only de public
+
seed controlado de catálogos base
+
bootstrap de un administrador propio
```

Fuente canónica del schema:

```text
postgres.public
```

No clonar desde tenant_vision_clara.

---

# 4. Datos que SÍ pueden seedearse

Clasificar y copiar únicamente catálogos necesarios para seguridad y navegación.

Esperados:

```text
accion
modulo
funcion
rol
rol_funcion
```

Confirmar dependencias antes de seedear.

No copiar automáticamente tablas clínicas.

Si otra tabla resulta requisito técnico indispensable, justificarla explícitamente.

---

# 5. Datos que deben comenzar vacíos

Como mínimo:

```text
antecedente_clinico
bitacora
bloqueo_horario
cita
consulta_clinica
control_medico
detalle_receta
diagnostico
examen_oftalmologico
historial_clinico
horario_oftalmologo
indicacion
oftalmologo
paciente
receta
resultado_examen
servicio_realizado
token_recuperacion
tratamiento
```

`usuario` debe contener únicamente el usuario bootstrap propio de OFTALMO-NORTE.

No copiar los 14 usuarios de VISION-CLARA.

---

# 6. Bootstrap admin

Crear un administrador clínico del tenant mediante flujo seguro.

No aceptar password en argumentos CLI.

Preferir:

```text
--bootstrap-admin-email correo@...
```

y solicitar contraseña localmente mediante:

```python
getpass.getpass(...)
```

Hash con el mismo algoritmo real del proyecto.

No imprimir password.
No guardar password plano.
No guardar password en Control Plane.
No reutilizar automáticamente una contraseña de VISION-CLARA.

El usuario debe asociarse a un rol Administrador existente en el catálogo seed.

---

# 7. Provisioner

Extender `scripts/saas/provision_tenant.py` o crear un módulo complementario coherente.

Modo sugerido:

```text
--provision-clean
```

Debe requerir:

```text
--empresa OFTALMO-NORTE
--confirm tenant_oftalmo_norte
--bootstrap-admin-email ...
```

Debe:

1. preflight Control Plane;
2. exigir PENDIENTE;
3. exigir DB inexistente;
4. schema-only dump/restore;
5. seed catálogos base;
6. bootstrap admin;
7. verificar estructura;
8. verificar tablas clínicas vacías;
9. health check;
10. activar Control Plane.

No usar data dump completo.

---

# 8. Schema-only

Usar `pg_dump`:

```text
--schema=public
--schema-only
--no-owner
--no-privileges
```

Restore con `pg_restore`.

No copiar:

```text
saas_control
auth
storage
realtime
vault
graphql
graphql_public
supabase_functions
```

---

# 9. Seed base

Preferir seed explícito/reproducible.

Puede generarse desde catálogos fuente o mediante SQL controlado.

Debe preservar claves/relaciones necesarias para:

```text
rol
funcion
accion
rol_funcion
modulo
```

No depender de IDs asumidos sin verificar.

Verificar counts esperados.

---

# 10. Activación

Solo marcar:

```text
tenant_database = ACTIVA
provisionamiento = COMPLETADO
```

si:

- schema completo;
- catálogos base válidos;
- admin bootstrap creado;
- tablas clínicas vacías;
- schemas prohibidos ausentes;
- SELECT 1 pasa;
- login tenant del admin puede funcionar.

---

# 11. Aislamiento A/B

Demostrar:

### A. Bases físicas distintas

```text
VISION-CLARA → tenant_vision_clara
OFTALMO-NORTE → tenant_oftalmo_norte
```

### B. Datos distintos

VISION-CLARA mantiene, como referencia:

```text
usuario = 14
paciente = 10
cita = 8
```

OFTALMO-NORTE debe comenzar:

```text
usuario = 1 (bootstrap)
paciente = 0
cita = 0
consulta_clinica = 0
diagnostico = 0
```

### C. Login aislado

Una cuenta exclusiva de OFTALMO-NORTE:

```text
empresa_codigo = OFTALMO-NORTE
→ login 200
```

La misma cuenta contra:

```text
empresa_codigo = VISION-CLARA
→ Credenciales inválidas
```

Una cuenta exclusiva de VISION-CLARA contra:

```text
empresa_codigo = OFTALMO-NORTE
→ Credenciales inválidas
```

No compartir passwords ni tokens.

---

# 12. No migrar routers clínicos todavía

En 7E demostramos aislamiento de:

```text
Control Plane
DB física
sesión
login
datos
```

Los routers clínicos siguen legacy hasta el siguiente bloque de cutover/migración.

---

# 13. Tests

Agregar cobertura para:

- provision-clean exige PENDIENTE;
- DB existente bloquea;
- schema-only;
- no data dump;
- catálogos permitidos;
- tablas clínicas vacías;
- solo un usuario bootstrap;
- password por getpass, no CLI;
- hash válido;
- no secrets logs;
- activación solo tras checks;
- aislamiento A/B;
- wrong-company login falla;
- resolver A/B retorna database_name distinto;
- registry cache separado por tenant.

Ejecutar suite completa.

---

# 14. No hacer

NO:

- copiar datos clínicos de VISION-CLARA;
- copiar sus usuarios;
- modificar tenant_vision_clara;
- crear los otros 5 tenants todavía;
- migrar routers clínicos;
- cutover frontend;
- backup/restore final;
- realtime;
- .env;
- commit/push/merge.

---

# 15. Criterio de cierre

7E queda cerrado cuando:

```text
[ ] tenant_oftalmo_norte existe
[ ] ACTIVA / COMPLETADO
[ ] mismo schema v1
[ ] catálogos base presentes
[ ] 1 admin propio
[ ] pacientes/citas/consultas/diagnósticos = 0
[ ] login OFTALMO-NORTE = 200
[ ] login cruzado A↔B = rechazado
[ ] resolver devuelve DB distintas
[ ] engine registry usa pools separados
[ ] suite verde
```
