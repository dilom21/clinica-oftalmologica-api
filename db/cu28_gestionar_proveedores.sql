-- CU28 - Gestionar proveedores: permisos RBAC por tenant.
-- PREPARADA, NO EJECUTADA. Aplicar primero en la base principal y luego, de
-- forma controlada, en cada base tenant. El script es aditivo e idempotente.
--
-- PRERREQUISITO: la tabla public.proveedores ya existe en cada tenant (la crea
-- la migracion 20261024150000_cu26_cu27_cu29_inventario_compras, porque
-- compra.proveedor_id la referencia). Este script NO crea ni altera columnas.
--
-- Registro en database/tenant_migrations/manifest.json: como migracion
-- posterior a 20261024150000, con "transaction": "script_idempotent".
--
-- Efecto en el catalogo limpio del provisionador: funcion +1, rol_funcion +1.

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- 1) Precondiciones ----------------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.proveedores') IS NULL
       OR to_regclass('public.modulo') IS NULL
       OR to_regclass('public.funcion') IS NULL
       OR to_regclass('public.accion') IS NULL
       OR to_regclass('public.rol') IS NULL
       OR to_regclass('public.rol_funcion') IS NULL THEN
        RAISE EXCEPTION 'CU28: faltan public.proveedores o tablas RBAC canonicas';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.funcion'::regclass
           AND contype = 'u'
           AND pg_get_constraintdef(oid) = 'UNIQUE (modulo_id, nombre)'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.rol_funcion'::regclass
           AND contype = 'u'
           AND pg_get_constraintdef(oid) = 'UNIQUE (rol_id, funcion_id)'
    ) THEN
        RAISE EXCEPTION 'CU28: faltan restricciones unicas RBAC canonicas';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND tablename = 'proveedores'
           AND indexdef ILIKE 'CREATE UNIQUE INDEX % ON public.proveedores USING btree (nit)'
    ) THEN
        RAISE EXCEPTION 'CU28: proveedores.nit no tiene indice unico';
    END IF;
END $$;

-- 2) RBAC: reutiliza el modulo "Inventario y Proveedores". No hardcodea ids.
-- La asignacion inicial se siembra SOLO cuando la funcion se crea. Si CU05
-- revoca o cambia el permiso, una segunda ejecucion no lo restaura.
DO $$
DECLARE
    v_modulo_id bigint;
    v_encontrados integer;
    v_funcion_id bigint;
    v_funcion_nueva boolean := FALSE;
    v_rol_admin bigint;
    v_accion_ambas bigint;
BEGIN
    SELECT count(*), min(id)
      INTO v_encontrados, v_modulo_id
      FROM public.modulo
     WHERE lower(trim(nombre)) = 'inventario y proveedores';

    IF v_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU28: se esperaba exactamente un modulo Inventario y Proveedores; encontrados: %',
            v_encontrados;
    END IF;

    SELECT count(*), min(id)
      INTO v_encontrados, v_rol_admin
      FROM public.rol
     WHERE lower(trim(nombre)) IN ('administrador', 'admin')
       AND estado IS TRUE;

    IF v_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU28: se esperaba un rol Administrador activo; encontrados: %',
            v_encontrados;
    END IF;

    SELECT count(*), min(id)
      INTO v_encontrados, v_accion_ambas
      FROM public.accion
     WHERE upper(trim(nombre)) = 'AMBAS' AND estado IS TRUE;

    IF v_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU28: se esperaba una accion AMBAS activa; encontradas: %',
            v_encontrados;
    END IF;

    INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
    VALUES (
        v_modulo_id,
        'Gestionar proveedores',
        'Registrar, consultar, actualizar y dar de baja proveedores',
        TRUE
    )
    ON CONFLICT (modulo_id, nombre) DO NOTHING
    RETURNING id INTO v_funcion_id;

    IF v_funcion_id IS NOT NULL THEN
        v_funcion_nueva := TRUE;
    ELSE
        SELECT id INTO v_funcion_id
          FROM public.funcion
         WHERE modulo_id = v_modulo_id AND nombre = 'Gestionar proveedores';
    END IF;

    IF v_funcion_id IS NULL THEN
        RAISE EXCEPTION 'CU28: no se pudo resolver la funcion Gestionar proveedores';
    END IF;

    IF v_funcion_nueva THEN
        INSERT INTO public.rol_funcion
            (rol_id, funcion_id, accion_id, descripcion)
        VALUES
            (v_rol_admin, v_funcion_id, v_accion_ambas,
             'Asignacion inicial CU28: Administrador gestiona proveedores')
        ON CONFLICT (rol_id, funcion_id) DO NOTHING;

        IF (
            SELECT count(*) FROM public.rol_funcion
             WHERE funcion_id = v_funcion_id AND rol_id = v_rol_admin
        ) <> 1 THEN
            RAISE EXCEPTION 'CU28: no se completo la asignacion de Gestionar proveedores';
        END IF;
    END IF;
END $$;

-- 3) Endurecimiento: proveedores solo se expone a traves de FastAPI. Igual que
-- los tenants ya migrados, RLS queda habilitado sin politicas para clientes.
ALTER TABLE public.proveedores ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.proveedores FROM PUBLIC;

DO $$
DECLARE
    v_rol text;
BEGIN
    FOR v_rol IN
        SELECT rolname FROM pg_roles
         WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON TABLE public.proveedores FROM %I', v_rol);
        EXECUTE format('REVOKE ALL ON SEQUENCE public.proveedores_id_seq FROM %I', v_rol);
    END LOOP;
END $$;

-- 4) Postcondiciones ---------------------------------------------------
DO $$
BEGIN
    IF (
        SELECT count(*)
          FROM public.funcion f
          JOIN public.modulo m ON m.id = f.modulo_id
         WHERE lower(trim(m.nombre)) = 'inventario y proveedores'
           AND f.nombre = 'Gestionar proveedores'
           AND f.estado IS TRUE
    ) <> 1 THEN
        RAISE EXCEPTION 'CU28: la funcion Gestionar proveedores no quedo activa';
    END IF;

    IF NOT (
        SELECT relrowsecurity FROM pg_class
         WHERE oid = 'public.proveedores'::regclass
    ) THEN
        RAISE EXCEPTION 'CU28: RLS no quedo habilitado en public.proveedores';
    END IF;
END $$;

SELECT current_database() AS base_verificada,
       'cu28_gestionar_proveedores'::text AS migracion_aplicada;

COMMIT;
