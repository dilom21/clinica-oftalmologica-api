# Reportes por voz + IA — Paso 6A

## Objetivo
Implementar `POST /ia/reportes/interpretar` para convertir texto ya transcrito en una configuración segura de reportes, sin ejecutar consultas, SQL ni exportaciones.

## Problema y motivo
El frontend necesita una interpretación controlada de órdenes de reporte en lenguaje natural mediante el provider DeepSeek existente, reutilizando el registry real y manteniendo los límites administrativos y de privacidad.

## Alcance autorizado
- Backend únicamente en este repositorio.
- Reutilizar `app/modules/integracion_ia/`, `app/modules/gestion_reportes/` y sus patrones existentes.
- Crear schemas, servicio/router, tests y documentación mínima necesaria.
- No tocar Angular, audio, speech-to-text, Supabase, migraciones, SQL generado por IA, ejecución/exportación, commit, push ni merge.

## Restricciones
- JWT válido, rol Administrador, permiso `Generar reportes` con `ACCION_LECTURA`.
- Request estricto: `texto` trim, no vacío, máximo 1000, `extra="forbid"`.
- Catálogo del prompt generado desde registry; sin filas ni datos clínicos.
- Flujo obligatorio `json.loads` → Pydantic → validación contra registry.
- Auditoría exitosa como `INTERPRETAR_REPORTE_IA`, entidad `reporte`, sin texto, prompt, filtros, respuesta IA ni datos clínicos.

## Tareas
- [x] ODD-6A-01 Implementar endpoint, schemas, catálogo seguro, provider reutilizado, doble validación, autorización y bitácora.
- [x] ODD-6A-02 Crear tests mockeados para éxito, validaciones, seguridad, provider, privacidad y auditoría.
- [x] ODD-6A-03 Ejecutar tests específicos y suite completa; corregir únicamente regresiones del alcance.
- [x] ODD-6A-04 Reproducir con DeepSeek real el comando de pacientes activos, corregir la compatibilidad contractual de la respuesta de forma segura y agregar regresiones.

## Criterios de aceptación
- La respuesta tiene configuración estable con dataset, columnas, filtros, orden, límite, aclaración y acción/formato sugeridos.
- Configuraciones inválidas del modelo nunca llegan como configuración confiable al cliente.
- No se ejecuta el motor de reportes ni se genera SQL desde este endpoint.
- Los tests solicitados pasan sin red y la suite completa queda verificada.

## Checks aplicables
- `.\.venv\Scripts\python.exe -m pytest tests/test_ia_reportes_voz.py -q`
- `.\.venv\Scripts\python.exe -m pytest -q`

## Ruta y progreso
- Ruta elegida por tarea: delegado directo, porque la implementación cruza router, schemas, servicio/provider, registry, seguridad, bitácora y tests.
- Evidencia de exploración: mapa local de `integracion_ia`, `gestion_reportes`, dependencias de seguridad y patrones de auditoría.
- TDD: no se encontró configuración TDD adicional; se ejecutaron los checks funcionales con pytest.
- Estrategia de entrega: `ask-on-risk`; el usuario explícitamente prohibió commit y esa restricción prevalece.
- Evidencia ODD-6A-01/02: `app/modules/integracion_ia/api/router.py`, `services/service.py`, `schemas/schemas.py` y `tests/test_ia_reportes_voz.py`.
- Evidencia ODD-6A-03: `13 passed` en tests del bloque y `433 passed` en suite completa; `git diff --check` sin errores.
- Corrección Paso 6A aplicada: normalización explícita y mínima de `listar` a `previsualizar` y `tabla` a `null`, antes de Pydantic; valores desconocidos y propiedades extra continúan siendo rechazados.
- Prompt reforzado para keys literales del catálogo, ausencia de labels/campos extra, booleanos JSON y combinaciones exactas de acción/formato.
- Regresiones agregadas para pacientes activos, variantes `listar`/`tabla`, propiedades adicionales y contrato del prompt.
- Evidencia de tests final: `19 passed` en el bloque y `440 passed` en la suite completa ejecutados con `.\.venv\Scripts\python.exe -m pytest tests/test_ia_reportes_voz.py -q` y `.\.venv\Scripts\python.exe -m pytest -q`.
- Ajuste final aplicado en `deepseek_provider.py`: `MAX_TOKENS_POR_DEFECTO = 2048`, manteniendo `response_format={"type": "json_object"}` y el parsing/validaciones existentes.
- Regresión agregada en `tests/test_integracion_ia_deepseek.py`: captura el request del cliente OpenAI mockeado y verifica `max_tokens == 2048` y `response_format == {"type": "json_object"}`, sin red.
- Prueba real final ejecutada con el texto exacto `Muéstrame los pacientes activos.` y validada mediante `service._validar_interpretacion_reporte`; no se expusieron secretos, headers, respuesta cruda ni datos reales. Salida estructural saneada: `{"accion_sugerida":"previsualizar","columnas":["id","nombres","apellidos","ci","sexo","fecha_nacimiento","telefono","fecha_registro","estado"],"dataset":"pacientes","filtros":[{"campo":"estado","operador":"eq","tipo_valor":"bool"}],"formato_sugerido":null,"keys":["accion_sugerida","columnas","dataset","filtros","formato_sugerido","limit","orden","pregunta_aclaracion","requiere_aclaracion"],"limit":200,"requiere_aclaracion":false}`.
