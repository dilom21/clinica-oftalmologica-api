-- CU31 - Pagos administrativos, idempotencia y outbox de comprobantes.
-- PREPARADA, NO EJECUTADA. Aplicar primero en la base principal y luego,
-- de forma controlada, en cada tenant. El script es aditivo e idempotente.
-- VERSION DE ESQUEMA RESULTANTE: v2-cu31.
--
-- notificacion_pago es una tabla exclusivamente backend. RLS queda habilitado
-- sin politicas para clientes y se revocan todos los privilegios de PUBLIC,
-- anon y authenticated. FastAPI y el worker deben conectarse directamente con
-- el propietario de las tablas o con un rol BYPASSRLS deliberadamente auditado.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.pago') IS NULL
       OR to_regclass('public.pago_detalle') IS NULL
       OR to_regclass('public.servicio_realizado') IS NULL
       OR to_regclass('public.modulo') IS NULL
       OR to_regclass('public.funcion') IS NULL
       OR to_regclass('public.accion') IS NULL
       OR to_regclass('public.rol') IS NULL
       OR to_regclass('public.rol_funcion') IS NULL THEN
        RAISE EXCEPTION 'CU31: faltan tablas canonicas de pagos o RBAC';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = 'public' AND t.relname = 'pago_detalle'
          AND c.contype = 'f'
          AND pg_get_constraintdef(c.oid) LIKE '%(pago_id)%REFERENCES pago(id)%'
    ) THEN
        RAISE EXCEPTION 'CU31: pago_detalle.pago_id no referencia pago.id';
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
        RAISE EXCEPTION 'CU31: faltan restricciones unicas RBAC canonicas';
    END IF;
END $$;

ALTER TABLE public.pago
    ADD COLUMN IF NOT EXISTS clave_idempotencia varchar(100);

DO $$
DECLARE
    v_tipo text;
    v_nullable boolean;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod), NOT a.attnotnull
      INTO v_tipo, v_nullable
      FROM pg_attribute a
     WHERE a.attrelid = 'public.pago'::regclass
       AND a.attname = 'clave_idempotencia'
       AND a.attnum > 0
       AND NOT a.attisdropped;

    IF v_tipo IS DISTINCT FROM 'character varying(100)'
       OR v_nullable IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'CU31: pago.clave_idempotencia existe con contrato incompatible (tipo=%, nullable=%)',
            v_tipo, v_nullable;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_pago_clave_idempotencia
    ON public.pago (clave_idempotencia)
    WHERE clave_idempotencia IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.notificacion_pago (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pago_id bigint NOT NULL
        CONSTRAINT fk_notificacion_pago_pago
        REFERENCES public.pago(id) ON DELETE RESTRICT,
    tipo varchar(50) NOT NULL DEFAULT 'COMPROBANTE_PAGO',
    estado varchar(30) NOT NULL DEFAULT 'PENDIENTE',
    destinatario varchar(150),
    clave_idempotencia varchar(200) NOT NULL,
    proveedor varchar(30) NOT NULL DEFAULT 'RESEND',
    proveedor_mensaje_id varchar(255),
    intentos bigint NOT NULL DEFAULT 0,
    proximo_intento timestamptz,
    bloqueado_hasta timestamptz,
    ultimo_error varchar(255),
    fecha_creacion timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_aceptado timestamptz,
    fecha_entregado timestamptz,
    CONSTRAINT uq_notificacion_pago_tipo UNIQUE (pago_id, tipo),
    CONSTRAINT uq_notificacion_pago_clave UNIQUE (clave_idempotencia),
    CONSTRAINT ck_notificacion_pago_estado CHECK (
        estado IN ('PENDIENTE', 'PROCESANDO',
                   'ACEPTADO_POR_PROVEEDOR', 'FALLIDO', 'ENTREGADO')
    ),
    CONSTRAINT ck_notificacion_pago_intentos CHECK (intentos >= 0)
);

