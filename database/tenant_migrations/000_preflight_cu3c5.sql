-- Preflight de Fase 3C.5 (solo lectura).
--
-- Se ejecuta antes de cualquier tenant, incluidos los que ya tienen 3C.2
-- aplicado. Por eso solo exige invariantes que se cumplen tanto antes como
-- despues de la migracion aditiva: tablas base presentes y ausencia de
-- instalacion parcial de lease/estado.
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_missing text;
    v_lease boolean;
    v_estado text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.usuario'), ('public.rol'), ('public.paciente'),
          ('public.cita'), ('public.bitacora'), ('public.modulo'),
          ('public.funcion'), ('public.accion'), ('public.rol_funcion')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'Preflight Fase 3C.5: faltan tablas: %', v_missing;
    END IF;

    -- Si el outbox existe, debe ser el de 3C.2 y no una instalacion parcial.
    IF to_regclass('public.notificacion_entrega') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'ck_notificacion_entrega_estado'
               AND conrelid = 'public.notificacion_entrega'::regclass
        ) THEN
            RAISE EXCEPTION
                'Preflight Fase 3C.5: outbox sin ck_notificacion_entrega_estado';
        END IF;

        SELECT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = 'public.notificacion_entrega'::regclass
               AND attname = 'lease_token' AND attnum > 0 AND NOT attisdropped
        ) INTO v_lease;

        SELECT pg_get_constraintdef(oid) INTO v_estado
          FROM pg_constraint WHERE conname = 'ck_notificacion_entrega_estado';

        -- Estado intermedio imposible: lease sin estado OMITIDO (o al reves).
        IF v_lease <> (v_estado LIKE '%OMITIDO%') THEN
            RAISE EXCEPTION
                'Preflight Fase 3C.5: instalacion parcial de lease_token/OMITIDO';
        END IF;
        IF v_estado NOT LIKE '%ACEPTADO_POR_FCM%' THEN
            RAISE EXCEPTION
                'Preflight Fase 3C.5: restriccion de estado ajena a 3C.2';
        END IF;
    END IF;

    -- Fase 3C.5.2: si el outbox de revocaciones ya existe, debe estar completo
    -- y solo puede existir sobre el esquema v8. Antes de aplicar v9 la tabla
    -- esta ausente: ese caso es valido.
    IF to_regclass('public.dispositivo_push_revocacion') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = 'public.dispositivo_push_revocacion'::regclass
               AND attname = 'lease_token' AND attnum > 0 AND NOT attisdropped
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'uq_dispositivo_push_revocacion_dispositivo_huella'
               AND conrelid = 'public.dispositivo_push_revocacion'::regclass
        ) OR NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'ck_dispositivo_push_revocacion_estado'
               AND conrelid = 'public.dispositivo_push_revocacion'::regclass
        ) THEN
            RAISE EXCEPTION
                'Preflight Fase 3C.5.2: instalacion parcial de dispositivo_push_revocacion';
        END IF;

        IF to_regclass('public.notificacion_entrega') IS NULL THEN
            RAISE EXCEPTION
                'Preflight Fase 3C.5.2: revocaciones sin outbox de entregas';
        END IF;
        SELECT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = 'public.notificacion_entrega'::regclass
               AND attname = 'lease_token' AND attnum > 0 AND NOT attisdropped
        ) INTO v_lease;
        SELECT pg_get_constraintdef(oid) INTO v_estado
          FROM pg_constraint
         WHERE conname = 'ck_notificacion_entrega_estado'
           AND conrelid = 'public.notificacion_entrega'::regclass;
        IF NOT v_lease OR v_estado IS NULL
           OR v_estado NOT LIKE '%OMITIDO%' THEN
            RAISE EXCEPTION
                'Preflight Fase 3C.5.2: revocaciones sin el esquema v8 de 3C.5';
        END IF;
    END IF;
END $$;

SELECT current_database() AS database_name,
       count(*) FILTER (WHERE table_name IN (
           'dispositivo_push','notificacion','notificacion_entrega',
           'preferencia_notificacion','cita_evento'
       )) AS tablas_notificaciones_push,
       count(*) FILTER (WHERE table_name IN (
           'pago','pago_detalle','notificacion_pago','cita','usuario'
       )) AS tablas_base_existentes
  FROM information_schema.tables
 WHERE table_schema='public';

ROLLBACK;
