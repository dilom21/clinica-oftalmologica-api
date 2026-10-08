# CONTEXTO_SAAS_7C_AUTH_TENANT.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Bloque:** 7C — Auth/JWT Tenant-Aware (sin cutover aún)  
**Repositorio objetivo:** `C:\SI2_Proyecto\clinica-oftalmologica-api`

Estado previo:

```text
7A ✅ Control Plane SaaS real
7B ✅ Tenant Connection Router
```

Actualmente:

```text
7 empresas
7 tenant_database PENDIENTE
0 bases físicas tenant_*
```

Por esa razón, este bloque debe preparar autenticación tenant-aware SIN romper el login single-tenant existente y SIN obligar todavía al frontend a enviar empresa.

El cutover real del login se hará después de crear/provisionar el primer tenant físico.

---

# 2. Objetivo

Agregar soporte seguro para:

```text
tenant_id
empresa_id
empresa_codigo
token_type
```

en JWT tenant-aware, y preparar el flujo de autenticación por empresa.

Pero:

```text
/auth/login actual debe seguir funcionando igual
```

hasta que exista al menos un tenant físico ACTIVO.

No cambiar todavía el comportamiento productivo del frontend.

---

# 3. Arquitectura objetivo

Futuro flujo final:

```text
empresa_codigo + correo + password
            ↓
TenantResolver
            ↓
empresa ACTIVA
suscripción ACTIVA
tenant DB ACTIVA
            ↓
TenantEngineRegistry
            ↓
usuario en DB tenant
            ↓
JWT tenant-aware
            ↓
requests clínicos resuelven tenant
```

En 7C se implementa:

```text
schemas + claims + helpers + servicio auth tenant + tests
```

sin hacer todavía el cutover global.

---

# 4. Reutilizar

Inspeccionar y reutilizar:

```text
app/modules/gestion_usuarios_seguridad/
app/core/security.py
app/core/dependencies.py
app/core/tenancy/
```

No crear un segundo sistema JWT paralelo si el actual se puede extender de forma compatible.

---

# 5. Claims JWT tenant-aware

Crear soporte para tokens con:

```json
{
  "sub": "usuario_id",
  "rol_id": 1,
  "tenant_id": 4,
  "empresa_id": 4,
  "empresa_codigo": "VISION-CLARA",
  "token_type": "tenant",
  "exp": "..."
}
```

No incluir:

```text
database_name
DATABASE_URL
host
port
password
plan price
connection string
```

`tenant_id` debe representar de forma estable el `tenant_database.id` o el identificador técnico elegido por el diseño.

Documentar claramente cuál se usa.

---

# 6. Compatibilidad con tokens legacy

Los tokens actuales probablemente no tienen:

```text
tenant_id
empresa_id
empresa_codigo
token_type
```

NO romperlos en este bloque.

Debe existir una distinción explícita:

```text
legacy token
tenant token
```

Por ejemplo:

```text
token_type = "tenant"
```

solo para nuevos tokens tenant-aware.

Las dependencias actuales deben seguir aceptando legacy tokens donde ya lo hacían.

No migrar endpoints clínicos todavía.

---

# 7. Schema de login tenant futuro

Crear schema interno/API preparado, por ejemplo:

```json
{
  "empresa_codigo": "VISION-CLARA",
  "correo": "admin@demo.example",
  "password": "..."
}
```

Validaciones:

```text
empresa_codigo requerido
trim
uppercase normalizado si la convención actual lo permite
máximo razonable
correo válido
password no vacío
extra="forbid"
```

No aceptar:

```text
database_name
tenant_id
empresa_id
```

desde el cliente para resolver DB.

---

# 8. Servicio de autenticación tenant

Crear un servicio separado o método explícito, por ejemplo:

```text
autenticar_usuario_tenant(...)
```

Flujo:

```text
empresa_codigo
→ TenantResolver
→ exige tenant DB ACTIVA
→ obtiene Session tenant
→ busca usuario por correo
→ verifica estado
→ verifica Argon2
→ genera JWT tenant-aware
```

Como NO existe ninguna tenant DB ACTIVA todavía:

```text
las pruebas usan mocks/fakes
```

No intentar hacer E2E real contra tenant_vision_clara.

---

# 9. Seguridad

La contraseña:

```text
nunca se loggea
nunca se devuelve
nunca se copia al Control Plane
```

El Control Plane no contiene usuarios clínicos.

Los usuarios siguen viviendo en cada tenant DB.

No permitir que el cliente seleccione database_name.

Nunca confiar en tenant_id enviado por body.

---

# 10. Nuevo endpoint: preparación sin cutover

Para no romper `/auth/login` actual, crear un endpoint SEPARADO temporal/preparatorio:

```http
POST /auth/tenant/login
```

o una ruta equivalente coherente con el router real.

Este endpoint:

```text
recibe empresa_codigo + correo + password
usa TenantResolver
si tenant DB PENDIENTE → devuelve error controlado
```

NO debe reemplazar `/auth/login` todavía.

Más adelante, cuando 7D/7E estén listos:

```text
frontend migrará al tenant login
legacy login será retirado o restringido
```

---

# 11. Códigos HTTP

Usar el patrón existente del proyecto.

Conceptualmente:

```text
empresa inexistente/suspendida → 401/403 según convención
suscripción inválida           → 403
tenant DB no disponible        → 503 o 409 controlado
credenciales inválidas         → 401
usuario inactivo               → 403
```

Evitar revelar si el correo existe.

Para credenciales inválidas:

```text
"Credenciales inválidas"
```

sin distinguir correo/password.

---

# 12. Dependencia de claims tenant

Preparar helper/dependency:

```text
get_tenant_claims(...)
```

o equivalente.

Debe:

```text
decodificar JWT
verificar token_type == tenant
extraer tenant_id/empresa_id/codigo
validar tipos
```

Pero NO conectarlo aún a routers clínicos.

Legacy endpoints siguen usando dependencia actual.

---

# 13. Protección contra token manipulation

Tests deben demostrar:

```text
tenant_id alterado sin firma válida → rechazo
empresa_id alterado sin firma válida → rechazo
token_type incorrecto → rechazo en dependencia tenant
claims faltantes → rechazo
```

No aceptar claims tenant desde headers alternativos tipo:

```text
X-Tenant-ID
X-Database
```

El tenant solo viene del JWT firmado.

---

# 14. Resolver nuevamente contra Control Plane

Aunque el JWT tenga `tenant_id`, NO confiar para siempre en el estado capturado.

Preparar mecanismo para que, al obtener contexto tenant en request futuro:

```text
JWT claims
→ Control Plane
→ validar empresa/suscripción/DB siguen ACTIVAS
→ obtener TenantContext actual
```

En 7C puede quedar como helper interno testeado.

Esto permitirá que:

```text
empresa suspendida
```

bloquee requests aunque el JWT todavía no haya expirado.

---

# 15. Revocación / suspensión

No implementar blacklist de tokens todavía.

Pero debe quedar posible bloquear por estado actual:

```text
empresa SUSPENDIDA
suscripción SUSPENDIDA/VENCIDA
tenant DB SUSPENDIDA
```

al resolver cada request tenant.

---

# 16. Tests

Crear, por ejemplo:

```text
tests/test_auth_tenant.py
```

Cubrir mínimo:

1. schema requiere empresa_codigo;
2. no acepta database_name;
3. no acepta tenant_id body;
4. normalización empresa_codigo;
5. tenant inexistente;
6. empresa suspendida;
7. suscripción inválida;
8. tenant DB PENDIENTE;
9. usuario inexistente → credenciales inválidas;
10. password incorrecto → mismo mensaje;
11. usuario inactivo;
12. login tenant válido mock;
13. JWT tenant contiene claims requeridos;
14. JWT no contiene database_name;
15. JWT no contiene secrets;
16. dependencia tenant acepta token tenant válido;
17. dependencia tenant rechaza legacy token;
18. dependencia tenant rechaza claims faltantes;
19. token manipulado falla firma;
20. tenant_id alterado falla firma;
21. empresa_id alterado falla firma;
22. estado actual del Control Plane puede invalidar token;
23. `/auth/login` legacy sigue funcionando en tests existentes;
24. endpoints clínicos no fueron migrados;
25. no se conecta a tenant DB real PENDIENTE.

---

# 17. Prueba real read-only

Se puede probar:

```text
POST /auth/tenant/login
```

con `VISION-CLARA` y credenciales ficticias/no sensibles.

Como `tenant_database` está PENDIENTE, debe fallar ANTES de abrir DB tenant con un error controlado.

No intentar validar usuario real.

No loggear password.

---

# 18. Suite

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

---

# 19. No hacer

NO:

- reemplazar /auth/login actual
- obligar frontend a enviar empresa
- crear tenant DB física
- cambiar estado PENDIENTE
- migrar usuarios
- migrar pacientes/citas
- cambiar get_db clínico
- Angular
- móvil
- backup
- restore
- realtime
- admin SaaS
- .env
- commit
- push
- merge

---

# 20. Criterio de terminado 7C

```text
[ ] schema login tenant
[ ] servicio autenticar tenant
[ ] endpoint preparatorio /auth/tenant/login
[ ] JWT tenant-aware
[ ] token_type tenant
[ ] claims tenant seguros
[ ] sin database_name en JWT
[ ] dependencia tenant separada
[ ] legacy login intacto
[ ] legacy tokens intactos
[ ] revalidación contra Control Plane preparada
[ ] tenant PENDIENTE bloqueado
[ ] tests
[ ] suite completa verde
[ ] no cutover todavía
```