-- Si una version preliminar creo la FK con CASCADE, se corrige de forma
-- atomica. RESTRICT preserva la evidencia: no se puede borrar un pago que ya
-- tenga intentos o entregas registrados en el outbox.
DO $$
DECLARE
    v_fk record;
    v_fk_count integer;
    v_fk_found boolean;
BEGIN
    SELECT count(*)
      INTO v_fk_count
      FROM pg_constraint c
     WHERE c.conrelid = 'public.notificacion_pago'::regclass
       AND c.contype = 'f'
       AND c.conkey = ARRAY[
           (SELECT attnum FROM pg_attribute
             WHERE attrelid = 'public.notificacion_pago'::regclass
               AND attname = 'pago_id')
       ]::smallint[];

    IF v_fk_count > 1 THEN
        RAISE EXCEPTION 'CU31: notificacion_pago.pago_id tiene mas de una FK';
    END IF;

    SELECT c.conname, c.confrelid, c.confkey, c.confdeltype
      INTO v_fk
      FROM pg_constraint c
     WHERE c.conrelid = 'public.notificacion_pago'::regclass
       AND c.contype = 'f'
       AND c.conkey = ARRAY[
           (SELECT attnum FROM pg_attribute
             WHERE attrelid = 'public.notificacion_pago'::regclass
               AND attname = 'pago_id')
       ]::smallint[];

    v_fk_found := FOUND;

    IF v_fk_found AND (
        v_fk.confrelid <> 'public.pago'::regclass
        OR v_fk.confkey <> ARRAY[
            (SELECT attnum FROM pg_attribute
              WHERE attrelid = 'public.pago'::regclass AND attname = 'id')
        ]::smallint[]
    ) THEN
        RAISE EXCEPTION 'CU31: notificacion_pago.pago_id tiene una FK incompatible';
    END IF;

    IF NOT v_fk_found THEN
        ALTER TABLE public.notificacion_pago
            ADD CONSTRAINT fk_notificacion_pago_pago
            FOREIGN KEY (pago_id) REFERENCES public.pago(id)
            ON DELETE RESTRICT NOT VALID;
        ALTER TABLE public.notificacion_pago
            VALIDATE CONSTRAINT fk_notificacion_pago_pago;
    ELSIF v_fk.confdeltype <> 'r' THEN
        EXECUTE format(
            'ALTER TABLE public.notificacion_pago DROP CONSTRAINT %I',
            v_fk.conname
        );
        ALTER TABLE public.notificacion_pago
            ADD CONSTRAINT fk_notificacion_pago_pago
            FOREIGN KEY (pago_id) REFERENCES public.pago(id)
            ON DELETE RESTRICT NOT VALID;
        ALTER TABLE public.notificacion_pago
            VALIDATE CONSTRAINT fk_notificacion_pago_pago;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_notificacion_pago_proveedor_mensaje
    ON public.notificacion_pago (proveedor_mensaje_id)
    WHERE proveedor_mensaje_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_notificacion_pago_cola
    ON public.notificacion_pago (proximo_intento, id)
    WHERE estado IN ('PENDIENTE', 'FALLIDO', 'PROCESANDO')
      AND destinatario IS NOT NULL
      AND intentos < 8;

-- Defensa en profundidad para una tabla ubicada en el esquema public de
-- Supabase. REVOKE corta el acceso en el nivel de privilegios y RLS sin
-- politicas mantiene denegado el acceso via Data API.
ALTER TABLE public.notificacion_pago ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notificacion_pago NO FORCE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.notificacion_pago FROM PUBLIC;

DO $$
DECLARE
    v_rol text;
    v_secuencia text;
BEGIN
    v_secuencia := pg_get_serial_sequence('public.notificacion_pago', 'id');

    FOR v_rol IN
        SELECT rolname FROM pg_roles
         WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.notificacion_pago FROM %I',
            v_rol
        );
        IF v_secuencia IS NOT NULL THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM %I',
                v_secuencia, v_rol
            );
        END IF;
    END LOOP;

    IF v_secuencia IS NOT NULL THEN
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC',
            v_secuencia
        );
    END IF;
