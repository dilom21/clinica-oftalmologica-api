# Hardening de zona horaria para reportes por voz

## Objetivo
Usar la fecha local configurable de la aplicación en el contexto temporal del prompt de `POST /ia/reportes/interpretar`, con `America/La_Paz` como valor predeterminado y sin afectar fechas almacenadas ni otros módulos.

## Alcance autorizado
- Backend únicamente en este repositorio.
- Agregar `APP_TIMEZONE` configurable por entorno, con fallback seguro a `America/La_Paz`.
- Centralizar la obtención de la fecha local para el prompt de reportes por voz usando `zoneinfo.ZoneInfo` y `datetime.now(zone).date()`.
- Ampliar únicamente `tests/test_ia_reportes_voz.py` con cobertura determinista del comportamiento temporal y privacidad existente.
- No tocar Angular, Supabase, `.env`, contratos, endpoints, SQL, fechas persistidas ni usos temporales de módulos no relacionados.
- No crear funcionalidades nuevas fuera de este hardening; no hacer commit, push ni merge.

## Checklist estable
- [x] Añadir configuración segura de `APP_TIMEZONE` sin exigir la variable al arrancar.
- [x] Implementar una utilidad centralizada de fecha local con `ZoneInfo` y fallback.
- [x] Sustituir únicamente `date.today()` del prompt de reportes por voz.
- [x] Cubrir default, configuración, valor inválido, frontera UTC y privacidad con reloj determinista.
- [x] Ejecutar el test específico y la suite completa.
- [x] Revisar diff para confirmar alcance mínimo y ausencia de cambios en `.env`.

## Criterios de aceptación
- Sin `APP_TIMEZONE`, la configuración expone exactamente `America/La_Paz` y la aplicación arranca.
- Un valor válido cambia la zona usada por la fecha del prompt.
- Un valor inválido no derriba la aplicación y usa `America/La_Paz`.
- En la frontera UTC `2026-10-05 02:00`, el prompt indica `2026-10-04` para `America/La_Paz`.
- El prompt no recibe filas de base de datos ni se modifican otros usos de fecha.
- Las pruebas existentes de reportes por voz continúan pasando.

## Checks
- `.\\.venv\\Scripts\\python.exe -m pytest tests/test_ia_reportes_voz.py -q`
- `.\\.venv\\Scripts\\python.exe -m pytest -q`
- `git diff --check`

## Progreso
- [x] ODD creado antes del primer cambio de implementación.
- [x] Implementación realizada.
- [x] Tests completados: 23 pruebas específicas y 444 pruebas de suite completa.
- [x] Revisión final completada; `git diff --check` sin errores.

## Ruta elegida
Delegated direct: el cambio es acotado a configuración, utilidad temporal, servicio IA y pruebas funcionales ordinarias.
