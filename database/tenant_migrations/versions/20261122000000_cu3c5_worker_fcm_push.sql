-- Fase 3C.5: soporte de worker tenant-scoped y envio FCM HTTP v1.
--
-- Migracion ADITIVA sobre el outbox creado en 3C.2. No recrea tablas, no
-- modifica datos y no toca la migracion historica 20261121000000.
--
-- Cambios:
--   * notificacion_entrega.lease_token: identificador opaco del claim vigente.
--     Permite que un worker antiguo (lease expirado) no sobrescriba el
--     resultado de un worker nuevo. La expiracion sigue siendo
--     ``bloqueado_hasta``; ``lease_token`` es el fencing token.
--   * estado terminal ``OMITIDO``: la entrega se descarta sin enviar porque el
--     usuario desactivo push, la notificacion fue cancelada o el dispositivo
--     dejo de ser elegible. No es ``INVALIDO`` (token no registrado) ni
--     ``FALLIDO`` (fallo del proveedor).
--   * indice parcial de recuperacion de leases vencidos.
--
-- Transaccion controlada por tenant_migrator.py. No incluir BEGIN/COMMIT.
-- RLS permanece DESACTIVADO por la instruccion vigente del propietario.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE
    v_lease boolean;
    v_estado text;
BEGIN
    IF to_regclass('public.notificacion_entrega') IS NULL THEN
        RAISE EXCEPTION
            'Fase 3C.5: falta el outbox de 3C.2 (public.notificacion_entrega)';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'ck_notificacion_entrega_estado'
           AND conrelid = 'public.notificacion_entrega'::regclass
    ) THEN
        RAISE EXCEPTION
            'Fase 3C.5: falta ck_notificacion_entrega_estado de 3C.2';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'public.notificacion_entrega'::regclass
           AND attname = 'lease_token' AND attnum > 0 AND NOT attisdropped
    ) INTO v_lease;
    IF v_lease THEN
        RAISE EXCEPTION
            'Fase 3C.5: instalacion parcial: notificacion_entrega.lease_token ya existe';
    END IF;

    SELECT pg_get_constraintdef(oid) INTO v_estado
      FROM pg_constraint WHERE conname = 'ck_notificacion_entrega_estado';
    IF v_estado LIKE '%OMITIDO%' THEN
        RAISE EXCEPTION
            'Fase 3C.5: instalacion parcial: el estado OMITIDO ya esta admitido';
    END IF;
    IF v_estado NOT LIKE '%ACEPTADO_POR_FCM%' THEN
        RAISE EXCEPTION
            'Fase 3C.5: la restriccion de estado no corresponde a la de 3C.2';
    END IF;
END
$preflight$;

ALTER TABLE public.notificacion_entrega
    ADD COLUMN lease_token VARCHAR(64);

ALTER TABLE public.notificacion_entrega
    DROP CONSTRAINT ck_notificacion_entrega_estado;

ALTER TABLE public.notificacion_entrega
    ADD CONSTRAINT ck_notificacion_entrega_estado CHECK (
        estado IN ('PENDIENTE','PROCESANDO','ACEPTADO_POR_FCM','FALLIDO',
                   'INVALIDO','OMITIDO')
    );

CREATE INDEX ix_notificacion_entrega_lease
    ON public.notificacion_entrega (bloqueado_hasta)
    WHERE estado = 'PROCESANDO';

COMMENT ON COLUMN public.notificacion_entrega.lease_token IS
    'Fase 3C.5: fencing token del claim; nulo cuando la entrega no esta reclamada.';
COMMENT ON COLUMN public.notificacion_entrega.bloqueado_hasta IS
    'Fase 3C.5: expiracion del lease; principal para recuperar PROCESANDO.';

DO $postflight$
DECLARE
    v_tipo text;
    v_largo integer;
    v_estado text;
BEGIN
    SELECT data_type, character_maximum_length INTO v_tipo, v_largo
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'notificacion_entrega'
       AND column_name = 'lease_token';
    IF v_tipo IS DISTINCT FROM 'character varying' OR v_largo IS DISTINCT FROM 64 THEN
        RAISE EXCEPTION 'Fase 3C.5: lease_token no es VARCHAR(64)';
    END IF;

    SELECT pg_get_constraintdef(oid) INTO v_estado
      FROM pg_constraint WHERE conname = 'ck_notificacion_entrega_estado';
    IF v_estado NOT LIKE '%OMITIDO%' THEN
        RAISE EXCEPTION 'Fase 3C.5: el estado OMITIDO no quedo admitido';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname = 'ix_notificacion_entrega_lease'
    ) THEN
        RAISE EXCEPTION 'Fase 3C.5: falta ix_notificacion_entrega_lease';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname = 'public'
           AND indexname = 'ix_notificacion_entrega_cola'
    ) THEN
        RAISE EXCEPTION 'Fase 3C.5: falta el indice de cola de 3C.2';
    END IF;
END
$postflight$;
