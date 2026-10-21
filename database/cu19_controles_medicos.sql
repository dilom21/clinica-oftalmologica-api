-- CU19: extensión aditiva del esquema PostgreSQL existente.
-- control_medico ya existe: se conservan su PK, sus relaciones y sus datos.
-- Ejecutar el archivo completo como una sola transacción.
BEGIN;
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.control_medico
    ADD COLUMN IF NOT EXISTS observaciones TEXT;

DO $$
DECLARE
    modulo_clinico_id BIGINT;
    funcion_control_id BIGINT;
    accion_ambas_id BIGINT;
BEGIN
    -- Reutiliza el módulo que ya contiene la consulta del historial.
    SELECT modulo_id INTO STRICT modulo_clinico_id
    FROM public.funcion
    WHERE nombre = 'Consultar historial clínico';

    SELECT id INTO STRICT accion_ambas_id
    FROM public.accion
    WHERE upper(trim(nombre)) = 'AMBAS' AND estado = TRUE;

    IF NOT EXISTS (
        SELECT 1 FROM public.rol
        WHERE translate(lower(trim(nombre)), 'áéíóúü', 'aeiouu') = 'oftalmologo'
          AND estado = TRUE
    ) THEN
        RAISE EXCEPTION 'No existe un rol Oftalmólogo activo para CU19';
    END IF;

    INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
    VALUES (
        modulo_clinico_id,
        'Programar controles médicos',
        'CU19: programar y administrar controles de una atención clínica previa',
        TRUE
    )
    ON CONFLICT (modulo_id, nombre) DO NOTHING;

    SELECT id INTO STRICT funcion_control_id
    FROM public.funcion
    WHERE modulo_id = modulo_clinico_id
      AND nombre = 'Programar controles médicos';

    -- Agrega exclusivamente el permiso nuevo. Una asignación previa se respeta.
    INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id, descripcion)
    SELECT id, funcion_control_id, accion_ambas_id,
           'CU19: controles médicos de consultas del oftalmólogo responsable'
    FROM public.rol
    WHERE translate(lower(trim(nombre)), 'áéíóúü', 'aeiouu') = 'oftalmologo'
      AND estado = TRUE
    ON CONFLICT (rol_id, funcion_id) DO NOTHING;
END $$;

COMMIT;
