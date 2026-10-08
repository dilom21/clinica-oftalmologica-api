# MASTER_PROMPT_CU15_BACK.md

# Implementación CU15 - Registrar consulta clínica

Actúa como desarrollador backend senior del proyecto Clínica
Oftalmológica.

Antes de modificar código:

1.  Lee:

-   CONTEXTO_CU15_BACK.md
-   estructura actual del repositorio
-   módulos existentes
-   modelos SQLAlchemy actuales
-   servicios y repositorios existentes

Respeta obligatoriamente la arquitectura:

Router → Service → Repository → Models

## Objetivo

Implementar completamente el CU15 Registrar consulta clínica.

## Debes crear/adaptar

Dentro del módulo correspondiente:

-   models.py
-   schemas.py
-   repository.py
-   service.py
-   router.py

siguiendo el mismo patrón utilizado en CU09 y CU11.

## Funcionalidad requerida

Implementar:

-   registrar consulta clínica
-   consultar consultas clínicas necesarias para futuras dependencias

La creación debe validar:

-   historial clínico existente
-   oftalmólogo válido
-   cita válida si llega informada

## Seguridad

Usar el sistema actual:

-   JWT existente
-   requerir_permiso()
-   roles definidos en BD

No crear un sistema paralelo de permisos.

## Base de datos

Usar la tabla:

consulta_clinica

No crear:

-   historial_clinico
-   paciente
-   oftalmologo
-   cita

porque ya existen.

## Calidad

Antes de finalizar:

Ejecutar:

-   compileall
-   pruebas unitarias
-   validar OpenAPI
-   verificar que CU anteriores continúan funcionando

No modificar:

-   .env
-   Supabase manualmente
-   migraciones ajenas
-   CU09/CU11

No realizar commit ni push.

Entregar reporte final indicando:

-   archivos modificados
-   endpoints creados
-   pruebas ejecutadas
-   pendientes encontrados.
