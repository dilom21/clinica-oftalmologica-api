-- CU36: libro estructurado de anulaciones, correcciones, devoluciones y
-- reembolsos de pagos. `pago.monto` y `pago_detalle.monto_aplicado` siguen
-- siendo inmutables; el historial financiero vive en estas tablas.
-- Transaccion controlada por tenant_migrator.py. No incluir BEGIN/COMMIT.
-- RLS permanece DESACTIVADO por instruccion del propietario (CU36).

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE
    v_missing text;
    v_existing text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.usuario'), ('public.servicio_realizado'),
          ('public.pago'), ('public.pago_detalle')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'CU36: faltan prerrequisitos: %', v_missing;
    END IF;

    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_existing
      FROM (VALUES
          ('public.pago_ajuste'), ('public.pago_ajuste_detalle')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NOT NULL;
    IF v_existing IS NOT NULL THEN
        RAISE EXCEPTION
            'CU36: instalacion parcial o no registrada: %', v_existing;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
              ('public.pago'::regclass, 'id'),
              ('public.servicio_realizado'::regclass, 'id'),
              ('public.usuario'::regclass, 'id')
          ) x(tabla, columna)
          LEFT JOIN pg_attribute a
            ON a.attrelid=x.tabla AND a.attname=x.columna
           AND a.attnum>0 AND NOT a.attisdropped
         WHERE a.atttypid IS DISTINCT FROM 'pg_catalog.int8'::regtype
    ) THEN
        RAISE EXCEPTION 'CU36: las llaves referenciadas deben ser BIGINT';
    END IF;
END
$preflight$;

CREATE TABLE public.pago_ajuste (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pago_id BIGINT NOT NULL,
    tipo VARCHAR(20) NOT NULL,
    naturaleza VARCHAR(40) NOT NULL,
    efecto_caja VARCHAR(20) NOT NULL,
    estado VARCHAR(25) NOT NULL DEFAULT 'CONFIRMADO',
    importe NUMERIC(12,2) NOT NULL,
    moneda VARCHAR(3) NOT NULL DEFAULT 'BOB',
    motivo TEXT NOT NULL,
    evidencia TEXT,
    referencia_externa VARCHAR(255),
    clave_idempotencia VARCHAR(100) NOT NULL,
    revertir_aplicacion_cuota BOOLEAN NOT NULL DEFAULT FALSE,
    creado_por BIGINT,
    confirmado_por BIGINT,
    conciliado_por BIGINT,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_confirmacion TIMESTAMPTZ,
    fecha_conciliacion TIMESTAMPTZ,
    observaciones TEXT,
    CONSTRAINT fk_pago_ajuste_pago FOREIGN KEY (pago_id)
        REFERENCES public.pago(id) ON DELETE RESTRICT,
    CONSTRAINT fk_pago_ajuste_creador FOREIGN KEY (creado_por)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    CONSTRAINT fk_pago_ajuste_confirmador FOREIGN KEY (confirmado_por)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    CONSTRAINT fk_pago_ajuste_conciliador FOREIGN KEY (conciliado_por)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    CONSTRAINT uq_pago_ajuste_clave_idempotencia UNIQUE (clave_idempotencia),
    CONSTRAINT ck_pago_ajuste_tipo CHECK (
        tipo IN ('ANULACION','CORRECCION','DEVOLUCION','REEMBOLSO',
                 'RECONCILIACION')
    ),
    CONSTRAINT ck_pago_ajuste_naturaleza CHECK (
        naturaleza IN ('ERROR_DE_REGISTRO','PAGO_DUPLICADO',
                       'SERVICIO_CANCELADO','SOBREPAGO','PAGO_INSUFICIENTE',
                       'REVERSION_FINANCIACION','OTRO')
    ),
    CONSTRAINT ck_pago_ajuste_efecto_caja CHECK (
        efecto_caja IN ('SIN_EFECTO','DEVOLUCION_MANUAL','DEVOLUCION_STRIPE')
    ),
    CONSTRAINT ck_pago_ajuste_estado CHECK (
        estado IN ('PENDIENTE_PROVEEDOR','CONFIRMADO','FALLIDO',
                   'CANCELADO','DESCONOCIDO')
    ),
    CONSTRAINT ck_pago_ajuste_importe CHECK (
        (tipo IN ('ANULACION','RECONCILIACION') AND importe = 0)
        OR (tipo = 'CORRECCION' AND importe <> 0)
        OR (tipo IN ('DEVOLUCION','REEMBOLSO') AND importe < 0)
    ),
    CONSTRAINT ck_pago_ajuste_correccion_positiva CHECK (
        importe <= 0 OR naturaleza = 'ERROR_DE_REGISTRO'
    ),
    CONSTRAINT ck_pago_ajuste_efecto_por_tipo CHECK (
        (tipo = 'REEMBOLSO' AND efecto_caja = 'DEVOLUCION_STRIPE')
        OR (tipo = 'DEVOLUCION' AND efecto_caja = 'DEVOLUCION_MANUAL')
        OR (tipo IN ('ANULACION','CORRECCION','RECONCILIACION')
            AND efecto_caja = 'SIN_EFECTO')
    ),
    CONSTRAINT ck_pago_ajuste_moneda CHECK (
        length(moneda) = 3 AND moneda = upper(moneda)
    )
);

