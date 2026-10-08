# MASTER_PROMPT_SAAS_7J_SELECTOR_CAMBIO_EMPRESA.md

Trabaja en este orden.

BACKEND:
C:\SI2_Proyecto\clinica-oftalmologica-api

FRONTEND:
C:\SI2_Proyecto\clinica-oftalmologica-web

Estamos en:

PASO 7J — SELECTOR Y CAMBIO SEGURO DE EMPRESA

Lee completos:

- CONTEXTO_SAAS_7J_SELECTOR_CAMBIO_EMPRESA.md
- MASTER_PROMPT_SAAS_7J_SELECTOR_CAMBIO_EMPRESA.md

PASO 7I está cerrado.
NO tocar backup/restore todavía.

==================================================
FASE 1 — AUDITORÍA
==================================================

Backend:
- inspecciona auth tenant actual;
- inspecciona TenantResolver;
- inspecciona schemas;
- inspecciona rutas.

Frontend:
- inspecciona ruta real de login clínico;
- AuthService clínico;
- almacenamiento del token;
- layout/sidebar/header clínico;
- app.routes.ts;
- interceptor;
- SaaS empresas page.

No asumir nombres ni rutas.

==================================================
FASE 2 — ENDPOINT PÚBLICO EMPRESAS
==================================================

Crear endpoint read-only coherente con arquitectura actual.

Preferencia:

GET /seguridad/tenant/empresas

Debe devolver SOLO empresas aptas para login:

- codigo
- nombre
- slug solo si ya es útil

Filtros:

empresa.estado = ACTIVA
tenant_database.estado = ACTIVA
suscripción válida si TenantResolver la exige para login

NO devolver:

- database_name
- ids internos innecesarios
- connection strings
- provisioning
- URLs
- secrets

No requiere JWT.

No hardcodear 7.

==================================================
FASE 3 — LOGIN CLÍNICO
==================================================

Agregar selector de empresa usando el endpoint real.

Debe:

- cargar empresas activas;
- mostrar nombre;
- enviar codigo como empresa_codigo;
- soportar empresa preseleccionada por query param/router state;
- mantener correo/password como hasta ahora;
- no guardar password;
- manejar loading/error con signals si el proyecto ya usa zoneless.

No romper login legacy salvo que ya exista decisión de deprecación.

==================================================
FASE 4 — EMPRESA ACTUAL
==================================================

Dentro del layout clínico autenticado:

mostrar claramente:

Empresa actual: <nombre>

y agregar:

Cambiar empresa

No mostrar database_name.

==================================================
FASE 5 — CAMBIAR EMPRESA
==================================================

Al elegir otra empresa:

1. NO reutilizar JWT actual.
2. Eliminar SOLO token clínico/tenant actual.
3. Limpiar contexto tenant del frontend.
4. Mantener saas_access_token intacto.
5. Navegar al login clínico.
6. Pasar empresa destino preseleccionada.
7. Exigir correo/password para ese tenant.
8. Después del login exitoso usar el nuevo JWT.

No SSO.
No impersonation.

==================================================
FASE 6 — ABRIR EMPRESA DESDE SAAS
==================================================

En:

/saas/empresas

Agregar botón:

Abrir empresa

Debe navegar al login clínico con esa empresa preseleccionada.

NO usar saas_access_token como token clínico.
NO generar JWT tenant.
NO saltarse login.

==================================================
FASE 7 — EMPRESA SUSPENDIDA
==================================================

El endpoint público debe excluirla.

Si queda seleccionada por una pantalla vieja:
el backend debe seguir bloqueando login.

Frontend muestra error saneado.

==================================================
FASE 8 — TESTS BACKEND
==================================================

Cubrir:

- lista empresas activas;
- 7 actuales en fixture/realistic test;
- suspendida excluida;
- tenant DB inactiva excluida;
- sin JWT;
- sin database_name;
- sin secrets;
- login tenant intacto;
- cross-tenant 401.

Ejecutar:

.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
FASE 9 — TESTS FRONTEND
==================================================

Cubrir:

- selector usa API;
- no lista hardcodeada;
- preselección;
- cambiar empresa borra solo tenant token;
- mantiene saas token;
- redirige;
- empresa actual visible;
- SaaS abrir empresa;
- no impersonation;
- login destino crea nuevo token;
- errores;
- DOM async real con signals;
- responsive básico.

Ejecutar:

npm test -- --watch=false
npx.cmd tsc --noEmit -p tsconfig.app.json
npm run build
git diff --check

==================================================
FASE 10 — E2E MANUAL
==================================================

No usar secrets en reporte.

Prueba:

1. Login VISION-CLARA → 200.
2. UI muestra VISION-CLARA.
3. Cambiar empresa → OFTALMO-NORTE.
4. Token tenant actual se elimina.
5. Login muestra OFTALMO-NORTE preseleccionada.
6. Usar cuenta válida de OFTALMO-NORTE → 200.
7. UI muestra OFTALMO-NORTE.
8. Confirmar que no aparecen datos de VISION-CLARA.
9. Desde SaaS Admin usar "Abrir empresa" MEDICO-OCULAR.
10. Debe ir al login tenant MEDICO-OCULAR, NO entrar automáticamente.

==================================================
NO HACER
==================================================

NO:
- reutilizar JWT cross-tenant
- SSO
- impersonation
- hardcodear empresas
- exponer database_name
- backup/restore
- realtime
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. ENDPOINT PÚBLICO
3. PAYLOAD PÚBLICO
4. FILTROS
5. LOGIN CLÍNICO
6. SELECTOR
7. EMPRESA ACTUAL
8. CAMBIAR EMPRESA
9. TOKEN CLEAR
10. PRESELECCIÓN
11. SAAS ABRIR EMPRESA
12. EMPRESA SUSPENDIDA
13. SEGURIDAD
14. TESTS BACKEND
15. TESTS FRONTEND
16. TYPECHECK
17. BUILD
18. E2E MANUAL PENDIENTE/RESULTADO
19. GIT STATUS

Confirma:
- no JWT reutilizado entre empresas
- no impersonation
- no secrets
- no database_name público
- no commit/push/merge
