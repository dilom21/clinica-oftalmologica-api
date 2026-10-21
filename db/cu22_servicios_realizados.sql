-- =====================================================================
-- CU22 - REGISTRAR SERVICIOS REALIZADOS (esquema canonico actual)
--
-- Este script NO migra automaticamente el catalogo plural historico
-- public.servicios_oftalmologicos. Si detecta filas antiguas que aun no
-- existen de forma equivalente en public.servicio_oftalmologico, aborta y
-- exige ejecutar primero db/cu22_migrar_catalogo_legacy.sql.
--
-- No elimina tablas ni datos y no modifica importes historicos. Los registros
-- previos conservan precio_aplicado = NULL cuando el importe es desconocido.
-- =====================================================================

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- 1) Precondiciones y contrato del esquema canonico --------------------
DO $cu22_pre$
DECLARE
    v_tabla text;
    v_referencia text;
    v_faltantes text;
    v_pendientes bigint := 0;
BEGIN
    FOREACH v_tabla IN ARRAY ARRAY[
        'servicio_oftalmologico', 'servicio_realizado',
        'paciente', 'oftalmologo', 'funcion', 'rol', 'accion', 'rol_funcion'
    ] LOOP
        IF to_regclass('public.' || v_tabla) IS NULL THEN
            RAISE EXCEPTION 'CU22: falta la tabla requerida public.%', v_tabla;
        END IF;
    END LOOP;

    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_faltantes
      FROM unnest(ARRAY['id','nombre','descripcion','precio','duracion','estado'])
           AS x(nombre)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name = 'servicio_oftalmologico'
           AND c.column_name = x.nombre
     );
    IF v_faltantes IS NOT NULL THEN
        RAISE EXCEPTION 'CU22: servicio_oftalmologico carece de columnas: %', v_faltantes;
    END IF;

    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_faltantes
      FROM unnest(ARRAY[
          'id','servicio_id','paciente_id','consulta_clinica_id',
          'oftalmologo_id','fecha_realizacion','observaciones','estado'
      ]) AS x(nombre)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema = 'public'
           AND c.table_name = 'servicio_realizado'
           AND c.column_name = x.nombre
     );
    IF v_faltantes IS NOT NULL THEN
        RAISE EXCEPTION 'CU22: servicio_realizado carece de columnas: %', v_faltantes;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='servicio_oftalmologico'
           AND column_name='id' AND data_type='bigint' AND is_nullable='NO'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='servicio_oftalmologico'
           AND column_name='nombre' AND data_type='character varying'
           AND character_maximum_length=150 AND is_nullable='NO'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='servicio_oftalmologico'
           AND column_name='precio' AND data_type='numeric'
           AND numeric_precision=10 AND numeric_scale=2
    ) THEN
        RAISE EXCEPTION 'CU22: servicio_oftalmologico no coincide con el contrato ORM canonico';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='servicio_realizado'
           AND column_name IN ('id','servicio_id','paciente_id','oftalmologo_id')
           AND (data_type <> 'bigint' OR is_nullable <> 'NO')
    ) THEN
        RAISE EXCEPTION 'CU22: IDs obligatorios de servicio_realizado no son bigint NOT NULL';
    END IF;

    FOREACH v_referencia IN ARRAY ARRAY[
        'servicio_oftalmologico', 'paciente', 'oftalmologo'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'public.servicio_realizado'::regclass
               AND contype = 'f'
               AND confrelid = to_regclass('public.' || v_referencia)
        ) THEN
            RAISE EXCEPTION 'CU22: falta FK servicio_realizado -> %', v_referencia;
        END IF;
    END LOOP;

    IF to_regclass('public.consulta_clinica') IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.servicio_realizado'::regclass
           AND contype = 'f'
           AND confrelid = 'public.consulta_clinica'::regclass
    ) THEN
        RAISE EXCEPTION 'CU22: consulta_clinica existe pero falta su FK desde servicio_realizado';
    END IF;

    -- La referencia plural es opcional. Si existe, nunca se ignoran sus filas.
    IF to_regclass('public.servicios_oftalmologicos') IS NOT NULL THEN
        SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_faltantes
          FROM unnest(ARRAY[
              'id','nombre','descripcion','precio_base','duracion_estimada','estado'
          ]) AS x(nombre)
         WHERE NOT EXISTS (
            SELECT 1 FROM information_schema.columns c
             WHERE c.table_schema = 'public'
               AND c.table_name = 'servicios_oftalmologicos'
               AND c.column_name = x.nombre
         );
        IF v_faltantes IS NOT NULL THEN
            RAISE EXCEPTION 'CU22: catalogo plural legacy incompatible; faltan: %', v_faltantes;
        END IF;

        EXECUTE $sql$
            SELECT count(*)
              FROM public.servicios_oftalmologicos old
             WHERE NOT EXISTS (
                SELECT 1 FROM public.servicio_oftalmologico current
                 WHERE current.id = old.id
                   AND current.nombre IS NOT DISTINCT FROM old.nombre
                   AND current.descripcion IS NOT DISTINCT FROM old.descripcion
                   AND current.precio IS NOT DISTINCT FROM old.precio_base::numeric(10,2)
                   AND current.duracion IS NOT DISTINCT FROM old.duracion_estimada
                   AND current.estado IS NOT DISTINCT FROM old.estado
             )
        $sql$ INTO v_pendientes;
        IF v_pendientes > 0 THEN
            RAISE EXCEPTION
                'CU22: existen % servicio(s) legacy sin migrar; ejecutar primero db/cu22_migrar_catalogo_legacy.sql',
                v_pendientes;
        END IF;
    END IF;
