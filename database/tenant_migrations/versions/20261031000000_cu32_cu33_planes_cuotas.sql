-- CU32/CU33: planes de pago, cuotas y vinculacion con pagos CU31.
-- Transaccion controlada por tenant_migrator.py. No incluir BEGIN/COMMIT.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE
    v_missing text;
    v_existing text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.usuario'), ('public.paciente'), ('public.consulta_clinica'),
          ('public.servicio_realizado'), ('public.pago'), ('public.pago_detalle'),
          ('public.modulo'), ('public.funcion'), ('public.accion'),
          ('public.rol'), ('public.rol_funcion')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'CU32/CU33: faltan prerrequisitos: %', v_missing;
    END IF;

    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_existing
      FROM (VALUES
          ('public.plan_pago'), ('public.plan_pago_servicio'),
          ('public.cuota'), ('public.intento_pago_cuota')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NOT NULL;
    IF v_existing IS NOT NULL THEN
        RAISE EXCEPTION
            'CU32/CU33: instalacion parcial o no registrada: %', v_existing;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
              ('public.usuario'::regclass, 'id'),
              ('public.paciente'::regclass, 'id'),
              ('public.consulta_clinica'::regclass, 'id'),
              ('public.servicio_realizado'::regclass, 'id'),
              ('public.pago'::regclass, 'id')
          ) x(tabla, columna)
          LEFT JOIN pg_attribute a
            ON a.attrelid=x.tabla AND a.attname=x.columna
           AND a.attnum>0 AND NOT a.attisdropped
         WHERE a.atttypid IS DISTINCT FROM 'pg_catalog.int8'::regtype
    ) THEN
        RAISE EXCEPTION 'CU32/CU33: las llaves referenciadas deben ser BIGINT';
    END IF;
END
$preflight$;

CREATE TABLE public.plan_pago (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paciente_id BIGINT NOT NULL,
    consulta_clinica_id BIGINT NOT NULL,
    monto_total NUMERIC(12,2) NOT NULL,
    moneda VARCHAR(3) NOT NULL DEFAULT 'BOB',
    cantidad_cuotas INTEGER NOT NULL,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado VARCHAR(20) NOT NULL DEFAULT 'ACTIVO',
    observaciones TEXT,
    creado_por BIGINT NOT NULL,
    clave_idempotencia VARCHAR(100) NOT NULL,
    CONSTRAINT fk_plan_pago_paciente FOREIGN KEY (paciente_id)
        REFERENCES public.paciente(id) ON DELETE RESTRICT,
    CONSTRAINT fk_plan_pago_consulta FOREIGN KEY (consulta_clinica_id)
        REFERENCES public.consulta_clinica(id) ON DELETE RESTRICT,
    CONSTRAINT fk_plan_pago_creador FOREIGN KEY (creado_por)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    CONSTRAINT uq_plan_pago_clave_idempotencia UNIQUE (clave_idempotencia),
    CONSTRAINT ck_plan_pago_monto CHECK (monto_total > 0),
    CONSTRAINT ck_plan_pago_moneda CHECK (moneda = 'BOB'),
    CONSTRAINT ck_plan_pago_cantidad CHECK (cantidad_cuotas > 0),
    CONSTRAINT ck_plan_pago_estado CHECK (
        estado IN ('ACTIVO', 'COMPLETADO', 'CANCELADO')
    )
);

CREATE INDEX ix_plan_pago_paciente_estado
    ON public.plan_pago (paciente_id, estado, fecha_creacion DESC);
CREATE INDEX ix_plan_pago_consulta ON public.plan_pago (consulta_clinica_id);
CREATE INDEX ix_plan_pago_creado_por ON public.plan_pago (creado_por);

CREATE TABLE public.plan_pago_servicio (
    plan_pago_id BIGINT NOT NULL,
    servicio_realizado_id BIGINT NOT NULL,
    monto_financiado NUMERIC(12,2) NOT NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT pk_plan_pago_servicio PRIMARY KEY
        (plan_pago_id, servicio_realizado_id),
    CONSTRAINT fk_pps_plan FOREIGN KEY (plan_pago_id)
        REFERENCES public.plan_pago(id) ON DELETE RESTRICT,
    CONSTRAINT fk_pps_servicio FOREIGN KEY (servicio_realizado_id)
        REFERENCES public.servicio_realizado(id) ON DELETE RESTRICT,
    CONSTRAINT ck_pps_monto CHECK (monto_financiado > 0)
);

