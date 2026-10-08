# MASTER_PROMPT_SAAS_7F_PROVISIONAR_5_TENANTS_RESTANTES.md

Trabaja EXCLUSIVAMENTE en:

C:\SI2_Proyecto\clinica-oftalmologica-api

Estamos en:

PASO 7F — PROVISIONAR LOS 5 TENANTS RESTANTES

Lee COMPLETOS:

- CONTEXTO_SAAS_7F_PROVISIONAR_5_TENANTS_RESTANTES.md
- MASTER_PROMPT_SAAS_7F_PROVISIONAR_5_TENANTS_RESTANTES.md
- scripts/saas/provision_tenant.py
- tests/test_tenant_provisioning.py

Estado confirmado:

VISION-CLARA = ACTIVA / COMPLETADO
OFTALMO-NORTE = ACTIVA / COMPLETADO
Aislamiento login A/B = correcto

Quedan:

VISUAL-ORIENTAL
INSTITUTO-VISION
OFTALMOCARE
VISTA-SUR
MEDICO-OCULAR

==================================================
OBJETIVO
==================================================

Provisionar los 5 tenants restantes con flujo CLEAN ya estabilizado.

SECUENCIAL.
NO paralelo.
FAIL-CLOSED.

Si uno falla:
STOP y no procesar los siguientes.

==================================================
1. PREFLIGHT GLOBAL
==================================================

Antes de mutar nada verificar los 5:

- empresa existe
- suscripción ACTIVA
- tenant_database PENDIENTE
- provisionamiento PENDIENTE
- DB física inexistente
- database_name válido
- PostgreSQL tools disponibles
- schema v1 fuente válido

Si cualquiera falla:
STOP antes de crear el primero.

==================================================
2. BATCH SEGURO
==================================================

Agregar modo explícito, por ejemplo:

--provision-pending-batch

Debe procesar EXACTAMENTE estos 5 tenants pendientes:

1. VISUAL-ORIENTAL
2. INSTITUTO-VISION
3. OFTALMOCARE
4. VISTA-SUR
5. MEDICO-OCULAR

NO incluir:
VISION-CLARA
OFTALMO-NORTE

NO descubrir y provisionar tenants arbitrarios fuera de este conjunto sin confirmación.

==================================================
3. ADMIN EMAILS
==================================================

Usar configuración explícita:

VISUAL-ORIENTAL:
admin@visualoriental.demo

INSTITUTO-VISION:
admin@institutovision.demo

OFTALMOCARE:
admin@oftalmocare.demo

VISTA-SUR:
admin@vistasur.demo

MEDICO-OCULAR:
admin@medicoocular.demo

Password:
pedir interactivamente con getpass DOS VECES por cada tenant.

NO CLI password.
NO logs.
NO Control Plane.
NO reutilización automática.

==================================================
4. FLUJO POR TENANT
==================================================

Reutilizar el MISMO flujo clean ya probado.

Para cada tenant:

- revalidar Control Plane
- solicitar password
- PROVISIONANDO
- CREATE DB
- schema-only public
- seed 5 catálogos
- bootstrap admin
- verify clean
- health check
- ACTIVA / COMPLETADO

No duplicar lógica si puede extraerse/reutilizarse.

==================================================
5. FAIL-CLOSED
==================================================

Si tenant N falla:

- marcar solo ese tenant ERROR según flujo existente
- NO tocar tenants anteriores completados
- NO continuar N+1
- NO hacer recovery automático
- NO DROP automático

Reportar exactamente cuál falló.

==================================================
6. VERIFY
==================================================

Por cada tenant verificar:

- 27 tablas
- 27 secuencias
- 182 columnas
- constraints semánticas OK
- 67 índices
- schemas prohibidos 0
- catálogos iguales a fuente
- usuario 1
- admin activo
- hash presente
- paciente 0
- cita 0
- historial_clinico 0
- consulta_clinica 0
- diagnostico 0
- receta 0
- bitacora 0
- token_recuperacion 0
- SELECT 1 OK

==================================================
7. AISLAMIENTO
==================================================

Al finalizar comprobar:

VISION-CLARA → tenant_vision_clara
OFTALMO-NORTE → tenant_oftalmo_norte
VISUAL-ORIENTAL → tenant_visual_oriental
INSTITUTO-VISION → tenant_instituto_vision
OFTALMOCARE → tenant_oftalmocare
VISTA-SUR → tenant_vista_sur
MEDICO-OCULAR → tenant_medico_ocular

Todas las database_name distintas.

EngineRegistry:
7 caches/engines independientes cuando se materializan.

No imprimir URLs.

==================================================
8. TESTS
==================================================

Agregar tests para:

- preflight global
- batch solo 5 permitidos
- orden secuencial
- no paralelo
- STOP en primer error
- anteriores exitosos se preservan
- siguientes no se tocan
- getpass por tenant
- emails correctos
- password no CLI/logs
- reutilización de provision-clean
- no inclusión VISION/NORTE
- 5 resultados exitosos
- aislamiento DB names
- engines separados

Ejecutar:

.\.venv\Scripts\python.exe -m pytest tests/test_tenant_provisioning.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check

==================================================
9. EJECUCIÓN REAL
==================================================

NO ejecutar desde OpenCode si existe riesgo de timeout externo.

Después de implementar/testear, entregar el comando PowerShell exacto al usuario.

Preferencia:

.\.venv\Scripts\python.exe scripts\saas\provision_tenant.py `
  --provision-pending-batch `
  --pg-bin-dir "C:\Program Files\PostgreSQL\18\bin" `
  --command-timeout-seconds 900

El usuario introducirá 5 passwords interactivamente.

==================================================
NO HACER
==================================================

NO:

- tocar VISION-CLARA
- tocar OFTALMO-NORTE
- paralelo
- datos clínicos copiados
- usuarios copiados
- routers clínicos
- frontend
- móvil
- backup
- realtime
- .env
- commit
- push
- merge

==================================================
REPORTE FINAL
==================================================

Entrega:

1. ESTADO
2. PREFLIGHT GLOBAL
3. MODO BATCH
4. ORDEN
5. EMAILS BOOTSTRAP
6. TESTS BLOQUE
7. SUITE COMPLETA
8. COMANDO POWERSHELL REAL
9. EJECUCIÓN REAL (si el usuario la realizó)
10. ESTADO DE LOS 7 TENANTS
11. AISLAMIENTO
12. CONTEOS
13. ENGINE REGISTRY
14. SECRETOS
15. GIT STATUS
