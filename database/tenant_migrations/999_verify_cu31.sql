-- Solo lectura. Verifica el contrato final; no compara ciegamente el numero
-- total con postgres porque el tenant conserva servicios_oftalmologicos.
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_faltantes text;
    v_asignaciones integer;
    v_predicado text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre)
      INTO v_faltantes
      FROM (VALUES
          ('public.pago'),
          ('public.pago_detalle'),
          ('public.notificacion_pago'),
          ('public.servicio_realizado'),
          ('public.servicios_oftalmologicos')
      ) AS x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_faltantes IS NOT NULL THEN
        RAISE EXCEPTION 'Verificacion CU31: faltan tablas: %', v_faltantes;
    END IF;

    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'pago') <> 11
       OR (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'pago_detalle') <> 4
       OR (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'notificacion_pago') <> 16 THEN
        RAISE EXCEPTION 'Verificacion CU31: cantidad de columnas incompatible';
    END IF;

    IF EXISTS (
        WITH esperada(tabla, columna, tipo, not_null) AS (
            VALUES
                ('pago', 'id', 'bigint', TRUE),
                ('pago', 'fecha_creacion', 'timestamp with time zone', TRUE),
                ('pago', 'fecha_hora_pago', 'timestamp with time zone', FALSE),
                ('pago', 'monto', 'numeric(12,2)', TRUE),
                ('pago', 'moneda', 'character varying(3)', TRUE),
                ('pago', 'metodo_pago', 'character varying(30)', TRUE),
                ('pago', 'estado', 'character varying(20)', TRUE),
                ('pago', 'pasarela', 'character varying(50)', FALSE),
                ('pago', 'referencia_transaccion', 'character varying(255)', FALSE),
                ('pago', 'observaciones', 'text', FALSE),
                ('pago', 'clave_idempotencia', 'character varying(100)', FALSE),
                ('pago_detalle', 'id', 'bigint', TRUE),
                ('pago_detalle', 'pago_id', 'bigint', TRUE),
                ('pago_detalle', 'servicio_realizado_id', 'bigint', TRUE),
                ('pago_detalle', 'monto_aplicado', 'numeric(12,2)', TRUE)
        )
        SELECT 1
          FROM esperada e
          LEFT JOIN pg_class t ON t.relname = e.tabla
          LEFT JOIN pg_namespace n ON n.oid = t.relnamespace
                                   AND n.nspname = 'public'
          LEFT JOIN pg_attribute a ON a.attrelid = t.oid
                                  AND a.attname = e.columna
                                  AND a.attnum > 0
                                  AND NOT a.attisdropped
         WHERE n.oid IS NULL OR a.attname IS NULL
            OR format_type(a.atttypid, a.atttypmod) <> e.tipo
            OR a.attnotnull <> e.not_null
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: contrato de columnas de pagos incompatible';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'public.pago'::regclass
           AND attname = 'id' AND attidentity = 'a'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_attribute
         WHERE attrelid = 'public.pago_detalle'::regclass
           AND attname = 'id' AND attidentity = 'a'
    ) OR NOT EXISTS (
        SELECT 1
          FROM pg_attrdef d JOIN pg_attribute a
            ON a.attrelid = d.adrelid AND a.attnum = d.adnum
         WHERE d.adrelid = 'public.pago'::regclass
           AND a.attname = 'fecha_creacion'
           AND lower(pg_get_expr(d.adbin, d.adrelid)) IN ('current_timestamp', 'now()')
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: identity/defaults de pagos incompatibles';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago'::regclass
           AND c.conname = 'uq_pago_referencia_transaccion' AND c.contype = 'u'
           AND pg_get_constraintdef(c.oid) = 'UNIQUE (referencia_transaccion)'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago'::regclass
           AND c.conname = 'ck_pago_monto_positivo' AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%monto >%0%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago'::regclass
           AND c.conname = 'ck_pago_moneda' AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%upper%moneda%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago'::regclass
           AND c.conname = 'ck_pago_metodo' AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%EFECTIVO%TARJETA%TRANSFERENCIA%QR%OTRO%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago'::regclass
           AND c.conname = 'ck_pago_estado' AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%PENDIENTE%APROBADO%RECHAZADO%ANULADO%REEMBOLSADO%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago_detalle'::regclass
           AND c.conname = 'uq_pago_detalle_pago_servicio' AND c.contype = 'u'
           AND pg_get_constraintdef(c.oid) = 'UNIQUE (pago_id, servicio_realizado_id)'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago_detalle'::regclass
           AND c.conname = 'ck_pago_detalle_monto_positivo' AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%monto_aplicado >%0%'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: restricciones de pagos incompatibles';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago_detalle'::regclass
           AND c.contype = 'f' AND c.confrelid = 'public.pago'::regclass
           AND c.confdeltype = 'c'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.pago_detalle'::regclass
           AND c.contype = 'f'
           AND c.confrelid = 'public.servicio_realizado'::regclass
           AND c.confdeltype = 'r'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'public.notificacion_pago'::regclass
           AND c.contype = 'f' AND c.confrelid = 'public.pago'::regclass
           AND c.confdeltype = 'r'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: claves foraneas incompatibles';
    END IF;

    IF to_regclass('public.idx_pago_referencia_transaccion') IS NOT NULL THEN
        RAISE EXCEPTION 'Verificacion CU31: indice redundante no fue retirado';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_index
         WHERE indexrelid = to_regclass('public.idx_pago_estado')
           AND indrelid = 'public.pago'::regclass
           AND NOT indisunique AND indisvalid
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_index
         WHERE indexrelid = to_regclass('public.idx_pago_detalle_pago')
           AND indrelid = 'public.pago_detalle'::regclass
           AND NOT indisunique AND indisvalid
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_index
         WHERE indexrelid = to_regclass('public.idx_pago_detalle_servicio_realizado')
           AND indrelid = 'public.pago_detalle'::regclass
           AND NOT indisunique AND indisvalid
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: indices base de pagos ausentes o invalidos';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_index
         WHERE indexrelid = 'public.uq_pago_clave_idempotencia'::regclass
           AND indisunique AND indisvalid
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_index
         WHERE indexrelid = 'public.uq_notificacion_pago_proveedor_mensaje'::regclass
           AND indisunique AND indisvalid
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_index
         WHERE indexrelid = 'public.ix_notificacion_pago_cola'::regclass
           AND NOT indisunique AND indisvalid
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: indices CU31 ausentes o invalidos';
    END IF;

    SELECT regexp_replace(
               lower(pg_get_expr(i.indpred, i.indrelid)), '[\s()]', '', 'g'
           )
      INTO v_predicado
      FROM pg_index i
     WHERE i.indexrelid = 'public.uq_pago_clave_idempotencia'::regclass;
    IF v_predicado IS DISTINCT FROM 'clave_idempotenciaisnotnull' THEN
        RAISE EXCEPTION 'Verificacion CU31: predicado idempotente incompatible';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_class
         WHERE oid = 'public.notificacion_pago'::regclass
           AND relrowsecurity IS TRUE AND relforcerowsecurity IS FALSE
    ) OR EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public' AND tablename = 'notificacion_pago'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: RLS del outbox incompatible';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_roles r
         WHERE r.rolname IN ('anon', 'authenticated')
           AND has_table_privilege(r.rolname, 'public.notificacion_pago',
               'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    ) THEN
        RAISE EXCEPTION 'Verificacion CU31: rol Data API con acceso al outbox';
    END IF;

    SELECT count(*) INTO v_asignaciones
      FROM public.rol_funcion rf
      JOIN public.rol r ON r.id = rf.rol_id
      JOIN public.funcion f ON f.id = rf.funcion_id
      JOIN public.modulo m ON m.id = f.modulo_id
      JOIN public.accion a ON a.id = rf.accion_id
     WHERE lower(trim(m.nombre)) = 'pagos'
       AND (
           (f.nombre = 'Gestionar pagos'
            AND lower(trim(r.nombre)) IN ('administrador', 'recepcionista')
            AND upper(trim(a.nombre)) = 'AMBAS')
        OR (f.nombre = 'Confirmar pagos bancarios'
            AND lower(trim(r.nombre)) = 'administrador'
            AND upper(trim(a.nombre)) = 'AMBAS')
        OR (f.nombre = 'Confirmar pagos bancarios'
            AND lower(trim(r.nombre)) = 'recepcionista'
            AND upper(trim(a.nombre)) = 'ESCRITURA')
       );
    IF v_asignaciones <> 4 THEN
        RAISE EXCEPTION 'Verificacion CU31: asignaciones RBAC iniciales incompletas';
    END IF;
END $$;

SELECT current_database() AS base,
       current_user AS rol_verificacion,
       current_setting('transaction_read_only') AS solo_lectura,
       (SELECT count(*) FROM information_schema.tables
         WHERE table_schema = 'public' AND table_type = 'BASE TABLE') AS tablas_public,
       (SELECT count(*) FROM pg_indexes WHERE schemaname = 'public') AS indices_public,
       (SELECT count(*) FROM public.servicios_oftalmologicos) AS filas_catalogo_historico;

SELECT table_name, column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema = 'public'
   AND table_name IN ('pago', 'pago_detalle', 'notificacion_pago')
 ORDER BY table_name, ordinal_position;

ROLLBACK;