CREATE UNIQUE INDEX uq_pago_ajuste_referencia_externa
    ON public.pago_ajuste (referencia_externa)
    WHERE referencia_externa IS NOT NULL;
CREATE INDEX ix_pago_ajuste_pago_fecha
    ON public.pago_ajuste (pago_id, fecha_creacion);
CREATE INDEX ix_pago_ajuste_estado ON public.pago_ajuste (estado);

CREATE TABLE public.pago_ajuste_detalle (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ajuste_id BIGINT NOT NULL,
    servicio_realizado_id BIGINT NOT NULL,
    monto NUMERIC(12,2) NOT NULL,
    CONSTRAINT fk_pago_ajuste_detalle_ajuste FOREIGN KEY (ajuste_id)
        REFERENCES public.pago_ajuste(id) ON DELETE RESTRICT,
    CONSTRAINT fk_pago_ajuste_detalle_servicio FOREIGN KEY (servicio_realizado_id)
        REFERENCES public.servicio_realizado(id) ON DELETE RESTRICT,
    CONSTRAINT uq_pago_ajuste_detalle_ajuste_servicio
        UNIQUE (ajuste_id, servicio_realizado_id),
    CONSTRAINT ck_pago_ajuste_detalle_monto CHECK (monto <> 0)
);

CREATE INDEX ix_pago_ajuste_detalle_servicio
    ON public.pago_ajuste_detalle (servicio_realizado_id);

COMMENT ON TABLE public.pago_ajuste IS
    'CU36: cabecera firmada de anulaciones, correcciones, devoluciones y reembolsos.';
COMMENT ON COLUMN public.pago_ajuste.importe IS
    'Importe firmado. Positivo solo para CORRECCION ERROR_DE_REGISTRO; cero solo para ANULACION/RECONCILIACION.';
COMMENT ON COLUMN public.pago_ajuste.estado IS
    'Solo CONFIRMADO afecta el importe vigente; PENDIENTE_PROVEEDOR/DESCONOCIDO bloquean conciliacion.';
COMMENT ON TABLE public.pago_ajuste_detalle IS
    'CU36: distribucion del importe del ajuste por servicio realizado.';

REVOKE ALL PRIVILEGES ON TABLE public.pago_ajuste,
    public.pago_ajuste_detalle FROM PUBLIC;

DO $security$
DECLARE
    v_role text;
    v_seq text;
    v_tab text;
BEGIN
    FOR v_role IN SELECT rolname FROM pg_roles
                  WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.pago_ajuste, public.pago_ajuste_detalle FROM %I',
            v_role
        );
    END LOOP;
    FOR v_tab IN SELECT unnest(ARRAY['pago_ajuste','pago_ajuste_detalle'])
    LOOP
        v_seq := pg_get_serial_sequence(format('public.%I', v_tab), 'id');
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
    END LOOP;
END
$security$;

DO $postflight$
DECLARE
    v_tab text;
    v_role text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY['pago_ajuste','pago_ajuste_detalle'] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'CU36: falta tabla %', v_tab;
        END IF;
        -- RLS desactivado por instruccion del propietario.
        IF (SELECT relrowsecurity FROM pg_class
             WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'CU36: RLS no debe habilitarse en %', v_tab;
        END IF;
        FOR v_role IN SELECT rolname FROM pg_roles
                      WHERE rolname IN ('anon','authenticated')
        LOOP
            IF has_table_privilege(v_role, 'public.' || v_tab, 'SELECT')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'INSERT')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'UPDATE')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'DELETE') THEN
                RAISE EXCEPTION 'CU36: % conserva privilegios en %', v_role, v_tab;
            END IF;
        END LOOP;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_pago_ajuste_referencia_externa'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'CU36: falta indice unico de referencia externa';
    END IF;
END
$postflight$;
