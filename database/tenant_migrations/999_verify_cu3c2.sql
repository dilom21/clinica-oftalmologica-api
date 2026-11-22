-- Verificacion de Fase 3C.2 (solo lectura). Se ejecuta despues de aplicar la
-- migracion 20261121000000 y debe fallar cerrado ante cualquier discrepancia.
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_tab text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY[
        'dispositivo_push','notificacion','notificacion_entrega',
        'preferencia_notificacion','cita_evento'
    ] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'Verificacion Fase 3C.2: falta %', v_tab;
        END IF;
        -- RLS desactivado por instruccion del propietario.
        IF (SELECT relrowsecurity FROM pg_class
             WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'Verificacion Fase 3C.2: RLS habilitado en %', v_tab;
        END IF;
    END LOOP;

    -- Token activo unico por tenant (indice parcial).
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='uq_dispositivo_push_token_activo'
           AND indexdef LIKE '%UNIQUE%'
           AND indexdef LIKE '%WHERE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta unicidad parcial del token activo';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_dispositivo_push_usuario_activo'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta indice usuario/activo';
    END IF;

    -- Bandeja.
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='uq_notificacion_evento_clave'
           AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta unicidad de evento_clave';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_notificacion_usuario_fecha'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_notificacion_usuario_leida'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: faltan indices de bandeja';
    END IF;

    -- Outbox.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_notificacion_entrega_notificacion_dispositivo'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta unicidad notificacion/dispositivo';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_notificacion_entrega_cola'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta indice de cola';
    END IF;

    -- Preferencias.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_preferencia_notificacion_usuario'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta unicidad de preferencia por usuario';
    END IF;

    -- Eventos de cita.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_cita_evento_clave_idempotencia'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: falta unicidad de idempotencia';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname='ck_cita_evento_reprogramacion'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname='ck_cita_evento_cambio_estado'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: faltan restricciones de eventos';
    END IF;

    -- El token FCM no puede degradarse a un tipo sin longitud acotada.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='dispositivo_push'
           AND column_name='token_fcm'
           AND data_type='character varying'
           AND character_maximum_length=1024
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: token_fcm no es VARCHAR(1024)';
    END IF;

    -- Ninguna FK hacia otra base: solo usuario/cita del mismo tenant.
    IF EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid=c.conrelid
          JOIN pg_namespace n ON n.oid=t.relnamespace
         WHERE n.nspname='public'
           AND t.relname IN ('dispositivo_push','notificacion',
                             'notificacion_entrega','preferencia_notificacion',
                             'cita_evento')
           AND c.contype='f'
           AND confrelid NOT IN (
               to_regclass('public.usuario'), to_regclass('public.cita'),
               to_regclass('public.dispositivo_push'),
               to_regclass('public.notificacion')
           )
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.2: FK inesperada entre bases';
    END IF;
END $$;

SELECT table_name, count(*) AS columnas
  FROM information_schema.columns
 WHERE table_schema='public'
   AND table_name IN (
       'dispositivo_push','notificacion','notificacion_entrega',
       'preferencia_notificacion','cita_evento'
   )
 GROUP BY table_name
 ORDER BY table_name;

ROLLBACK;
