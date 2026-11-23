-- Verificacion de Fase 3C.5 (solo lectura). Se ejecuta despues de aplicar la
-- migracion 20261122000000 y falla cerrado ante cualquier discrepancia.
--
-- Incluye las comprobaciones de 3C.2 que siguen siendo obligatorias (el
-- manifiesto apunta aqui como unico verificador) y agrega las de 3C.5.
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_tab text;
    v_estado text;
    v_huella text;
    v_role text;
    v_seq text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY[
        'dispositivo_push','notificacion','notificacion_entrega',
        'preferencia_notificacion','cita_evento',
        'dispositivo_push_revocacion'
    ] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'Verificacion Fase 3C.5: falta %', v_tab;
        END IF;
        -- RLS desactivado por instruccion del propietario.
        IF (SELECT relrowsecurity FROM pg_class
             WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'Verificacion Fase 3C.5: RLS habilitado en %', v_tab;
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
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta unicidad parcial del token activo';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_dispositivo_push_usuario_activo'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta indice usuario/activo';
    END IF;

    -- Bandeja.
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='uq_notificacion_evento_clave'
           AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta unicidad de evento_clave';
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
        RAISE EXCEPTION 'Verificacion Fase 3C.5: faltan indices de bandeja';
    END IF;

    -- Outbox: idempotencia, cola y lease.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_notificacion_entrega_notificacion_dispositivo'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta unicidad notificacion/dispositivo';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_notificacion_entrega_cola'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta indice de cola';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_notificacion_entrega_lease'
           AND indexdef LIKE '%WHERE%'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta indice parcial de lease';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='notificacion_entrega'
           AND column_name='lease_token'
           AND data_type='character varying'
           AND character_maximum_length=64
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: lease_token no es VARCHAR(64)';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO v_estado
      FROM pg_constraint WHERE conname='ck_notificacion_entrega_estado';
    IF v_estado IS NULL
       OR v_estado NOT LIKE '%OMITIDO%'
       OR v_estado NOT LIKE '%ACEPTADO_POR_FCM%'
       OR v_estado NOT LIKE '%INVALIDO%' THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: estados de entrega incompletos';
    END IF;

    -- Preferencias.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_preferencia_notificacion_usuario'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta unicidad de preferencia por usuario';
    END IF;

    -- Eventos de cita.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_cita_evento_clave_idempotencia'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: falta unicidad de idempotencia';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname='ck_cita_evento_reprogramacion'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname='ck_cita_evento_cambio_estado'
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: faltan restricciones de eventos';
    END IF;

    -- El token FCM no puede degradarse a un tipo sin longitud acotada.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='dispositivo_push'
           AND column_name='token_fcm'
           AND data_type='character varying'
           AND character_maximum_length=1024
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: token_fcm no es VARCHAR(1024)';
    END IF;

    -- Fase 3C.5.2: outbox durable de revocaciones globales.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_dispositivo_push_revocacion_dispositivo_huella'
    ) THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: falta unicidad dispositivo/huella';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_dispositivo_push_revocacion_estado'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_dispositivo_push_revocacion_huella'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='ck_dispositivo_push_revocacion_intentos'
    ) THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: faltan restricciones de revocacion';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO v_estado
      FROM pg_constraint
     WHERE conname='ck_dispositivo_push_revocacion_estado';
    IF v_estado IS NULL
       OR v_estado NOT LIKE '%PENDIENTE%'
       OR v_estado NOT LIKE '%COMPLETADA%'
       OR v_estado NOT LIKE '%NO_ENCONTRADO%'
       OR v_estado NOT LIKE '%RECHAZADA%'
       OR v_estado NOT LIKE '%FALLIDA%' THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: estados de revocacion incompletos';
    END IF;
    SELECT pg_get_constraintdef(oid) INTO v_huella
      FROM pg_constraint
     WHERE conname='ck_dispositivo_push_revocacion_huella'
       AND conrelid='public.dispositivo_push_revocacion'::regclass;

    IF v_huella IS NULL
       OR v_huella NOT LIKE '%^[0-9a-f]{64}$%' THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: la huella no valida SHA-256 hexadecimal';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_dispositivo_push_revocacion_cola'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_dispositivo_push_revocacion_lease'
           AND indexdef LIKE '%WHERE%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes
         WHERE schemaname='public'
           AND indexname='ix_dispositivo_push_revocacion_usuario'
    ) THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: faltan indices de revocacion';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema='public'
           AND table_name='dispositivo_push_revocacion'
           AND column_name IN ('token_fcm','token','fcm_token')
    ) THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: el outbox guarda el token original';
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
                             'cita_evento','dispositivo_push_revocacion')
           AND c.contype='f'
           AND confrelid NOT IN (
               to_regclass('public.usuario'), to_regclass('public.cita'),
               to_regclass('public.dispositivo_push'),
               to_regclass('public.notificacion')
           )
    ) THEN
        RAISE EXCEPTION 'Verificacion Fase 3C.5: FK inesperada entre bases';
    END IF;
    -- PUBLIC no debe conservar privilegios en la tabla.
    IF EXISTS (
        SELECT 1
          FROM pg_class c
          CROSS JOIN LATERAL aclexplode(
              COALESCE(c.relacl, acldefault('r', c.relowner))
          ) a
         WHERE c.oid = 'public.dispositivo_push_revocacion'::regclass
           AND a.grantee = 0
    ) THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: PUBLIC conserva privilegios en la tabla de revocaciones';
    END IF;

    FOR v_role IN SELECT rolname FROM pg_roles
                  WHERE rolname IN ('anon', 'authenticated')
    LOOP
        IF has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'SELECT')
           OR has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'INSERT')
           OR has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'UPDATE')
           OR has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'DELETE')
           OR has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'TRUNCATE')
           OR has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'REFERENCES')
           OR has_table_privilege(v_role, 'public.dispositivo_push_revocacion', 'TRIGGER')
        THEN
            RAISE EXCEPTION
                'Verificacion Fase 3C.5.2: % conserva privilegios en la tabla de revocaciones',
                v_role;
        END IF;
    END LOOP;

    v_seq := pg_get_serial_sequence(
        'public.dispositivo_push_revocacion', 'id'
    );

    IF v_seq IS NULL THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: falta la secuencia de identidad';
    END IF;

    -- PUBLIC tampoco debe acceder a la secuencia.
    IF EXISTS (
        SELECT 1
          FROM pg_class c
          CROSS JOIN LATERAL aclexplode(
              COALESCE(c.relacl, acldefault('S', c.relowner))
          ) a
         WHERE c.oid = v_seq::regclass
           AND a.grantee = 0
    ) THEN
        RAISE EXCEPTION
            'Verificacion Fase 3C.5.2: PUBLIC conserva privilegios en la secuencia';
    END IF;

    FOR v_role IN SELECT rolname FROM pg_roles
                  WHERE rolname IN ('anon', 'authenticated')
    LOOP
        IF has_sequence_privilege(v_role, v_seq, 'USAGE')
           OR has_sequence_privilege(v_role, v_seq, 'SELECT')
           OR has_sequence_privilege(v_role, v_seq, 'UPDATE')
        THEN
            RAISE EXCEPTION
                'Verificacion Fase 3C.5.2: % conserva privilegios en la secuencia',
                v_role;
        END IF;
    END LOOP;
END $$;

SELECT table_name, count(*) AS columnas
  FROM information_schema.columns
 WHERE table_schema='public'
   AND table_name IN (
       'dispositivo_push','notificacion','notificacion_entrega',
       'preferencia_notificacion','cita_evento',
       'dispositivo_push_revocacion'
   )
 GROUP BY table_name
 ORDER BY table_name;

ROLLBACK;
