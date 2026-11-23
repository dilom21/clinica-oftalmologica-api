-- CU26/CU27 tenant-local RBAC seed.
-- Execute only inside one tenant database after its control-plane preflight.
-- This script is deliberately fail-closed: it never changes an existing row.
BEGIN;

DO $seed$
DECLARE
    v_modulo_id BIGINT;
    v_accion_id BIGINT;
    v_rol_id BIGINT;
    v_funcion_id BIGINT;
    v_count INTEGER;
    v_nombre TEXT;
    v_funciones CONSTANT TEXT[] := ARRAY[
        'Gestionar productos, categorías y lotes',
        'Gestionar movimientos de inventario'
    ];
BEGIN
    SELECT count(*) INTO v_count FROM public.modulo
    WHERE nombre = 'Inventario y Proveedores';
    IF v_count > 1 THEN
        RAISE EXCEPTION 'Ambiguous modulo name: Inventario y Proveedores';
    ELSIF v_count = 0 THEN
        RAISE EXCEPTION 'Audited target modulo is missing: Inventario y Proveedores';
    ELSE
        SELECT id INTO v_modulo_id FROM public.modulo
        WHERE nombre = 'Inventario y Proveedores';
        IF NOT EXISTS (SELECT 1 FROM public.modulo WHERE id = v_modulo_id AND estado) THEN
            RAISE EXCEPTION 'Target modulo is inactive';
        END IF;
    END IF;

    SELECT count(*) INTO v_count FROM public.accion WHERE nombre = 'AMBAS';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one global active/action row named AMBAS, found %', v_count;
    END IF;
    SELECT id INTO v_accion_id FROM public.accion WHERE nombre = 'AMBAS' AND estado;
    IF v_accion_id IS NULL THEN
        RAISE EXCEPTION 'The unique AMBAS action is inactive';
    END IF;

    SELECT count(*) INTO v_count FROM public.rol WHERE nombre = 'Administrador';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'Expected exactly one global role named Administrador, found %', v_count;
    END IF;
    SELECT id INTO v_rol_id FROM public.rol WHERE nombre = 'Administrador' AND estado;
    IF v_rol_id IS NULL THEN
        RAISE EXCEPTION 'The unique Administrador role is inactive';
    END IF;

    FOREACH v_nombre IN ARRAY v_funciones LOOP
        IF EXISTS (
            SELECT 1 FROM public.funcion
            WHERE nombre = v_nombre AND modulo_id <> v_modulo_id
        ) THEN
            RAISE EXCEPTION 'Global function homonym exists in another module: %', v_nombre;
        END IF;
        SELECT count(*) INTO v_count FROM public.funcion
        WHERE modulo_id = v_modulo_id AND nombre = v_nombre;
        IF v_count > 1 THEN
            RAISE EXCEPTION 'Ambiguous function under target module: %', v_nombre;
        ELSIF v_count = 0 THEN
            INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
            VALUES (v_modulo_id, v_nombre, 'CU26/CU27', TRUE)
            RETURNING id INTO v_funcion_id;
            INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id, descripcion)
            VALUES (v_rol_id, v_funcion_id, v_accion_id, 'CU26/CU27 seed');
        ELSE
            SELECT id INTO v_funcion_id FROM public.funcion
            WHERE modulo_id = v_modulo_id AND nombre = v_nombre;
            IF NOT EXISTS (SELECT 1 FROM public.funcion WHERE id = v_funcion_id AND estado) THEN
                RAISE EXCEPTION 'Target function is inactive: %', v_nombre;
            END IF;
            SELECT count(*) INTO v_count FROM public.rol_funcion
            WHERE funcion_id = v_funcion_id;
            IF v_count = 0 THEN
                INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id, descripcion)
                VALUES (v_rol_id, v_funcion_id, v_accion_id, 'CU26/CU27 seed');
            ELSIF v_count <> 1 THEN
                RAISE EXCEPTION 'Existing target function has % assignments: %', v_count, v_nombre;
            ELSIF NOT EXISTS (
                SELECT 1 FROM public.rol_funcion rf
                JOIN public.accion a ON a.id = rf.accion_id
                WHERE rf.rol_id = v_rol_id AND rf.funcion_id = v_funcion_id
                  AND rf.accion_id = v_accion_id AND a.estado
            ) THEN
                RAISE EXCEPTION 'Existing target assignment is not active AMBAS for Administrator: %', v_nombre;
            END IF;
        END IF;
    END LOOP;
END
$seed$;

COMMIT;
