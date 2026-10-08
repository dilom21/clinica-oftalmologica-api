# CONTEXTO_SAAS_7I_E2E_MULTITENANT_COMPLETO.md

## 1. Identificación

**Proyecto:** Clínica Oftalmológica  
**Bloque:** 7I — E2E SaaS Multitenant completo  
**Repositorios involucrados:**

Backend:
`C:\SI2_Proyecto\clinica-oftalmologica-api`

Frontend:
`C:\SI2_Proyecto\clinica-oftalmologica-web`

---

# 2. Estado previo

Completado:

```text
7A ✅ Control Plane SaaS
7B ✅ Tenant Connection Router
7C ✅ Auth/JWT tenant-aware
7D ✅ Primer tenant real
7E ✅ Segundo tenant + aislamiento A/B
7F ✅ 7 tenants físicos provisionados
7G ✅ Backend SaaS Admin
7H ✅ Frontend SaaS Admin
```

Tenants confirmados:

```text
VISION-CLARA       ACTIVA / COMPLETADO
OFTALMO-NORTE      ACTIVA / COMPLETADO
VISUAL-ORIENTAL    ACTIVA / COMPLETADO
INSTITUTO-VISION   ACTIVA / COMPLETADO
OFTALMOCARE        ACTIVA / COMPLETADO
VISTA-SUR          ACTIVA / COMPLETADO
MEDICO-OCULAR      ACTIVA / COMPLETADO
```

Frontend SaaS:

```text
/saas/login
/saas
/saas/empresas
/saas/planes
/saas/suscripciones
/saas/tenants
/saas/provisionamientos
/saas/bitacora
```

Backend SaaS:

```text
POST  /saas/auth/login
GET   /saas/empresas
GET   /saas/empresas/{empresa_id}
GET   /saas/planes
GET   /saas/suscripciones
GET   /saas/tenants
GET   /saas/provisionamientos
GET   /saas/bitacora
PATCH /saas/empresas/{empresa_id}/estado
PATCH /saas/suscripciones/{suscripcion_id}/estado
```

Pendiente:
bootstrap REAL de un SaaS Admin y validación E2E completa.

---

# 3. Objetivo

Cerrar el bloque SaaS demostrando de punta a punta:

```text
SaaS Admin real
→ login
→ dashboard
→ metadata de 7 empresas
→ tenants
→ planes
→ suscripciones
→ provisionamientos
→ bitácora
→ suspensión de una empresa
→ bloqueo real del login tenant
→ reactivación
→ login tenant vuelve a funcionar
→ aislamiento de empresas
```

No modificar datos clínicos.

---

# 4. Bootstrap SaaS Admin real

Usar el script ya creado:

```text
scripts/saas/bootstrap_saas_admin.py
```

Debe ejecutarlo el usuario MANUALMENTE desde PowerShell porque solicita contraseña por `getpass`.

No usar password por CLI.

No pegar password ni token completo en chat, logs o documentos.

Correo recomendado para demo:

```text
admin@saas.demo
```

El usuario puede elegir otro correo.

---

# 5. Backend local

Levantar desde:

```text
C:\SI2_Proyecto\clinica-oftalmologica-api
```

Comando esperado:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Verificar:

```text
http://127.0.0.1:8000/docs
```

---

# 6. Frontend local

Levantar desde:

```text
C:\SI2_Proyecto\clinica-oftalmologica-web
```

Usar el comando real del proyecto, normalmente:

```powershell
npm start
```

o:

```powershell
ng serve
```

No cambiar producción.

Verificar que `environment.development.ts` apunte al backend local si ya existe esa configuración.

No cambiar URL productiva salvo bug real.

---

# 7. Login SaaS E2E

Abrir:

```text
http://localhost:4200/saas/login
```

o el puerto real de Angular.

Usar:

```text
correo SaaS Admin
password bootstrap
```

Esperado:

```text
HTTP 200
token SaaS almacenado en saas_access_token
redirect /saas
```

No mostrar token.

---

# 8. Dashboard

Verificar datos reales:

```text
Empresas totales = 7
Tenants activos = 7
Suscripciones activas = 7
Planes = 3
Provisionamientos error = 0
```

Usar valores reales del backend si cambian.

No aceptar métricas hardcodeadas.

---

# 9. Empresas

Verificar que aparecen las 7:

```text
VISION-CLARA
OFTALMO-NORTE
VISUAL-ORIENTAL
INSTITUTO-VISION
OFTALMOCARE
VISTA-SUR
MEDICO-OCULAR
```

Metadata esperada:

```text
estado empresa
plan
suscripción
database_name
estado tenant
version_schema
fecha provisioning
```

No secrets.

---

# 10. Tenants

Verificar 7 filas y 7 `database_name` distintas:

