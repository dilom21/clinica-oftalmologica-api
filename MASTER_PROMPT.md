# MASTER_PROMPT.md
# Clínica Oftalmológica — Backend

Repositorio oficial:
https://github.com/dilom21/clinica-oftalmologica-api.git

## Flujo obligatorio
Antes de modificar:
1. leer MASTER_PROMPT.md;
2. leer CONTEXTO_CUXX_BACK.md;
3. revisar git status;
4. inspeccionar el código LOCAL y cambios no commiteados;
5. preservar trabajo previo;
6. recién implementar.

No asumir que GitHub refleja exactamente el estado local.

## Arquitectura
Respetar:
Router/API → Service → Repository → SQLAlchemy → PostgreSQL

Pydantic para entrada/salida.

No poner SQL/lógica compleja en router.
No duplicar helpers existentes.
No conectar directamente a Supabase si el proyecto usa SQLAlchemy.

## Proyecto colaborativo
Otros integrantes desarrollan otros CU en paralelo.

Por eso:
- minimizar cambios en archivos compartidos;
- no refactorizar código ajeno sin necesidad;
- no renombrar/reorganizar por gusto;
- aislar lógica del CU cuando sea práctico;
- mantener compatibilidad.

## Seguridad
Reutilizar:
- JWT
- obtener_usuario_actual
- requerir_permiso
- rol_funcion
- funcion
- accion
- helpers de rol existentes

Backend es la fuente de verdad.

## Base de datos
No modificar estructura salvo autorización explícita.

No:
- CREATE/ALTER/DROP
- migraciones
- cambiar RLS
- modificar .env
- insertar fixtures permanentes en la BD compartida

SELECT de smoke sí está permitido.

## Cambios locales
Nunca descartar trabajo con reset --hard, restore indiscriminado o limpieza destructiva.

Preservar untracked y cambios de otros CU.

## Pruebas
Todo CU debe terminar verificado:
- tests existentes y nuevos
- compileall
- import app.main
- OpenAPI
- smoke controlado si corresponde

No afirmar éxito sin ejecutar verificaciones.

## HTTP
Usar:
- 400/422 validación
- 401 autenticación
- 403 autorización
- 404 inexistente
- 409 conflicto de negocio

## Bitácora
Si la operación modifica datos y existe infraestructura de bitácora, reutilizarla.
No crear una segunda bitácora.

## Git
Durante implementación:
NO commit, push, merge, rebase ni force.

Al terminar:
- git status
- lista de archivos
- esperar instrucciones.

## ARCHIVOS IA — NUNCA VERSIONAR
Los siguientes archivos son exclusivamente locales:
- MASTER_PROMPT.md
- CONTEXTO_CU*_BACK.md
- CONTEXTO_CU*_FRONT.md
- CONTEXTO_CU*_MOBILE.md
- prompts/contextos equivalentes

No borrarlos, pero:
- no git add;
- no commit;
- no subir a dev-josias;
- no subir a pruebas;
- no subir a main.

Si aparecen staged, retirarlos del staging sin eliminar el archivo local.

## Reporte final
Informar:
1. archivos modificados;
2. funcionalidad;
3. endpoints;
4. reglas de negocio;
5. tests;
6. resultado;
7. pendientes;
8. git status;
9. confirmación de no commit/push/merge;
10. confirmación de que CONTEXTO/MASTER_PROMPT no fueron staged.