END $$;

-- RBAC: reutiliza el modulo Pagos existente. No hardcodea ids.
-- Las asignaciones se siembran SOLO cuando cada funcion se crea. Si CU05
-- revoca o cambia un permiso, una segunda ejecucion no lo restaura.
DO $$
DECLARE
    v_modulo_id bigint;
    v_modulos_encontrados integer;
    v_elementos_encontrados integer;
    v_funcion_gestion bigint;
    v_funcion_confirmar bigint;
    v_gestion_nueva boolean := FALSE;
    v_confirmar_nueva boolean := FALSE;
    v_rol_admin bigint;
    v_rol_recepcionista bigint;
    v_accion_ambas bigint;
    v_accion_escritura bigint;
BEGIN
    SELECT count(*), min(id)
      INTO v_modulos_encontrados, v_modulo_id
      FROM public.modulo
     WHERE lower(trim(nombre)) = 'pagos';

    IF v_modulos_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU31: se esperaba exactamente un modulo Pagos; encontrados: %',
            v_modulos_encontrados;
    END IF;

    SELECT count(*), min(id)
      INTO v_elementos_encontrados, v_rol_admin
      FROM public.rol
     WHERE lower(trim(nombre)) IN ('administrador', 'admin')
       AND estado IS TRUE;

    IF v_elementos_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU31: se esperaba un rol Administrador activo; encontrados: %',
            v_elementos_encontrados;
    END IF;

    SELECT count(*), min(id)
      INTO v_elementos_encontrados, v_rol_recepcionista
      FROM public.rol
     WHERE lower(trim(nombre)) = 'recepcionista'
       AND estado IS TRUE;

    IF v_elementos_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU31: se esperaba un rol Recepcionista activo; encontrados: %',
            v_elementos_encontrados;
    END IF;
    SELECT count(*), min(id)
      INTO v_elementos_encontrados, v_accion_ambas
      FROM public.accion
     WHERE upper(trim(nombre)) = 'AMBAS' AND estado IS TRUE;

    IF v_elementos_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU31: se esperaba una accion AMBAS activa; encontradas: %',
            v_elementos_encontrados;
    END IF;

    SELECT count(*), min(id)
      INTO v_elementos_encontrados, v_accion_escritura
      FROM public.accion
     WHERE upper(trim(nombre)) = 'ESCRITURA' AND estado IS TRUE;

    IF v_elementos_encontrados <> 1 THEN
        RAISE EXCEPTION
            'CU31: se esperaba una accion ESCRITURA activa; encontradas: %',
            v_elementos_encontrados;
    END IF;

    INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
    VALUES (
        v_modulo_id,
        'Gestionar pagos',
        'Consultar y registrar pagos administrativos',
        TRUE
    )
    ON CONFLICT (modulo_id, nombre) DO NOTHING
    RETURNING id INTO v_funcion_gestion;

    IF v_funcion_gestion IS NOT NULL THEN
        v_gestion_nueva := TRUE;
    ELSE
        SELECT id INTO v_funcion_gestion
          FROM public.funcion
         WHERE modulo_id = v_modulo_id AND nombre = 'Gestionar pagos';
    END IF;

    INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
    VALUES (
        v_modulo_id,
        'Confirmar pagos bancarios',
        'Confirmar transferencias y pagos QR despues de verificar el abono',
        TRUE
    )
    ON CONFLICT (modulo_id, nombre) DO NOTHING
    RETURNING id INTO v_funcion_confirmar;

    IF v_funcion_confirmar IS NOT NULL THEN
        v_confirmar_nueva := TRUE;
    ELSE
        SELECT id INTO v_funcion_confirmar
          FROM public.funcion
         WHERE modulo_id = v_modulo_id
           AND nombre = 'Confirmar pagos bancarios';
    END IF;

    IF v_funcion_gestion IS NULL OR v_funcion_confirmar IS NULL THEN
        RAISE EXCEPTION 'CU31: no se pudieron resolver las dos funciones RBAC';
    END IF;

    IF v_gestion_nueva THEN
        INSERT INTO public.rol_funcion
            (rol_id, funcion_id, accion_id, descripcion)
        VALUES
            (v_rol_admin, v_funcion_gestion, v_accion_ambas,
             'Asignacion inicial CU31: Administrador gestiona pagos'),
            (v_rol_recepcionista, v_funcion_gestion, v_accion_ambas,
             'Asignacion inicial CU31: Recepcionista gestiona pagos')
        ON CONFLICT (rol_id, funcion_id) DO NOTHING;
    END IF;

    IF v_confirmar_nueva THEN
        INSERT INTO public.rol_funcion
            (rol_id, funcion_id, accion_id, descripcion)
        VALUES
            (v_rol_admin, v_funcion_confirmar, v_accion_ambas,
             'Asignacion inicial CU31: Administrador confirma pagos bancarios'),
            (v_rol_recepcionista, v_funcion_confirmar, v_accion_escritura,
             'Asignacion inicial CU31: Recepcionista confirma pagos bancarios')
        ON CONFLICT (rol_id, funcion_id) DO NOTHING;
    END IF;

    -- Verifica la siembra solo en la primera creacion. En reejecuciones no
    -- exige asignaciones porque CU05 pudo revocarlas legitimamente.
    IF v_gestion_nueva AND (
        SELECT count(*) FROM public.rol_funcion
         WHERE funcion_id = v_funcion_gestion
           AND rol_id IN (v_rol_admin, v_rol_recepcionista)
    ) <> 2 THEN
        RAISE EXCEPTION 'CU31: no se completaron las asignaciones de Gestionar pagos';
    END IF;

    IF v_confirmar_nueva AND (
        SELECT count(*) FROM public.rol_funcion
         WHERE funcion_id = v_funcion_confirmar
           AND rol_id IN (v_rol_admin, v_rol_recepcionista)
    ) <> 2 THEN
        RAISE EXCEPTION 'CU31: no se completaron las asignaciones de Confirmar pagos bancarios';
    END IF;
