-- CU22: catálogo compartido y permiso de acceso.
-- Ejecutar una sola vez desde el SQL Editor de Supabase tras revisar el archivo.
-- No crea tablas, no cambia FKs y no elimina el catálogo plural.
-- Copia el catálogo CU21 conservando IDs; aborta si un ID ya tiene otros datos.

BEGIN;

LOCK TABLE public.servicios_oftalmologicos IN SHARE MODE;
LOCK TABLE public.servicio_oftalmologico IN SHARE ROW EXCLUSIVE MODE;

DO $cu22$
DECLARE
    v_modulo_id bigint;
    v_funcion_id bigint;
    v_accion_id bigint;
BEGIN
    SELECT modulo_id INTO v_modulo_id
    FROM public.funcion
    WHERE nombre = 'Registrar consulta clínica' AND estado = true;
    SELECT id INTO v_accion_id
    FROM public.accion WHERE upper(trim(nombre)) = 'AMBAS' AND estado = true;
    IF v_modulo_id IS NULL OR v_accion_id IS NULL THEN
        RAISE EXCEPTION 'Falta el módulo clínico o la acción AMBAS activa';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.rol
        WHERE translate(lower(trim(nombre)), 'áéíóú', 'aeiou') = 'oftalmologo'
          AND estado = true
    ) THEN
        RAISE EXCEPTION 'Falta un rol Oftalmólogo activo';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM public.servicios_oftalmologicos antiguo
        JOIN public.servicio_oftalmologico actual ON actual.id = antiguo.id
        WHERE actual.nombre IS DISTINCT FROM antiguo.nombre
           OR actual.descripcion IS DISTINCT FROM antiguo.descripcion
           OR actual.precio IS DISTINCT FROM antiguo.precio_base::numeric(10,2)
           OR actual.duracion IS DISTINCT FROM antiguo.duracion_estimada
           OR actual.estado IS DISTINCT FROM antiguo.estado
    ) THEN
        RAISE EXCEPTION 'Hay IDs de servicio con datos diferentes; revisar antes de migrar';
    END IF;

    INSERT INTO public.servicio_oftalmologico
        (id, nombre, descripcion, precio, duracion, estado)
        OVERRIDING SYSTEM VALUE
    SELECT id, nombre, descripcion, precio_base::numeric(10,2), duracion_estimada, estado
    FROM public.servicios_oftalmologicos
    ON CONFLICT (id) DO NOTHING;

    INSERT INTO public.funcion (modulo_id, nombre, estado)
    VALUES (v_modulo_id, 'Registrar servicios realizados', true)
    ON CONFLICT (modulo_id, nombre) DO UPDATE SET estado = true
    RETURNING id INTO v_funcion_id;

    -- AMBAS permite registrar y consultar CU22 al oftalmólogo.
    INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id)
    SELECT id, v_funcion_id, v_accion_id FROM public.rol
    WHERE translate(lower(trim(nombre)), 'áéíóú', 'aeiou') = 'oftalmologo'
      AND estado = true
    ON CONFLICT (rol_id, funcion_id) DO UPDATE SET accion_id = EXCLUDED.accion_id;

    -- La secuencia nunca retrocede. Se ajusta al final, después de validar todo.
    PERFORM setval(
        pg_get_serial_sequence('public.servicio_oftalmologico', 'id'),
        GREATEST(
            COALESCE((SELECT max(id) FROM public.servicio_oftalmologico), 1),
            COALESCE(pg_sequence_last_value(
                pg_get_serial_sequence('public.servicio_oftalmologico', 'id')::regclass
            ), 1)
        ),
        EXISTS (SELECT 1 FROM public.servicio_oftalmologico)
    );
END
$cu22$;

COMMIT;
