# CONTEXTO_SAAS_7F_PROVISIONAR_5_TENANTS_RESTANTES.md

Repositorio:
`C:\SI2_Proyecto\clinica-oftalmologica-api`

Estado confirmado:

```text
VISION-CLARA
→ tenant_vision_clara
→ ACTIVA / COMPLETADO
→ login real 200

OFTALMO-NORTE
→ tenant_oftalmo_norte
→ ACTIVA / COMPLETADO
→ login real 200
→ login cruzado con VISION-CLARA rechazado 401 en ambos sentidos
```

Con esto el aislamiento A/B ya está demostrado.

Quedan 5 empresas demo PENDIENTES:

```text
VISUAL-ORIENTAL
→ tenant_visual_oriental

INSTITUTO-VISION
→ tenant_instituto_vision

OFTALMOCARE
→ tenant_oftalmocare

VISTA-SUR
→ tenant_vista_sur

MEDICO-OCULAR
→ tenant_medico_ocular
```

Objetivo de 7F:
Provisionar los 5 tenants restantes con el flujo CLEAN ya validado, de forma SECUENCIAL y fail-closed.

NO provisionar en paralelo.

Cada tenant debe quedar con:

```text
schema v1
5 catálogos base
1 administrador propio
0 datos clínicos/transaccionales
ACTIVA / COMPLETADO
```

No modificar VISION-CLARA ni OFTALMO-NORTE.

---

## Estrategia recomendada

Crear un modo batch seguro que internamente reutilice exactamente el flujo `--provision-clean`.

Debe procesar uno por uno:

1. VISUAL-ORIENTAL
2. INSTITUTO-VISION
3. OFTALMOCARE
4. VISTA-SUR
5. MEDICO-OCULAR

Si uno falla:

```text
STOP
```

No continuar con los siguientes.

Los ya completados antes del fallo permanecen ACTIVA/COMPLETADO.

---

## Bootstrap admin

Correos demo sugeridos:

```text
VISUAL-ORIENTAL   → admin@visualoriental.demo
INSTITUTO-VISION  → admin@institutovision.demo
OFTALMOCARE       → admin@oftalmocare.demo
VISTA-SUR         → admin@vistasur.demo
MEDICO-OCULAR     → admin@medicoocular.demo
```

Las contraseñas se piden localmente con `getpass` para cada tenant.

NO aceptar passwords por CLI.
NO imprimir passwords.
NO reutilizar automáticamente la contraseña de otro tenant.

---

## Preflight global

Antes de mutar nada, comprobar para los 5:

- empresa existe
- tenant_database PENDIENTE
- provisionamiento PENDIENTE
- DB física no existe
- database_name válido
- suscripción ACTIVA
- herramientas PostgreSQL disponibles
- schema fuente v1 válido

Si cualquier tenant falla preflight global:
STOP antes de crear el primero.

---

## Provisionamiento por tenant

Para cada empresa:

```text
[1] revalidar Control Plane
[2] pedir password bootstrap
[3] PROVISIONANDO
[4] crear database
[5] schema-only dump/restore
[6] seed catálogos
[7] bootstrap admin
[8] verify clean
[9] SELECT 1
[10] ACTIVA / COMPLETADO
```

Usar PostgreSQL 18:

`C:\Program Files\PostgreSQL\18\bin`

---

## Verificación por tenant

Esperado:

```text
accion = 3
modulo = 6
funcion = 19
rol = 4
rol_funcion = 39
usuario = 1

paciente = 0
cita = 0
historial_clinico = 0
consulta_clinica = 0
diagnostico = 0
receta = 0
bitacora = 0
token_recuperacion = 0
```

Además:

- 27 tablas
- 27 secuencias
- 182 columnas
- constraints equivalentes
- 67 índices
- schemas prohibidos = 0

Usar conteos reales de catálogos si la fuente cambió; los transaccionales deben seguir en 0.

---

## Aislamiento global

Al terminar deben existir 7 databases distintas:

```text
tenant_vision_clara
tenant_oftalmo_norte
tenant_visual_oriental
tenant_instituto_vision
tenant_oftalmocare
tenant_vista_sur
tenant_medico_ocular
```

TenantResolver debe resolver una DB distinta por empresa.

TenantEngineRegistry debe mantener engines separados.

---

## Login

No es necesario probar manualmente los 5 logins durante el batch si:
- bootstrap admin fue validado;
- hash existe;
- auth tenant ya fue probado real en VISION-CLARA y OFTALMO-NORTE.

Después del batch se hará una prueba manual de muestra con uno de los nuevos tenants.

---

## No hacer

NO:

- tocar VISION-CLARA
- tocar OFTALMO-NORTE
- provisionar en paralelo
- copiar datos clínicos
- copiar usuarios entre tenants
- migrar routers clínicos
- frontend/móvil
- backup/restore final
- realtime
- .env
- commit/push/merge