CREATE UNIQUE INDEX uq_pps_servicio_comprometido
    ON public.plan_pago_servicio (servicio_realizado_id)
    WHERE activo IS TRUE;
CREATE INDEX ix_pps_servicio_historial
    ON public.plan_pago_servicio (servicio_realizado_id);

CREATE TABLE public.cuota (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_pago_id BIGINT NOT NULL,
    numero_cuota INTEGER NOT NULL,
    monto_programado NUMERIC(12,2) NOT NULL,
    fecha_vencimiento DATE NOT NULL,
    estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
    CONSTRAINT fk_cuota_plan FOREIGN KEY (plan_pago_id)
        REFERENCES public.plan_pago(id) ON DELETE RESTRICT,
    CONSTRAINT uq_cuota_plan_numero UNIQUE (plan_pago_id, numero_cuota),
    CONSTRAINT ck_cuota_numero CHECK (numero_cuota > 0),
    CONSTRAINT ck_cuota_monto CHECK (monto_programado > 0),
    CONSTRAINT ck_cuota_estado CHECK (
        estado IN ('PENDIENTE', 'PAGADA', 'CANCELADA')
    )
);

CREATE INDEX ix_cuota_plan_estado_vencimiento
    ON public.cuota (plan_pago_id, estado, fecha_vencimiento);

CREATE TABLE public.intento_pago_cuota (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cuota_id BIGINT NOT NULL,
    pago_id BIGINT NOT NULL,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    aplicado_a_cuota BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_intento_cuota FOREIGN KEY (cuota_id)
        REFERENCES public.cuota(id) ON DELETE RESTRICT,
    CONSTRAINT fk_intento_pago FOREIGN KEY (pago_id)
        REFERENCES public.pago(id) ON DELETE RESTRICT,
    CONSTRAINT uq_intento_pago UNIQUE (pago_id)
);

CREATE UNIQUE INDEX uq_intento_cuota_aplicada
    ON public.intento_pago_cuota (cuota_id)
    WHERE aplicado_a_cuota IS TRUE;
CREATE INDEX ix_intento_cuota_historial
    ON public.intento_pago_cuota (cuota_id, fecha_creacion DESC);

COMMENT ON COLUMN public.plan_pago_servicio.activo IS
    'Reserva financiera: permanece TRUE al completar; solo se libera al cancelar sin cobros.';
COMMENT ON COLUMN public.cuota.estado IS
    'VENCIDA es una condicion calculada; no se persiste.';

ALTER TABLE public.plan_pago ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.plan_pago_servicio ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cuota ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.intento_pago_cuota ENABLE ROW LEVEL SECURITY;

ALTER TABLE public.plan_pago NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.plan_pago_servicio NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.cuota NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.intento_pago_cuota NO FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.plan_pago,
    public.plan_pago_servicio, public.cuota, public.intento_pago_cuota
    FROM PUBLIC;

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
            'REVOKE ALL PRIVILEGES ON TABLE public.plan_pago, public.plan_pago_servicio, public.cuota, public.intento_pago_cuota FROM %I',
            v_role
        );
    END LOOP;
    FOR v_tab IN SELECT unnest(ARRAY['plan_pago','cuota','intento_pago_cuota'])
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

DO $rbac$
DECLARE
    v_modulo bigint;
    v_ambas bigint;
    v_admin bigint;
    v_recepcion bigint;
    v_funcion bigint;
    v_nombre text;
