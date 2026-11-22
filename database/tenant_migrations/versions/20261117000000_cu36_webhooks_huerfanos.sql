-- CU36 (E-3): registro durable de webhooks Stripe huerfanos.
-- Un refund verificado por firma que no puede atribuirse a un ajuste/tenant se
-- guarda aqui antes de responder a Stripe. Solo se contabiliza mediante
-- conciliacion manual con refund_id explicito, evidencia y rol administrador.
-- Transaccion controlada por tenant_migrator.py. No incluir BEGIN/COMMIT.
-- RLS permanece DESACTIVADO por instruccion del propietario (CU36).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE
    v_missing text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.pago'), ('public.pago_ajuste'), ('public.usuario')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'CU36 E-3: faltan prerrequisitos: %', v_missing;
    END IF;
    IF to_regclass('public.stripe_webhook_huerfano') IS NOT NULL THEN
        RAISE EXCEPTION
            'CU36 E-3: stripe_webhook_huerfano ya existe sin migracion confirmada';
    END IF;
END
$preflight$;

CREATE TABLE public.stripe_webhook_huerfano (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stripe_event_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    refund_id VARCHAR(255) NOT NULL,
    payment_intent_id VARCHAR(255),
    amount BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL,
    status VARCHAR(30) NOT NULL,
    payload TEXT NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    ajuste_id BIGINT,
    motivo TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_stripe_webhook_huerfano_ajuste FOREIGN KEY (ajuste_id)
        REFERENCES public.pago_ajuste(id) ON DELETE RESTRICT,
    CONSTRAINT uq_stripe_webhook_huerfano_evento UNIQUE (stripe_event_id),
    CONSTRAINT ck_stripe_webhook_huerfano_amount CHECK (amount > 0),
    CONSTRAINT ck_stripe_webhook_huerfano_moneda CHECK (
        length(currency) = 3 AND currency = upper(currency)
    ),
    CONSTRAINT ck_stripe_webhook_huerfano_estado CHECK (
        estado IN ('PENDIENTE', 'ATRIBUIDO', 'DESCARTADO')
    )
);

CREATE INDEX ix_stripe_webhook_huerfano_refund
    ON public.stripe_webhook_huerfano (refund_id, estado);
CREATE INDEX ix_stripe_webhook_huerfano_pi
    ON public.stripe_webhook_huerfano (payment_intent_id);
CREATE INDEX ix_stripe_webhook_huerfano_pendientes
    ON public.stripe_webhook_huerfano (creado_en)
    WHERE estado = 'PENDIENTE';

COMMENT ON TABLE public.stripe_webhook_huerfano IS
    'CU36 E-3: refund Stripe verificado sin metadata, pendiente de conciliacion manual auditada.';
COMMENT ON COLUMN public.stripe_webhook_huerfano.payload IS
    'Evento Stripe original serializado; evidencia de auditoria.';
COMMENT ON COLUMN public.stripe_webhook_huerfano.estado IS
    'PENDIENTE hasta que un administrador lo atribuya con refund_id y evidencia.';

REVOKE ALL PRIVILEGES ON TABLE public.stripe_webhook_huerfano FROM PUBLIC;

DO $security$
DECLARE
    v_role text;
    v_seq text;
BEGIN
    FOR v_role IN SELECT rolname FROM pg_roles
                  WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.stripe_webhook_huerfano FROM %I',
            v_role
        );
    END LOOP;
    v_seq := pg_get_serial_sequence('public.stripe_webhook_huerfano', 'id');
    IF v_seq IS NOT NULL THEN
        EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC', v_seq);
        FOR v_role IN SELECT rolname FROM pg_roles
                      WHERE rolname IN ('anon', 'authenticated')
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM %I', v_seq, v_role
            );
        END LOOP;
    END IF;
END
$security$;

DO $postflight$
DECLARE
    v_role text;
BEGIN
    IF to_regclass('public.stripe_webhook_huerfano') IS NULL THEN
        RAISE EXCEPTION 'CU36 E-3: falta stripe_webhook_huerfano';
    END IF;
    -- RLS desactivado por instruccion del propietario.
    IF (SELECT relrowsecurity FROM pg_class
         WHERE oid = to_regclass('public.stripe_webhook_huerfano')) THEN
        RAISE EXCEPTION 'CU36 E-3: RLS no debe habilitarse';
    END IF;
    FOR v_role IN SELECT rolname FROM pg_roles
                  WHERE rolname IN ('anon', 'authenticated')
    LOOP
        IF has_table_privilege(v_role, 'public.stripe_webhook_huerfano', 'SELECT')
           OR has_table_privilege(v_role, 'public.stripe_webhook_huerfano', 'INSERT')
           OR has_table_privilege(v_role, 'public.stripe_webhook_huerfano', 'UPDATE')
           OR has_table_privilege(v_role, 'public.stripe_webhook_huerfano', 'DELETE') THEN
            RAISE EXCEPTION
                'CU36 E-3: % conserva privilegios en stripe_webhook_huerfano', v_role;
        END IF;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_stripe_webhook_huerfano_evento'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'CU36 E-3: falta unicidad de stripe_event_id';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_stripe_webhook_huerfano_estado'
    ) THEN
        RAISE EXCEPTION 'CU36 E-3: falta restriccion de estado';
    END IF;
END
$postflight$;
