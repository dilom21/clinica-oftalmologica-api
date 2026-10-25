
CREATE TABLE public.pago (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_hora_pago TIMESTAMPTZ NULL,
    monto NUMERIC(12,2) NOT NULL,
    moneda VARCHAR(3) NOT NULL DEFAULT 'BOB',
    metodo_pago VARCHAR(30) NOT NULL DEFAULT 'TARJETA',
    estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    pasarela VARCHAR(50) NULL,
    referencia_transaccion VARCHAR(255) NULL,
    observaciones TEXT NULL,

    CONSTRAINT ck_pago_monto_positivo
        CHECK (monto > 0),

    CONSTRAINT ck_pago_moneda
        CHECK (char_length(moneda) = 3 AND moneda = upper(moneda)),

    CONSTRAINT ck_pago_metodo
        CHECK (metodo_pago IN ('EFECTIVO', 'TARJETA', 'TRANSFERENCIA', 'QR', 'OTRO')),

    CONSTRAINT ck_pago_estado
        CHECK (estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'ANULADO', 'REEMBOLSADO')),

    CONSTRAINT uq_pago_referencia_transaccion
        UNIQUE (referencia_transaccion)
);

CREATE TABLE public.pago_detalle (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pago_id BIGINT NOT NULL,
    servicio_realizado_id BIGINT NOT NULL,
    monto_aplicado NUMERIC(12,2) NOT NULL,

    CONSTRAINT fk_pago_detalle_pago
        FOREIGN KEY (pago_id)
        REFERENCES public.pago(id)
        ON DELETE CASCADE,

    CONSTRAINT fk_pago_detalle_servicio_realizado
        FOREIGN KEY (servicio_realizado_id)
        REFERENCES public.servicio_realizado(id)
        ON DELETE RESTRICT,

    CONSTRAINT ck_pago_detalle_monto_positivo
        CHECK (monto_aplicado > 0),

    CONSTRAINT uq_pago_detalle_pago_servicio
        UNIQUE (pago_id, servicio_realizado_id)
);

CREATE INDEX idx_pago_estado
    ON public.pago (estado);

CREATE INDEX idx_pago_referencia_transaccion
    ON public.pago (referencia_transaccion);

CREATE INDEX idx_pago_detalle_pago
    ON public.pago_detalle (pago_id);

CREATE INDEX idx_pago_detalle_servicio_realizado
    ON public.pago_detalle (servicio_realizado_id);

COMMENT ON TABLE public.pago IS
'Cabecera de una transaccion de pago. El contexto clinico y el paciente se derivan de pago_detalle -> servicio_realizado.';

COMMENT ON COLUMN public.pago.referencia_transaccion IS
'Referencia externa de la pasarela, por ejemplo el PaymentIntent id de Stripe (pi_...).';

COMMENT ON TABLE public.pago_detalle IS
'Detalle que relaciona un pago con uno o varios servicios realizados.';

COMMENT ON COLUMN public.pago_detalle.monto_aplicado IS
'Importe del servicio aplicado a este pago, preservado como valor historico.';