END $$;

-- Postcondiciones fuertes: CREATE ... IF NOT EXISTS no garantiza que un objeto
-- preexistente tenga la forma correcta. Cualquier deriva aborta el COMMIT.
DO $$
DECLARE
    v_errores text;
    v_rol text;
    v_predicado text;
    v_secuencia text;
BEGIN
    IF to_regclass('public.notificacion_pago') IS NULL THEN
        RAISE EXCEPTION 'CU31: no existe public.notificacion_pago';
    END IF;

    WITH esperada(nombre, tipo, not_null) AS (
        VALUES
            ('id', 'bigint', TRUE),
            ('pago_id', 'bigint', TRUE),
            ('tipo', 'character varying(50)', TRUE),
            ('estado', 'character varying(30)', TRUE),
            ('destinatario', 'character varying(150)', FALSE),
            ('clave_idempotencia', 'character varying(200)', TRUE),
            ('proveedor', 'character varying(30)', TRUE),
            ('proveedor_mensaje_id', 'character varying(255)', FALSE),
            ('intentos', 'bigint', TRUE),
            ('proximo_intento', 'timestamp with time zone', FALSE),
            ('bloqueado_hasta', 'timestamp with time zone', FALSE),
            ('ultimo_error', 'character varying(255)', FALSE),
            ('fecha_creacion', 'timestamp with time zone', TRUE),
            ('fecha_actualizacion', 'timestamp with time zone', TRUE),
            ('fecha_aceptado', 'timestamp with time zone', FALSE),
            ('fecha_entregado', 'timestamp with time zone', FALSE)
    )
    SELECT string_agg(
               format('%s(tipo=%s, not_null=%s)', e.nombre,
                      coalesce(format_type(a.atttypid, a.atttypmod), 'AUSENTE'),
                      coalesce(a.attnotnull::text, 'AUSENTE')),
               ', ' ORDER BY e.nombre
           )
      INTO v_errores
      FROM esperada e
      LEFT JOIN pg_attribute a
        ON a.attrelid = 'public.notificacion_pago'::regclass
       AND a.attname = e.nombre
       AND a.attnum > 0
       AND NOT a.attisdropped
     WHERE a.attname IS NULL
        OR format_type(a.atttypid, a.atttypmod) <> e.tipo
        OR a.attnotnull <> e.not_null;

    IF v_errores IS NOT NULL THEN
        RAISE EXCEPTION 'CU31: columnas incompatibles en notificacion_pago: %',
            v_errores;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'public.notificacion_pago'::regclass
           AND attname = 'id' AND attidentity = 'a'
    ) THEN
        RAISE EXCEPTION 'CU31: notificacion_pago.id no es IDENTITY ALWAYS';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_attrdef d
          JOIN pg_attribute a
            ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE d.adrelid = 'public.notificacion_pago'::regclass
           AND a.attname = 'tipo'
           AND pg_get_expr(d.adbin, d.adrelid) LIKE '%COMPROBANTE_PAGO%'
    ) OR NOT EXISTS (
        SELECT 1
          FROM pg_attrdef d
          JOIN pg_attribute a
            ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE d.adrelid = 'public.notificacion_pago'::regclass
           AND a.attname = 'estado'
           AND pg_get_expr(d.adbin, d.adrelid) LIKE '%PENDIENTE%'
    ) OR NOT EXISTS (
        SELECT 1
          FROM pg_attrdef d
          JOIN pg_attribute a
            ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE d.adrelid = 'public.notificacion_pago'::regclass
           AND a.attname = 'proveedor'
           AND pg_get_expr(d.adbin, d.adrelid) LIKE '%RESEND%'
    ) OR NOT EXISTS (
        SELECT 1
          FROM pg_attrdef d
          JOIN pg_attribute a
            ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE d.adrelid = 'public.notificacion_pago'::regclass
           AND a.attname = 'intentos'
           AND pg_get_expr(d.adbin, d.adrelid) ~ '^0(::bigint)?$'
    ) OR (
        SELECT count(*)
          FROM pg_attrdef d
          JOIN pg_attribute a
            ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE d.adrelid = 'public.notificacion_pago'::regclass
           AND a.attname IN ('fecha_creacion', 'fecha_actualizacion')
           AND lower(pg_get_expr(d.adbin, d.adrelid))
               IN ('current_timestamp', 'now()')
    ) <> 2 THEN
        RAISE EXCEPTION 'CU31: defaults incompatibles en notificacion_pago';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.contype = 'p'
           AND c.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.notificacion_pago'::regclass
                   AND attname = 'id')
           ]::smallint[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.conname = 'uq_notificacion_pago_tipo'
           AND c.contype = 'u'
           AND c.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.notificacion_pago'::regclass
                   AND attname = 'pago_id'),
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.notificacion_pago'::regclass
                   AND attname = 'tipo')
           ]::smallint[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.conname = 'uq_notificacion_pago_clave'
           AND c.contype = 'u'
           AND c.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.notificacion_pago'::regclass
                   AND attname = 'clave_idempotencia')
           ]::smallint[]
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.conname = 'ck_notificacion_pago_intentos'
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%intentos >= 0%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.conname = 'ck_notificacion_pago_estado'
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%PENDIENTE%'
           AND pg_get_constraintdef(c.oid) LIKE '%PROCESANDO%'
           AND pg_get_constraintdef(c.oid) LIKE '%ACEPTADO_POR_PROVEEDOR%'
           AND pg_get_constraintdef(c.oid) LIKE '%FALLIDO%'
           AND pg_get_constraintdef(c.oid) LIKE '%ENTREGADO%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.contype = 'f'
           AND c.confrelid = 'public.pago'::regclass
           AND c.confdeltype = 'r'
           AND c.conkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.notificacion_pago'::regclass
                   AND attname = 'pago_id')
           ]::smallint[]
           AND c.confkey = ARRAY[
               (SELECT attnum FROM pg_attribute
                 WHERE attrelid = 'public.pago'::regclass
                   AND attname = 'id')
           ]::smallint[]
    ) THEN
        RAISE EXCEPTION 'CU31: restricciones incompatibles en notificacion_pago';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_class x
          JOIN pg_namespace n ON n.oid = x.relnamespace
          JOIN pg_index i ON i.indexrelid = x.oid
         WHERE n.nspname = 'public'
           AND x.relname = 'uq_pago_clave_idempotencia'
           AND i.indrelid = 'public.pago'::regclass
           AND i.indisunique AND i.indisvalid
           AND pg_get_indexdef(i.indexrelid) LIKE '%(clave_idempotencia)%'
           AND regexp_replace(
                   lower(pg_get_expr(i.indpred, i.indrelid)), '[\s()]', '', 'g'
               ) = 'clave_idempotenciaisnotnull'
    ) THEN
        RAISE EXCEPTION 'CU31: indice uq_pago_clave_idempotencia incompatible';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_class x
          JOIN pg_namespace n ON n.oid = x.relnamespace
          JOIN pg_index i ON i.indexrelid = x.oid
         WHERE n.nspname = 'public'
           AND x.relname = 'uq_notificacion_pago_proveedor_mensaje'
           AND i.indrelid = 'public.notificacion_pago'::regclass
           AND i.indisunique AND i.indisvalid
           AND pg_get_indexdef(i.indexrelid) LIKE '%(proveedor_mensaje_id)%'
           AND regexp_replace(
                   lower(pg_get_expr(i.indpred, i.indrelid)), '[\s()]', '', 'g'
               ) = 'proveedor_mensaje_idisnotnull'
    ) THEN
        RAISE EXCEPTION
            'CU31: indice uq_notificacion_pago_proveedor_mensaje incompatible';
    END IF;

    SELECT regexp_replace(
               lower(pg_get_expr(i.indpred, i.indrelid)), '[\s()]', '', 'g'
           )
      INTO v_predicado
      FROM pg_class x
      JOIN pg_namespace n ON n.oid = x.relnamespace
      JOIN pg_index i ON i.indexrelid = x.oid
     WHERE n.nspname = 'public'
       AND x.relname = 'ix_notificacion_pago_cola'
       AND i.indrelid = 'public.notificacion_pago'::regclass
       AND i.indisvalid
       AND NOT i.indisunique
       AND pg_get_indexdef(i.indexrelid) LIKE '%(proximo_intento, id)%';

    IF v_predicado IS NULL
       OR v_predicado NOT LIKE '%pendiente%'
       OR v_predicado NOT LIKE '%fallido%'
       OR v_predicado NOT LIKE '%procesando%'
       OR v_predicado NOT LIKE '%destinatarioisnotnull%'
       OR v_predicado NOT LIKE '%intentos<8%' THEN
        RAISE EXCEPTION 'CU31: indice ix_notificacion_pago_cola incompatible';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'public.notificacion_pago'::regclass
           AND relrowsecurity IS TRUE
           AND relforcerowsecurity IS FALSE
    ) THEN
        RAISE EXCEPTION 'CU31: RLS backend-only no quedo configurado';
    END IF;

    FOR v_rol IN
        SELECT rolname FROM pg_roles
         WHERE rolname IN ('anon', 'authenticated')
    LOOP
        IF has_table_privilege(v_rol, 'public.notificacion_pago', 'SELECT')
           OR has_table_privilege(v_rol, 'public.notificacion_pago', 'INSERT')
           OR has_table_privilege(v_rol, 'public.notificacion_pago', 'UPDATE')
           OR has_table_privilege(v_rol, 'public.notificacion_pago', 'DELETE')
           OR has_table_privilege(v_rol, 'public.notificacion_pago', 'TRUNCATE')
           OR has_table_privilege(v_rol, 'public.notificacion_pago', 'REFERENCES')
           OR has_table_privilege(v_rol, 'public.notificacion_pago', 'TRIGGER') THEN
            RAISE EXCEPTION
                'CU31: el rol % conserva privilegios en notificacion_pago', v_rol;
        END IF;

        v_secuencia := pg_get_serial_sequence('public.notificacion_pago', 'id');
        IF v_secuencia IS NOT NULL AND (
            has_sequence_privilege(v_rol, v_secuencia, 'USAGE')
            OR has_sequence_privilege(v_rol, v_secuencia, 'SELECT')
            OR has_sequence_privilege(v_rol, v_secuencia, 'UPDATE')
        ) THEN
            RAISE EXCEPTION
                'CU31: el rol % conserva privilegios en la secuencia del outbox',
                v_rol;
        END IF;
    END LOOP;

    IF EXISTS (
        SELECT 1
          FROM pg_class c
          CROSS JOIN LATERAL aclexplode(
              coalesce(c.relacl, acldefault('r', c.relowner))
          ) acl
         WHERE c.oid = 'public.notificacion_pago'::regclass
           AND acl.grantee = 0
    ) THEN
        RAISE EXCEPTION 'CU31: PUBLIC conserva privilegios en notificacion_pago';
    END IF;

    IF (
        SELECT count(*)
          FROM public.funcion f
          JOIN public.modulo m ON m.id = f.modulo_id
         WHERE lower(trim(m.nombre)) = 'pagos'
           AND f.nombre IN ('Gestionar pagos', 'Confirmar pagos bancarios')
    ) <> 2 THEN
        RAISE EXCEPTION 'CU31: postcondicion de funciones RBAC incumplida';
    END IF;
