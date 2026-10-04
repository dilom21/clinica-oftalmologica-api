-- CU22: conservar el importe aplicado, independiente del catálogo.
-- El registro histórico existente conserva NULL: su precio aplicado es desconocido.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '20s';

ALTER TABLE public.servicio_realizado
    ADD COLUMN IF NOT EXISTS precio_aplicado numeric(10,2);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'servicio_realizado'
          AND column_name = 'precio_aplicado'
          AND data_type = 'numeric' AND numeric_precision = 10 AND numeric_scale = 2
          AND is_nullable = 'YES'
    ) THEN
        RAISE EXCEPTION 'precio_aplicado debe ser numeric(10,2) nullable; revisar el esquema';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.servicio_realizado'::regclass
          AND conname = 'servicio_realizado_precio_aplicado_valido'
    ) THEN
        ALTER TABLE public.servicio_realizado
            ADD CONSTRAINT servicio_realizado_precio_aplicado_valido
            CHECK (precio_aplicado IS NULL OR
                   (precio_aplicado >= 0 AND precio_aplicado <> 'NaN'::numeric));
    END IF;
END $$;

COMMENT ON COLUMN public.servicio_realizado.precio_aplicado IS
    'Importe aplicado en Bs al realizar el servicio. NULL solo en registros históricos sin importe conocido.';

COMMIT;
