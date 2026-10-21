-- Solo lectura. Debe ejecutarse antes de la primera migracion CU31 del tenant.
-- El migrador debe usar una transaccion READ ONLY y abortar ante cualquier error.
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_faltantes text;
    v_modulos integer;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre)
      INTO v_faltantes
      FROM (VALUES
          ('public.servicio_realizado'),
          ('public.servicios_oftalmologicos'),
          ('public.modulo'),
          ('public.funcion'),
          ('public.accion'),
          ('public.rol'),
          ('public.rol_funcion')
      ) AS x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;

    IF v_faltantes IS NOT NULL THEN
        RAISE EXCEPTION 'Preflight CU31: faltan prerrequisitos: %', v_faltantes;
    END IF;

    -- Las migraciones originales no son idempotentes. Una aparicion parcial
    -- sin asiento en el ledger debe investigarse, nunca sobrescribirse.
    IF to_regclass('public.pago') IS NOT NULL
       OR to_regclass('public.pago_detalle') IS NOT NULL
       OR to_regclass('public.notificacion_pago') IS NOT NULL THEN
        RAISE EXCEPTION
            'Preflight CU31: existen objetos destino sin migracion confirmada; requiere reconciliacion manual';
    END IF;

    SELECT count(*) INTO v_modulos
      FROM public.modulo
     WHERE lower(trim(nombre)) = 'pagos' AND estado IS TRUE;
    IF v_modulos <> 1 THEN
        RAISE EXCEPTION
            'Preflight CU31: se esperaba exactamente un modulo Pagos activo; encontrados: %',
            v_modulos;
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
        RAISE EXCEPTION 'Preflight CU31: faltan restricciones unicas RBAC canonicas';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'public.servicio_realizado'::regclass
           AND attname = 'id' AND atttypid = 'bigint'::regtype
           AND attnotnull AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION
            'Preflight CU31: servicio_realizado.id no cumple BIGINT NOT NULL';
    END IF;
END $$;

SELECT current_database() AS base,
       current_user AS rol_migracion,
       current_setting('transaction_read_only') AS solo_lectura,
       (SELECT count(*) FROM information_schema.tables
         WHERE table_schema = 'public' AND table_type = 'BASE TABLE') AS tablas_public,
       (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public') AS indices_public,
       (SELECT count(*) FROM public.servicios_oftalmologicos) AS filas_catalogo_historico;

SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name = 'servicios_oftalmologicos'
 ORDER BY ordinal_position;

ROLLBACK;