END $$;

SELECT table_name, column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema='public'
   AND table_name IN ('pago','notificacion_pago')
 ORDER BY table_name, ordinal_position;

SELECT r.nombre AS rol, f.nombre AS funcion, a.nombre AS accion
  FROM public.rol_funcion rf
  JOIN public.rol r ON r.id=rf.rol_id
  JOIN public.funcion f ON f.id=rf.funcion_id
  JOIN public.accion a ON a.id=rf.accion_id
 WHERE f.nombre IN ('Gestionar pagos', 'Confirmar pagos bancarios')
 ORDER BY f.nombre, r.nombre;

SELECT c.relrowsecurity AS rls_habilitado,
       c.relforcerowsecurity AS rls_forzado,
       r.rolname AS propietario,
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
            THEN has_table_privilege('anon', c.oid, 'SELECT')
            ELSE NULL END AS anon_select,
       CASE WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated')
            THEN has_table_privilege('authenticated', c.oid, 'SELECT')
            ELSE NULL END AS authenticated_select
  FROM pg_class c
  JOIN pg_roles r ON r.oid = c.relowner
 WHERE c.oid = 'public.notificacion_pago'::regclass
;

SELECT current_user AS usuario_migracion,
       owner_role.rolname AS propietario_outbox,
       current_role_data.rolbypassrls AS usuario_bypassrls,
       (current_user = owner_role.rolname
        OR current_role_data.rolbypassrls) AS conexion_directa_habilitada
  FROM pg_class c
  JOIN pg_roles owner_role ON owner_role.oid = c.relowner
  JOIN pg_roles current_role_data ON current_role_data.rolname = current_user
 WHERE c.oid = 'public.notificacion_pago'::regclass;

SELECT c.conname, c.contype, pg_get_constraintdef(c.oid) AS definicion
  FROM pg_constraint c
 WHERE c.conrelid = 'public.notificacion_pago'::regclass
 ORDER BY c.contype, c.conname;

SELECT i.indexname, i.indexdef
  FROM pg_indexes i
 WHERE i.schemaname = 'public'
   AND i.tablename IN ('pago', 'notificacion_pago')
   AND i.indexname IN (
       'uq_pago_clave_idempotencia',
       'uq_notificacion_pago_proveedor_mensaje',
       'ix_notificacion_pago_cola'
   )
 ORDER BY i.tablename, i.indexname;

SELECT current_database() AS base_verificada,
       'v2-cu31'::text AS version_schema_objetivo;

COMMIT;
