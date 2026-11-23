# CONTEXTO_CU15_BACK.md

# CU15 - Registrar consulta clínica (Backend)

## Contexto del proyecto

Proyecto: Clínica Oftalmológica\
Backend: FastAPI + PostgreSQL/Supabase\
Arquitectura utilizada:

Router → Service → Repository → Models → PostgreSQL

Repositorios: - API: clinica-oftalmologica-api - Web:
clinica-oftalmologica-web - Mobile: clinica-oftalmologica-mobile

## Estado actual de la base de datos

Supabase ya contiene las tablas base:

-   usuario
-   rol
-   paciente
-   oftalmologo
-   cita
-   historial_clinico
-   antecedente_clinico

Para CU15 NO crear tablas duplicadas.

La tabla principal a utilizar:

historial_clinico

Relación existente:

Paciente 1 --- 1 Historial clínico

## Objetivo del CU15

Permitir registrar una consulta médica realizada por un oftalmólogo a un
paciente.

Una consulta clínica debe quedar asociada a:

-   historial clínico del paciente
-   cita médica (si existe)
-   oftalmólogo que realizó la atención

## Nueva entidad requerida

Tabla:

consulta_clinica

Campos esperados:

-   id
-   historial_clinico_id FK
-   cita_id FK nullable
-   oftalmologo_id FK
-   fecha_consulta
-   motivo_consulta
-   anamnesis
-   observaciones
-   estado

Relaciones:

historial_clinico 1 --- N consulta_clinica

cita 1 --- 1 consulta_clinica

oftalmologo 1 --- N consulta_clinica

## Reglas de negocio

-   Solo usuarios autenticados pueden registrar consultas.
-   El rol autorizado debe ser oftalmólogo.
-   El oftalmólogo debe registrar consultas propias.
-   No eliminar información clínica histórica.
-   Mantener bitácora siguiendo la infraestructura existente.
-   Validar existencia de paciente/historial/cita antes de registrar.

## Consideraciones

No modificar: - CU09 - CU11 - tablas existentes de agenda -
autenticación

Mantener la arquitectura y convenciones actuales del proyecto.
