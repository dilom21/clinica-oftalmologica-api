-- =====================================================================
-- CU22 - PASO EXPLICITO PARA INSTALACIONES CON CATALOGO PLURAL LEGACY
--
-- Ejecutar solo si public.servicios_oftalmologicos existe. Copia hacia la
-- tabla canonica singular conservando IDs. No elimina, renombra ni vacia la
-- tabla antigua. Aborta ante cualquier colision con datos diferentes.
-- Despues de verificar este paso, ejecutar db/cu22_servicios_realizados.sql.
-- =====================================================================

BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

DO $legacy_pre$
DECLARE
    v_faltantes text;
BEGIN
    IF to_regclass('public.servicios_oftalmologicos') IS NULL THEN
        RAISE EXCEPTION 'CU22 legacy: public.servicios_oftalmologicos no existe; este paso no aplica';
    END IF;
    IF to_regclass('public.servicio_oftalmologico') IS NULL THEN
        RAISE EXCEPTION 'CU22 legacy: falta la tabla canonica public.servicio_oftalmologico';
    END IF;
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_faltantes
      FROM unnest(ARRAY[
          'id','nombre','descripcion','precio_base','duracion_estimada','estado'
      ]) AS x(nombre)
     WHERE NOT EXISTS (
        SELECT 1 FROM information_schema.columns c
         WHERE c.table_schema='public'
           AND c.table_name='servicios_oftalmologicos'
           AND c.column_name=x.nombre
     );
    IF v_faltantes IS NOT NULL THEN
        RAISE EXCEPTION 'CU22 legacy: faltan columnas legacy: %', v_faltantes;
    END IF;
END
$legacy_pre$;

LOCK TABLE public.servicios_oftalmologicos IN SHARE MODE;
LOCK TABLE public.servicio_oftalmologico IN SHARE ROW EXCLUSIVE MODE;

DO $legacy_copy$
DECLARE
    v_sequence text;
    v_max_id bigint;
    v_last_value bigint;
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.servicios_oftalmologicos old
        JOIN public.servicio_oftalmologico current ON current.id=old.id
        WHERE current.nombre IS DISTINCT FROM old.nombre
           OR current.descripcion IS DISTINCT FROM old.descripcion
           OR current.precio IS DISTINCT FROM old.precio_base::numeric(10,2)
           OR current.duracion IS DISTINCT FROM old.duracion_estimada
           OR current.estado IS DISTINCT FROM old.estado
    ) THEN
        RAISE EXCEPTION 'CU22 legacy: existen IDs con datos distintos; requiere conciliacion manual';
    END IF;

    INSERT INTO public.servicio_oftalmologico
        (id, nombre, descripcion, precio, duracion, estado)
        OVERRIDING SYSTEM VALUE
    SELECT id, nombre, descripcion, precio_base::numeric(10,2),
           duracion_estimada, estado
      FROM public.servicios_oftalmologicos
    ON CONFLICT (id) DO NOTHING;

    IF EXISTS (
        SELECT 1 FROM public.servicios_oftalmologicos old
        WHERE NOT EXISTS (
            SELECT 1 FROM public.servicio_oftalmologico current
             WHERE current.id=old.id
               AND current.nombre IS NOT DISTINCT FROM old.nombre
               AND current.descripcion IS NOT DISTINCT FROM old.descripcion
               AND current.precio IS NOT DISTINCT FROM old.precio_base::numeric(10,2)
               AND current.duracion IS NOT DISTINCT FROM old.duracion_estimada
               AND current.estado IS NOT DISTINCT FROM old.estado
        )
    ) THEN
        RAISE EXCEPTION 'CU22 legacy: la copia no supero la postcondicion de equivalencia';
    END IF;

    v_sequence := pg_get_serial_sequence('public.servicio_oftalmologico', 'id');
    IF v_sequence IS NULL THEN
        RAISE EXCEPTION 'CU22 legacy: no se pudo resolver la secuencia/identity del ID canonico';
    END IF;
    SELECT max(id) INTO v_max_id FROM public.servicio_oftalmologico;
    EXECUTE format('SELECT last_value FROM %s', v_sequence) INTO v_last_value;
    IF v_max_id IS NOT NULL THEN
        PERFORM setval(v_sequence::regclass, GREATEST(v_max_id, v_last_value), TRUE);
    END IF;
END
$legacy_copy$;

COMMIT;

-- Verificacion: ambos conteos deben coincidir para el subconjunto legacy.
SELECT
    (SELECT count(*) FROM public.servicios_oftalmologicos) AS filas_legacy,
    (SELECT count(*) FROM public.servicios_oftalmologicos old
      WHERE EXISTS (
        SELECT 1 FROM public.servicio_oftalmologico current
         WHERE current.id=old.id
           AND current.nombre IS NOT DISTINCT FROM old.nombre
           AND current.descripcion IS NOT DISTINCT FROM old.descripcion
           AND current.precio IS NOT DISTINCT FROM old.precio_base::numeric(10,2)
           AND current.duracion IS NOT DISTINCT FROM old.duracion_estimada
           AND current.estado IS NOT DISTINCT FROM old.estado
      )) AS filas_legacy_equivalentes;