BEGIN
    SELECT min(id) INTO v_modulo FROM public.modulo
     WHERE lower(trim(nombre))='pagos' AND estado IS TRUE;
    SELECT min(id) INTO v_ambas FROM public.accion
     WHERE upper(trim(nombre))='AMBAS' AND estado IS TRUE;
    SELECT min(id) INTO v_admin FROM public.rol
     WHERE lower(trim(nombre))='administrador' AND estado IS TRUE;
    SELECT min(id) INTO v_recepcion FROM public.rol
     WHERE lower(trim(nombre))='recepcionista' AND estado IS TRUE;
    IF v_modulo IS NULL OR v_ambas IS NULL
       OR v_admin IS NULL OR v_recepcion IS NULL THEN
        RAISE EXCEPTION 'CU32/CU33: catalogo RBAC incompleto';
    END IF;

    FOREACH v_nombre IN ARRAY ARRAY['Gestionar planes de pago','Gestionar cuotas']
    LOOP
        INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
        VALUES (
            v_modulo, v_nombre,
            CASE WHEN v_nombre='Gestionar planes de pago'
                 THEN 'Crear, consultar y cancelar planes de pago'
                 ELSE 'Consultar, cobrar, confirmar y rechazar cuotas' END,
            TRUE
        )
        ON CONFLICT (modulo_id, nombre) DO NOTHING;
        SELECT id INTO v_funcion FROM public.funcion
         WHERE modulo_id=v_modulo AND nombre=v_nombre AND estado IS TRUE;
        IF v_funcion IS NULL THEN
            RAISE EXCEPTION 'CU32/CU33: no se resolvio funcion %', v_nombre;
        END IF;
        INSERT INTO public.rol_funcion
            (rol_id, funcion_id, accion_id, descripcion)
        VALUES
            (v_admin, v_funcion, v_ambas, 'Asignacion inicial CU32/CU33'),
            (v_recepcion, v_funcion, v_ambas, 'Asignacion inicial CU32/CU33')
        ON CONFLICT (rol_id, funcion_id) DO NOTHING;
    END LOOP;
END
$rbac$;

DO $postflight$
DECLARE
    v_tab text;
    v_role text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY[
        'plan_pago','plan_pago_servicio','cuota','intento_pago_cuota'
    ] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'CU32/CU33: falta tabla %', v_tab;
        END IF;
        IF NOT (SELECT relrowsecurity FROM pg_class
                 WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'CU32/CU33: RLS no habilitado en %', v_tab;
        END IF;
        FOR v_role IN SELECT rolname FROM pg_roles
                      WHERE rolname IN ('anon','authenticated')
        LOOP
            IF has_table_privilege(v_role, 'public.' || v_tab, 'SELECT')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'INSERT')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'UPDATE')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'DELETE') THEN
                RAISE EXCEPTION 'CU32/CU33: % conserva privilegios en %', v_role, v_tab;
            END IF;
        END LOOP;
    END LOOP;
    IF (SELECT count(*) FROM public.funcion f
        JOIN public.modulo m ON m.id=f.modulo_id
        WHERE lower(trim(m.nombre))='pagos'
          AND f.nombre IN ('Gestionar planes de pago','Gestionar cuotas')) <> 2 THEN
        RAISE EXCEPTION 'CU32/CU33: funciones RBAC incompletas';
    END IF;
    IF (SELECT count(*)
          FROM public.rol_funcion rf
          JOIN public.rol r ON r.id=rf.rol_id
          JOIN public.funcion f ON f.id=rf.funcion_id
          JOIN public.accion a ON a.id=rf.accion_id
         WHERE lower(trim(r.nombre)) IN ('administrador','recepcionista')
           AND f.nombre IN ('Gestionar planes de pago','Gestionar cuotas')
           AND upper(trim(a.nombre))='AMBAS') <> 4 THEN
        RAISE EXCEPTION 'CU32/CU33: asignaciones administrativas incompletas';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.rol_funcion rf
          JOIN public.rol r ON r.id=rf.rol_id
          JOIN public.funcion f ON f.id=rf.funcion_id
         WHERE lower(trim(r.nombre)) IN ('paciente','oftalmologo')
           AND f.nombre IN ('Gestionar planes de pago','Gestionar cuotas')
    ) THEN
        RAISE EXCEPTION 'CU32/CU33: rol clinico no autorizado conserva permisos';
    END IF;
END
$postflight$;

