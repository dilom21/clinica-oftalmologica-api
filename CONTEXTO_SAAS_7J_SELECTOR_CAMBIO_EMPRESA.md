# CONTEXTO_SAAS_7J_SELECTOR_CAMBIO_EMPRESA.md

## 1. Identificación

Proyecto: Clínica Oftalmológica  
Paso: 7J — Selector y cambio seguro de empresa  
Repositorios:

Backend:
`C:\SI2_Proyecto\clinica-oftalmologica-api`

Frontend:
`C:\SI2_Proyecto\clinica-oftalmologica-web`

## 2. Estado previo

PASO 7I cerrado correctamente:

- SaaS Admin real creado.
- Login SaaS real funciona.
- Dashboard SaaS muestra datos reales.
- 7 empresas activas.
- 7 tenants activos.
- 7 suscripciones activas.
- 3 planes.
- 0 provisionamientos con error.
- Suspensión lógica probada.
- Login tenant bloqueado cuando empresa está suspendida.
- Reactivación probada.
- Login tenant vuelve a 200.
- Aislamiento cross-tenant probado con 401.
- Tokens SaaS y tenant separados.

## 3. Objetivo

Permitir cambiar de empresa de forma visible y segura, sin reutilizar el JWT del tenant actual.

La UX debe demostrar claramente SaaS multiempresa.

Flujo correcto:

Empresa A
→ usuario autenticado en tenant A
→ "Cambiar empresa"
→ seleccionar Empresa B
→ eliminar token tenant actual
→ ir al login tenant con Empresa B preseleccionada
→ usuario vuelve a autenticarse
→ backend resuelve tenant B
→ nuevo JWT de tenant B
→ sistema clínico opera contra tenant B

NUNCA:

Empresa A JWT
→ cambiar solo empresa_codigo
→ reutilizar mismo JWT

Eso rompería aislamiento.

## 4. Endpoint público mínimo para selector

No hardcodear las 7 empresas en Angular.

Crear un endpoint público, de solo lectura, con metadata mínima y segura.

Ruta sugerida:

`GET /seguridad/tenant/empresas`

o equivalente coherente con la arquitectura actual.

Debe devolver únicamente empresas disponibles para login tenant:

- codigo
- nombre
- slug, solo si ya existe y es necesario para UI

Solo empresas ACTIVAS y con tenant_database ACTIVA.

NO devolver:

- database_name
- tenant_database.id
- connection strings
- plan interno
- suscripciones
- provisioning
- URLs
- credenciales
- secretos

No requiere JWT.

## 5. Login tenant frontend

Inspeccionar el login clínico actual.

Agregar selector de empresa:

- listado real desde backend;
- código como value;
- nombre como label;
- empresa preseleccionada desde query param o router state cuando venga de "Cambiar empresa" o "Abrir empresa".

El request sigue siendo:

POST /seguridad/tenant/login

con:

- empresa_codigo
- correo
- password

No cambiar contrato si no es necesario.

## 6. Cambio de empresa dentro del sistema clínico

En el layout clínico autenticado, agregar una acción visible:

`Cambiar empresa`

Debe:

1. mostrar selector o llevar al selector/login;
2. al confirmar cambio:
   - eliminar SOLO token tenant actual;
   - limpiar contexto tenant en frontend;
   - NO eliminar saas_access_token;
   - redirigir al login tenant;
   - preseleccionar empresa destino;
3. pedir credenciales de la empresa destino.

No intentar SSO entre tenants en este paso.

## 7. Abrir empresa desde SaaS Admin

En `/saas/empresas`, agregar acción:

`Abrir empresa`

Debe:

- NO usar JWT SaaS para entrar al sistema clínico;
- NO crear JWT tenant automáticamente;
- NO impersonar usuarios;
- navegar al login clínico con empresa preseleccionada;
- conservar `saas_access_token` separado si la aplicación actual lo permite.

Ejemplo conceptual:

`/login?empresa=MEDICO-OCULAR`

Usar la ruta real del login clínico después de inspeccionarla.

## 8. Identidad de empresa actual

Dentro del layout clínico mostrar claramente la empresa actual.

Preferencia:
- nombre visible;
- código opcional secundario.

No inferir el nombre desde database_name.

El nombre puede resolverse desde el selector/listado público o desde contexto seguro ya disponible.

## 9. Seguridad

Obligatorio:

- token tenant incluye empresa/tenant.
- backend sigue revalidando tenant.
- cambiar empresa elimina el token tenant actual.
- empresa destino exige login propio.
- usuario de empresa A no puede entrar a B si no existe allí.
- SaaS token nunca autoriza endpoints clínicos.
- tenant token nunca autoriza endpoints SaaS.

## 10. Empresas suspendidas

El selector público NO debe listar empresas suspendidas.

Si una empresa se suspende después de que la pantalla ya la cargó:
- POST /seguridad/tenant/login debe rechazarla;
- frontend muestra mensaje saneado;
- no debe entrar.

## 11. Responsive y UX

El selector debe funcionar en móvil y escritorio.

No convertir la cabecera en un dropdown confuso si eso empeora responsive.

Puede ser:
- botón "Cambiar empresa";
- modal;
- pantalla intermedia;
- login con selector.

Prioridad:
- claridad;
- seguridad;
- empresa actual visible.

## 12. Tests backend

Cubrir:

- endpoint público lista 7 empresas activas;
- no expone database_name;
- no expone secrets;
- empresa suspendida desaparece;
- tenant_database no activa desaparece;
- endpoint sin JWT funciona;
- orden estable por nombre/código si aplica;
- login sigue requiriendo empresa_codigo;
- cross-tenant sigue 401.

## 13. Tests frontend

Cubrir:

- login muestra empresas reales;
- no hardcodeadas;
- preselección por query param;
- cambio empresa borra solo tenant token;
- SaaS token permanece;
- redirección al login destino;
- empresa destino visible;
- SaaS "Abrir empresa" no impersona;
- login exitoso crea nuevo token tenant;
- empresa suspendida maneja error;
- responsive básico;
- token clínico no se reutiliza.

## 14. No hacer

NO:

- SSO cross-tenant;
- impersonación;
- reutilizar JWT de otro tenant;
- hardcodear lista de empresas;
- exponer database_name en endpoint público;
- backup/restore todavía;
- realtime;
- commit/push/merge.

## 15. Criterio de cierre

[ ] endpoint público de empresas seguras  
[ ] selector real en login clínico  
[ ] empresa actual visible  
[ ] acción Cambiar empresa  
[ ] elimina token tenant actual  
[ ] preselecciona empresa destino  
[ ] exige re-login  
[ ] SaaS "Abrir empresa" seguro  
[ ] empresa suspendida no aparece  
[ ] tests backend  
[ ] tests frontend  
[ ] build frontend  
[ ] aislamiento preservado