END
$cu22_pre$;

LOCK TABLE public.servicio_realizado IN SHARE ROW EXCLUSIVE MODE;

-- 2) Snapshot nullable del precio aplicado -----------------------------
ALTER TABLE public.servicio_realizado
    ADD COLUMN IF NOT EXISTS precio_aplicado numeric(10,2);

DO $cu22_price$
DECLARE
    v_def text;
    v_validated boolean;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'servicio_realizado'
           AND column_name = 'precio_aplicado'
           AND data_type = 'numeric'
           AND numeric_precision = 10
           AND numeric_scale = 2
           AND is_nullable = 'YES'
    ) THEN
        RAISE EXCEPTION 'CU22: precio_aplicado debe ser numeric(10,2) nullable';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.servicio_realizado
         WHERE precio_aplicado IS NOT NULL
           AND (precio_aplicado < 0 OR precio_aplicado = 'NaN'::numeric)
    ) THEN
        RAISE EXCEPTION 'CU22: hay precios aplicados historicos invalidos; no se corrigen automaticamente';
    END IF;

    SELECT pg_get_constraintdef(oid), convalidated
      INTO v_def, v_validated
      FROM pg_constraint
     WHERE conrelid = 'public.servicio_realizado'::regclass
       AND conname = 'servicio_realizado_precio_aplicado_valido';
    IF v_def IS NULL THEN
        ALTER TABLE public.servicio_realizado
            ADD CONSTRAINT servicio_realizado_precio_aplicado_valido
            CHECK (
                precio_aplicado IS NULL OR
                (precio_aplicado >= 0 AND precio_aplicado <> 'NaN'::numeric)
            );
    ELSIF v_validated IS NOT TRUE
       OR v_def NOT ILIKE '%precio_aplicado >=%'
       OR v_def NOT ILIKE '%precio_aplicado <>%'
       OR v_def NOT ILIKE '%NaN%' THEN
        RAISE EXCEPTION 'CU22: la restriccion precio_aplicado existente no cumple el contrato canonico';
    END IF;
END
$cu22_price$;

COMMENT ON COLUMN public.servicio_realizado.precio_aplicado IS
    'Importe aplicado al realizar el servicio; NULL en historicos cuyo importe no puede probarse.';

-- 3) Compatibilidad aditiva con pagos, si CU de pagos ya esta instalado --
DO $cu22_pagos$
BEGIN
    IF to_regclass('public.pago_detalle') IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'public.pago_detalle'::regclass
           AND contype = 'f'
           AND confrelid = 'public.servicio_realizado'::regclass
    ) THEN
        RAISE EXCEPTION 'CU22: pago_detalle existe pero no referencia servicio_realizado';
    END IF;