```text
tenant_vision_clara
tenant_oftalmo_norte
tenant_visual_oriental
tenant_instituto_vision
tenant_oftalmocare
tenant_vista_sur
tenant_medico_ocular
```

---

# 11. Suscripciones

Verificar:

```text
7 suscripciones
todas vigentes/ACTIVAS antes de la prueba de estado
```

No cambiar plan en 7I salvo que sea estrictamente necesario.

---

# 12. Provisionamientos

Verificar:

```text
7 registros
estado COMPLETADO
```

No deben aparecer errores residuales.

---

# 13. Bitácora SaaS

Después del login SaaS debe existir al menos:

```text
LOGIN_SAAS
```

Después de suspender/reactivar una empresa:

```text
CAMBIAR_ESTADO_EMPRESA
```

No passwords, tokens ni connection strings.

---

# 14. Prueba de suspensión real

Usar una empresa que NO sea VISION-CLARA para reducir riesgo.

Preferencia:

```text
MEDICO-OCULAR
```

Flujo:

```text
1. confirmar ACTIVA
2. suspender desde frontend SaaS
3. confirmar metadata SUSPENDIDA
4. intentar login tenant MEDICO-OCULAR
5. debe ser rechazado por Control Plane
6. confirmar DB física sigue existiendo
7. reactivar empresa
8. login tenant vuelve a funcionar
```

No tocar suscripción en esta prueba si no es necesario.

No apagar ni borrar database física.

---

# 15. Login tenant MEDICO-OCULAR

Cuenta bootstrap:

```text
admin@medicoocular.demo
```

La contraseña fue creada por el usuario durante 7F.

No pedir que la comparta.

Antes de suspender:

```text
login tenant → 200
```

Suspendida:

```text
login tenant → rechazo controlado
```

Reactivada:

```text
login tenant → 200
```

El código HTTP exacto durante suspensión debe corresponder al backend actual (401/403 según contrato real).

---

# 16. Aislamiento

Reconfirmar:

```text
admin@medicoocular.demo + MEDICO-OCULAR → 200
admin@medicoocular.demo + VISION-CLARA → 401
```

No hacer muchos intentos.

---

# 17. Token separation

Verificar en navegador:

```text
saas_access_token
```

separado de:

```text
access_token clínico
```

Comprobar:

```text
logout SaaS NO elimina token clínico
401 SaaS NO elimina token clínico
logout clínico NO elimina token SaaS salvo que diseño explícitamente lo decida
```

No mostrar valores.

---

# 18. Seguridad SaaS Admin

Probar:

```text
JWT tenant → /saas/empresas → rechazado
JWT legacy → /saas/empresas → rechazado
JWT SaaS → endpoint clínico → rechazado
```

Puede hacerse por tests existentes si ya está cubierto; no hace falta exponer tokens manualmente.

---

# 19. Validación final de 7 tenants

READ-ONLY:

Para las 7 empresas:

```text
empresa ACTIVA
suscripción ACTIVA
tenant_database ACTIVA
provisionamiento COMPLETADO
version_schema v1
```

Después de reactivar MEDICO-OCULAR.

No abrir todas las DB si no es necesario; Control Plane es suficiente para metadata.

Health real de muestra:

```text
VISION-CLARA
OFTALMO-NORTE
MEDICO-OCULAR
```

SELECT 1.

---

# 20. Tests finales

Backend:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_saas_admin_backend.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_auth_tenant.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Frontend:

```powershell
npm test -- --watch=false
npm run build
```

Ejecutar typecheck si existe.

---

# 21. No hacer

NO:

- modificar datos clínicos
- crear más tenants
- DROP/dump/restore
- migrar routers clínicos todavía
- backup/restore
- realtime
- cambiar .env
- commit/push/merge

---

# 22. Criterio de cierre 7I / BLOQUE SaaS

```text
[ ] SaaS Admin real creado
[ ] login SaaS 200
[ ] frontend SaaS usable
[ ] 7 empresas visibles
[ ] 7 tenants visibles
[ ] 3 planes
[ ] 7 suscripciones
[ ] 7 provisionamientos completados
[ ] bitácora SaaS
[ ] suspensión empresa funciona
[ ] tenant login bloqueado al suspender
[ ] DB física permanece
[ ] reactivación funciona
[ ] login tenant vuelve a 200
[ ] aislamiento cross-tenant sigue funcionando
[ ] tokens SaaS/clínico separados
[ ] suite backend verde
[ ] tests/build frontend verdes
```

Al completar esto:

```text
PASO 7 — SaaS + Tenant + Multitenant = COMPLETO
```

El siguiente bloque será:

```text
PASO 8 — Backup manual + automático + Restore por tenant
```
