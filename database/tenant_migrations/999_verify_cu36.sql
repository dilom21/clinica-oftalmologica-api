BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_tab text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY['pago_ajuste','pago_ajuste_detalle'] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'Verificacion CU36: falta %', v_tab;
        END IF;
        -- CU36 mantiene RLS desactivado por instruccion del propietario.
        IF (SELECT relrowsecurity FROM pg_class
             WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'Verificacion CU36: RLS habilitado en %', v_tab;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_pago_ajuste_clave_idempotencia'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU36: falta unicidad de idempotencia';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_pago_ajuste_referencia_externa'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU36: falta unicidad de referencia externa';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_pago_ajuste_importe'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_pago_ajuste_efecto_por_tipo'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU36: faltan restricciones de importe o efecto';
    END IF;

    -- E-3: registro durable de webhooks huerfanos.
    IF to_regclass('public.stripe_webhook_huerfano') IS NULL THEN
        RAISE EXCEPTION 'Verificacion CU36: falta stripe_webhook_huerfano';
    END IF;
    IF (SELECT relrowsecurity FROM pg_class
         WHERE oid=to_regclass('public.stripe_webhook_huerfano')) THEN
        RAISE EXCEPTION 'Verificacion CU36: RLS habilitado en stripe_webhook_huerfano';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_stripe_webhook_huerfano_evento'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU36: falta unicidad del evento huerfano';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_stripe_webhook_huerfano_estado'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_stripe_webhook_huerfano_amount'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU36: faltan restricciones del evento huerfano';
    END IF;
END $$;

SELECT table_name, count(*) AS columnas
  FROM information_schema.columns
 WHERE table_schema='public'
   AND table_name IN (
       'pago_ajuste','pago_ajuste_detalle','stripe_webhook_huerfano'
   )
 GROUP BY table_name
 ORDER BY table_name;

ROLLBACK;