END
$cu22_pagos$;

-- 4) RBAC: misma funcion clinica, Oftalmologo con accion AMBAS ----------
DO $cu22_rbac$
DECLARE
    v_modulo_id bigint;
    v_funcion_id bigint;
    v_rol_id bigint;
    v_accion_id bigint;
BEGIN
    SELECT modulo_id INTO v_modulo_id
      FROM public.funcion
     WHERE nombre IN ('Registrar consulta clínica', 'Consultar historial clínico')
       AND estado IS TRUE
     ORDER BY CASE nombre WHEN 'Registrar consulta clínica' THEN 1 ELSE 2 END, id
     LIMIT 1;
    SELECT id INTO v_rol_id FROM public.rol
     WHERE translate(lower(trim(nombre)), 'áéíóú', 'aeiou') = 'oftalmologo'
       AND estado IS TRUE ORDER BY id LIMIT 1;
    SELECT id INTO v_accion_id FROM public.accion
     WHERE upper(trim(nombre)) = 'AMBAS' AND estado IS TRUE
     ORDER BY id LIMIT 1;
    IF v_modulo_id IS NULL OR v_rol_id IS NULL OR v_accion_id IS NULL THEN
        RAISE EXCEPTION 'CU22: faltan modulo clinico, rol Oftalmologo o accion AMBAS activos';
    END IF;

    SELECT id INTO v_funcion_id FROM public.funcion
     WHERE modulo_id = v_modulo_id
       AND nombre = 'Registrar servicios realizados'
     ORDER BY id LIMIT 1;
    IF v_funcion_id IS NULL THEN
        INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
        VALUES (
            v_modulo_id, 'Registrar servicios realizados',
            'Permite registrar y consultar servicios oftalmológicos realizados.', TRUE
        ) RETURNING id INTO v_funcion_id;
    ELSE
        UPDATE public.funcion SET estado = TRUE WHERE id = v_funcion_id;
    END IF;

    INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id, descripcion)
    VALUES (v_rol_id, v_funcion_id, v_accion_id, 'CU22: Oftalmólogo AMBAS')
    ON CONFLICT (rol_id, funcion_id) DO UPDATE
       SET accion_id = EXCLUDED.accion_id,
           descripcion = EXCLUDED.descripcion;
END
$cu22_rbac$;

-- 5) Postcondiciones ----------------------------------------------------
DO $cu22_post$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='servicio_realizado'
           AND column_name='precio_aplicado' AND data_type='numeric'
           AND numeric_precision=10 AND numeric_scale=2 AND is_nullable='YES'
    ) THEN
        RAISE EXCEPTION 'CU22: fallo la postcondicion de precio_aplicado';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.rol_funcion rf
        JOIN public.funcion f ON f.id = rf.funcion_id
        JOIN public.rol r ON r.id = rf.rol_id
        JOIN public.accion a ON a.id = rf.accion_id
        WHERE f.nombre = 'Registrar servicios realizados'
          AND translate(lower(trim(r.nombre)), 'áéíóú', 'aeiou') = 'oftalmologo'
          AND upper(trim(a.nombre)) = 'AMBAS'
    ) THEN
        RAISE EXCEPTION 'CU22: fallo la postcondicion RBAC';
    END IF;
END
$cu22_post$;

COMMIT;

-- Verificacion de solo lectura posterior a la ejecucion:
SELECT column_name, data_type, numeric_precision, numeric_scale, is_nullable
FROM information_schema.columns
WHERE table_schema='public' AND table_name='servicio_realizado'
  AND column_name='precio_aplicado';

SELECT r.nombre AS rol, f.nombre AS funcion, a.nombre AS accion
FROM public.rol_funcion rf
JOIN public.rol r ON r.id=rf.rol_id
JOIN public.funcion f ON f.id=rf.funcion_id
JOIN public.accion a ON a.id=rf.accion_id
WHERE f.nombre='Registrar servicios realizados';
