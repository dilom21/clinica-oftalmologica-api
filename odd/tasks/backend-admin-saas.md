# Backend Administrador SaaS — PASO 7G

## Objetivo
Crear un backend administrativo SaaS separado de los administradores clínicos, operando únicamente sobre `saas_control` y sin exponer secretos ni datos clínicos.

## Alcance autorizado
- Login SaaS con JWT `token_type=saas_admin`.
- Dependencia de autenticación SaaS independiente de JWT tenant/legacy.
- Consultas administrativas de empresas, planes, suscripciones, tenants, provisionamientos y bitácora.
- Cambio opcional de estado de empresa/suscripción, sin apagar ni borrar bases físicas.
- Bootstrap interactivo de un usuario SaaS.
- Tests aislados en memoria; no ejecutar bootstrap real.

## Restricciones
- Trabajar exclusivamente en este repositorio.
- No frontend, backup/restore, realtime, migraciones clínicas, `.env`, commit, push ni merge.
- No devolver `DATABASE_URL`, credenciales, tokens, contraseñas ni connection strings.

## Tareas
- [x] T7G-01 Mapear modelos SQLAlchemy y schemas del control plane.
- [x] T7G-02 Implementar autenticación SaaS y bootstrap interactivo.
- [x] T7G-03 Implementar endpoints administrativos y bitácora saneada.
- [x] T7G-04 Registrar router sin mezclar rutas clínicas.
- [x] T7G-05 Crear tests aislados de autenticación, listados, seguridad, saneamiento y cambios de estado.
- [x] T7G-06 Ejecutar bloque de tests, suite completa y `git diff --check` después de corregir los hallazgos.

## Criterios de aceptación
- Los 7 tenants se listan desde metadata del control plane sin abrir sus DB.
- Solo JWT con `token_type=saas_admin` autoriza endpoints SaaS.
- Suspender empresa impide resolución tenant; reactivar la permite.
- Los tests no conectan a la BD compartida y no contienen secretos.

## Ruta y verificación
- Ruta: delegated direct, porque la implementación toca múltiples archivos no triviales.
- TDD efectivo: pendiente de confirmar en configuración existente; usar checks funcionales si no está configurado.
- Verificación: `\.venv\Scripts\python.exe -m pytest tests/test_saas_admin_backend.py -q`, `\.venv\Scripts\python.exe -m pytest -q`, `git diff --check`.

## Progreso
- Exploración del contexto y control plane completada.
- Se corrigió el rechazo explícito de JWT SaaS en la dependencia clínica y se amplió la cobertura aislada a siete empresas/tenants.
- Se reforzó el saneamiento de mensajes de provisionamiento para no devolver claves sensibles ni connection strings.
- Verificación ejecutada: bloque dirigido `9 passed`; suite completa `623 passed, 2 warnings`; `git diff --check` OK con advertencias informativas de conversión LF/CRLF de Git.
- No se ejecutó bootstrap real ni se modificó `.env`.
