# CONTEXTO_SAAS_7D3_VALIDAR_LOGIN_TENANT.md

## 1. Estado previo

Proyecto:
`C:\SI2_Proyecto\clinica-oftalmologica-api`

Completado:

```text
7A ✅ Control Plane SaaS
7B ✅ Tenant Connection Router
7C ✅ Auth/JWT tenant-aware preparado
7D.1 ✅ Auditoría/preparación
7D.2 ✅ Creación/restauración física
7D.2R ✅ Recuperación controlada
7D.2V ✅ Verificador semántico + reconciliación
```

Estado real:

```text
empresa: VISION-CLARA
database_name: tenant_vision_clara
tenant_database.estado: ACTIVA
provisionamiento.estado: COMPLETADO
version_schema: v1

TenantResolver: OK
TenantEngineRegistry: OK
SELECT 1: OK

conteos:
usuario = 14
paciente = 10
cita = 8
```

La base `tenant_vision_clara` contiene una copia verificada del esquema y datos clínicos actuales.

---

# 2. Objetivo 7D.3

Validar el login tenant REAL contra:

```text
POST /seguridad/tenant/login
```

usando:

```text
empresa_codigo = VISION-CLARA
```

y una cuenta existente conocida por el usuario.

NO compartir ni registrar la contraseña.

El objetivo es demostrar:

```text
empresa → Control Plane → tenant_vision_clara → usuario tenant → Argon2 → JWT tenant-aware
```

---

# 3. No hacer

NO:

- modificar datos clínicos
- crear otro tenant
- cambiar estados del Control Plane
- hacer DROP/dump/restore
- migrar routers clínicos todavía
- hacer cutover del login legacy
- modificar frontend/móvil
- imprimir contraseña
- imprimir DATABASE_URL
- imprimir token completo en logs/documentos
- commit/push/merge

---

# 4. Preflight

Antes del login:

1. Backend local levantado.
2. `VISION-CLARA` resuelve ACTIVA.
3. `check_tenant_connection` pasa.
4. La tabla `usuario` del tenant tiene 14 filas.
5. No cambiar el `DATABASE_URL` principal.
6. No tocar `.env`.

---

# 5. Prueba manual recomendada

Usar Swagger/OpenAPI del backend local.

Endpoint:

```http
POST /seguridad/tenant/login
```

Body:

```json
{
  "empresa_codigo": "VISION-CLARA",
  "correo": "<correo conocido>",
  "password": "<password conocido>"
}
```

No pegar la contraseña ni el token completo en reportes.

Resultado esperado:

```text
HTTP 200
access_token presente
token_type bearer o contrato actual
```

---

# 6. Validación de claims

Decodificar el JWT localmente con la lógica del proyecto o mediante test seguro.

Verificar solamente presencia/valores no sensibles:

```text
sub
rol_id
tenant_id
empresa_id
empresa_codigo = VISION-CLARA
token_type = tenant
exp
```

Verificar ausencia:

```text
database_name
DATABASE_URL
host
port
password
connection string
```

No imprimir el token completo.

---

# 7. Verificación de origen real

Instrumentar de forma temporal solo en test o usar mocks/spies seguros para comprobar que el login:

```text
NO usa SessionLocal clínico legacy para buscar el usuario
SÍ usa TenantResolver
SÍ usa TenantEngineRegistry
SÍ abre sesión sobre tenant_vision_clara
```

No agregar logs permanentes con connection strings.

---

# 8. Casos negativos reales

Sin exponer credenciales reales:

1. empresa inexistente → rechazo controlado.
2. empresa válida + password incorrecto → `Credenciales inválidas`.
3. `database_name` enviado en body → 422.
4. `tenant_id` enviado en body → 422.
5. token legacy no satisface `get_tenant_claims`.
6. token tenant válido sí satisface `get_tenant_claims`.

No bloquear cuentas ni hacer intentos repetidos innecesarios.

---

# 9. Tests

Mantener suite existente.

Agregar solo si falta cobertura E2E/integración no destructiva.

Ejecutar:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

---

# 10. Criterio de cierre 7D

7D queda CERRADO cuando:

```text
[ ] tenant_vision_clara ACTIVA
[ ] provisionamiento COMPLETADO
[ ] resolver real OK
[ ] engine registry real OK
[ ] health check OK
[ ] login tenant real HTTP 200
[ ] JWT tenant claims correctos
[ ] no secrets en token
[ ] login legacy sigue intacto
[ ] suite verde
```
