-- Fase 3C.2: persistencia de notificaciones push (base tenant).
--
-- Crea dispositivos, bandeja, outbox por dispositivo, preferencias y eventos
-- estructurados de cita. No crea endpoints, no envia FCM y no instrumenta los
-- servicios de citas.
--
-- Transaccion controlada por tenant_migrator.py. No incluir BEGIN/COMMIT.
-- RLS permanece DESACTIVADO por la instruccion vigente del propietario; el
-- acceso queda restringido por REVOKE a PUBLIC/anon/authenticated, igual que
-- las tablas de CU36.
--
-- El token FCM en claro vive SOLO aqui (base tenant), porque el worker lo
-- necesitara para enviar. Nunca se copia al Control Plane: alli solo se guarda
-- una huella SHA-256.

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

DO $preflight$
DECLARE
    v_missing text;
    v_existing text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.usuario'), ('public.cita'), ('public.rol'),
          ('public.bitacora')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'Fase 3C.2: faltan prerrequisitos: %', v_missing;
    END IF;

    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_existing
      FROM (VALUES
          ('public.dispositivo_push'), ('public.notificacion'),
          ('public.notificacion_entrega'), ('public.preferencia_notificacion'),
          ('public.cita_evento')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NOT NULL;
    IF v_existing IS NOT NULL THEN
        RAISE EXCEPTION
            'Fase 3C.2: instalacion parcial o no registrada: %', v_existing;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
              ('public.usuario'::regclass, 'id'),
              ('public.cita'::regclass, 'id')
          ) x(tabla, columna)
          LEFT JOIN pg_attribute a
            ON a.attrelid=x.tabla AND a.attname=x.columna
           AND a.attnum>0 AND NOT a.attisdropped
         WHERE a.atttypid IS DISTINCT FROM 'pg_catalog.int8'::regtype
    ) THEN
        RAISE EXCEPTION 'Fase 3C.2: las llaves referenciadas deben ser BIGINT';
    END IF;
END
$preflight$;

-- ---------------------------------------------------------------------------
-- dispositivo_push: token FCM operativo por usuario del tenant.
-- Revocacion logica; el token activo es unico dentro de la base tenant.
-- ---------------------------------------------------------------------------
CREATE TABLE public.dispositivo_push (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id BIGINT NOT NULL,
    token_fcm VARCHAR(1024) NOT NULL,
    plataforma VARCHAR(10) NOT NULL,
    instalacion_id VARCHAR(100),
    app_version VARCHAR(50),
    nombre_dispositivo VARCHAR(120),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_registro TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_ultimo_uso TIMESTAMPTZ,
    revocado_en TIMESTAMPTZ,
    CONSTRAINT fk_dispositivo_push_usuario FOREIGN KEY (usuario_id)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    CONSTRAINT ck_dispositivo_push_plataforma CHECK (
        plataforma IN ('ANDROID','IOS','WEB')
    ),
    CONSTRAINT ck_dispositivo_push_revocacion CHECK (
        (activo AND revocado_en IS NULL) OR (NOT activo)
    )
);

CREATE UNIQUE INDEX uq_dispositivo_push_token_activo
    ON public.dispositivo_push (token_fcm)
    WHERE activo;
CREATE INDEX ix_dispositivo_push_usuario_activo
    ON public.dispositivo_push (usuario_id, activo);

-- ---------------------------------------------------------------------------
-- notificacion: bandeja independiente del push y de los dispositivos.
-- ---------------------------------------------------------------------------
CREATE TABLE public.notificacion (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id BIGINT NOT NULL,
    tipo VARCHAR(30) NOT NULL,
    evento_clave VARCHAR(150) NOT NULL,
    titulo VARCHAR(150) NOT NULL,
    cuerpo TEXT NOT NULL,
    datos JSON,
    cita_id BIGINT,
    leida BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_lectura TIMESTAMPTZ,
    cancelada_en TIMESTAMPTZ,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_notificacion_usuario FOREIGN KEY (usuario_id)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    -- La cita puede retirarse administrativamente; la notificacion ya emitida
    -- conserva su evidencia y solo pierde el enlace.
    CONSTRAINT fk_notificacion_cita FOREIGN KEY (cita_id)
        REFERENCES public.cita(id) ON DELETE SET NULL,
    CONSTRAINT uq_notificacion_evento_clave UNIQUE (evento_clave),
    CONSTRAINT ck_notificacion_tipo CHECK (
        tipo IN ('CITA_CREADA','CITA_CONFIRMADA','CITA_REPROGRAMADA',
                 'CITA_CANCELADA','RECORDATORIO_CITA',
                 'ACTUALIZACION_OPERATIVA')
    ),
    CONSTRAINT ck_notificacion_lectura_coherente CHECK (
        (leida AND fecha_lectura IS NOT NULL)
        OR (NOT leida AND fecha_lectura IS NULL)
    )
);

CREATE INDEX ix_notificacion_usuario_fecha
    ON public.notificacion (usuario_id, fecha_creacion DESC);
CREATE INDEX ix_notificacion_usuario_leida
    ON public.notificacion (usuario_id, leida);

-- ---------------------------------------------------------------------------
-- notificacion_entrega: outbox por dispositivo; evidencia historica.
-- ACEPTADO_POR_FCM no significa entregado ni leido.
-- ---------------------------------------------------------------------------
CREATE TABLE public.notificacion_entrega (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notificacion_id BIGINT NOT NULL,
    dispositivo_id BIGINT NOT NULL,
    estado VARCHAR(25) NOT NULL DEFAULT 'PENDIENTE',
    proveedor VARCHAR(20) NOT NULL DEFAULT 'FCM',
    proveedor_mensaje_id VARCHAR(255),
    intentos SMALLINT NOT NULL DEFAULT 0,
    proximo_intento TIMESTAMPTZ,
    bloqueado_hasta TIMESTAMPTZ,
    codigo_ultimo_error VARCHAR(100),
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_aceptado TIMESTAMPTZ,
    CONSTRAINT fk_notificacion_entrega_notificacion FOREIGN KEY (notificacion_id)
        REFERENCES public.notificacion(id) ON DELETE RESTRICT,
    CONSTRAINT fk_notificacion_entrega_dispositivo FOREIGN KEY (dispositivo_id)
        REFERENCES public.dispositivo_push(id) ON DELETE RESTRICT,
    CONSTRAINT uq_notificacion_entrega_notificacion_dispositivo
        UNIQUE (notificacion_id, dispositivo_id),
    CONSTRAINT ck_notificacion_entrega_estado CHECK (
        estado IN ('PENDIENTE','PROCESANDO','ACEPTADO_POR_FCM','FALLIDO',
                   'INVALIDO')
    ),
    CONSTRAINT ck_notificacion_entrega_proveedor CHECK (proveedor = 'FCM'),
    CONSTRAINT ck_notificacion_entrega_intentos CHECK (intentos >= 0)
);

CREATE UNIQUE INDEX uq_notificacion_entrega_proveedor_mensaje
    ON public.notificacion_entrega (proveedor_mensaje_id)
    WHERE proveedor_mensaje_id IS NOT NULL;
CREATE INDEX ix_notificacion_entrega_cola
    ON public.notificacion_entrega (estado, proximo_intento);

-- ---------------------------------------------------------------------------
-- preferencia_notificacion: una fila por usuario. Desactivar push no oculta
-- ni borra notificaciones de la bandeja.
-- ---------------------------------------------------------------------------
CREATE TABLE public.preferencia_notificacion (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    usuario_id BIGINT NOT NULL,
    push_habilitado BOOLEAN NOT NULL DEFAULT TRUE,
    citas_creadas BOOLEAN NOT NULL DEFAULT TRUE,
    citas_confirmadas BOOLEAN NOT NULL DEFAULT TRUE,
    citas_reprogramadas BOOLEAN NOT NULL DEFAULT TRUE,
    citas_canceladas BOOLEAN NOT NULL DEFAULT TRUE,
    recordatorios BOOLEAN NOT NULL DEFAULT TRUE,
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_preferencia_notificacion_usuario FOREIGN KEY (usuario_id)
        REFERENCES public.usuario(id) ON DELETE RESTRICT,
    CONSTRAINT uq_preferencia_notificacion_usuario UNIQUE (usuario_id)
);

-- ---------------------------------------------------------------------------
-- cita_evento: evidencia estructurada de creacion, cambio de estado y
-- reprogramacion real. No contiene datos del paciente ni texto clinico.
-- REPROGRAMADA es un tipo de evento, nunca un estado de cita.
-- ---------------------------------------------------------------------------
CREATE TABLE public.cita_evento (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cita_id BIGINT NOT NULL,
    tipo VARCHAR(30) NOT NULL,
    estado_anterior VARCHAR(20),
    estado_nuevo VARCHAR(20),
    fecha_anterior DATE,
    hora_anterior TIME,
    fecha_nueva DATE,
    hora_nueva TIME,
    fecha_evento TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor_usuario_id BIGINT,
    clave_idempotencia VARCHAR(150) NOT NULL,
    CONSTRAINT fk_cita_evento_cita FOREIGN KEY (cita_id)
        REFERENCES public.cita(id) ON DELETE RESTRICT,
    -- El evento debe sobrevivir aunque el actor sea dado de baja.
    CONSTRAINT fk_cita_evento_actor FOREIGN KEY (actor_usuario_id)
        REFERENCES public.usuario(id) ON DELETE SET NULL,
    CONSTRAINT uq_cita_evento_clave_idempotencia UNIQUE (clave_idempotencia),
    CONSTRAINT ck_cita_evento_tipo CHECK (
        tipo IN ('CITA_CREADA','CITA_ESTADO_CAMBIADO','CITA_REPROGRAMADA')
    ),
    CONSTRAINT ck_cita_evento_estado_anterior CHECK (
        estado_anterior IS NULL
        OR estado_anterior IN ('PROGRAMADA','CONFIRMADA','EN_ESPERA','ATENDIDA',
                               'CANCELADA','NO_ASISTIO')
    ),
    CONSTRAINT ck_cita_evento_estado_nuevo CHECK (
        estado_nuevo IS NULL
        OR estado_nuevo IN ('PROGRAMADA','CONFIRMADA','EN_ESPERA','ATENDIDA',
                            'CANCELADA','NO_ASISTIO')
    ),
    CONSTRAINT ck_cita_evento_creacion CHECK (
        tipo <> 'CITA_CREADA'
        OR (estado_anterior IS NULL AND fecha_anterior IS NULL
            AND hora_anterior IS NULL)
    ),
    CONSTRAINT ck_cita_evento_cambio_estado CHECK (
        tipo <> 'CITA_ESTADO_CAMBIADO'
        OR (estado_anterior IS NOT NULL AND estado_nuevo IS NOT NULL
            AND estado_anterior <> estado_nuevo)
    ),
    CONSTRAINT ck_cita_evento_reprogramacion CHECK (
        tipo <> 'CITA_REPROGRAMADA'
        OR (fecha_anterior IS NOT NULL AND hora_anterior IS NOT NULL
            AND fecha_nueva IS NOT NULL AND hora_nueva IS NOT NULL
            AND (fecha_anterior <> fecha_nueva
                 OR hora_anterior <> hora_nueva))
    )
);

CREATE INDEX ix_cita_evento_cita_fecha
    ON public.cita_evento (cita_id, fecha_evento);
CREATE INDEX ix_cita_evento_tipo_fecha
    ON public.cita_evento (tipo, fecha_evento);

COMMENT ON TABLE public.dispositivo_push IS
    'Fase 3C.2: dispositivo del usuario tenant; token FCM operativo y secreto.';
COMMENT ON COLUMN public.dispositivo_push.token_fcm IS
    'Secreto operativo: prohibido loguear, serializar o copiar al Control Plane.';
COMMENT ON TABLE public.notificacion IS
    'Fase 3C.2: bandeja por usuario; no depende de dispositivos ni de push.';
COMMENT ON COLUMN public.notificacion.datos IS
    'JSON minimo de navegacion; prohibido incluir datos clinicos o PII.';
COMMENT ON TABLE public.notificacion_entrega IS
    'Fase 3C.2: outbox por dispositivo; evidencia historica, sin borrado en cascada.';
COMMENT ON TABLE public.preferencia_notificacion IS
    'Fase 3C.2: preferencias por usuario; desactivar push no oculta la bandeja.';
COMMENT ON TABLE public.cita_evento IS
    'Fase 3C.2: eventos estructurados de cita; reemplaza bitacora.descripcion.';

REVOKE ALL PRIVILEGES ON TABLE public.dispositivo_push,
    public.notificacion, public.notificacion_entrega,
    public.preferencia_notificacion, public.cita_evento FROM PUBLIC;

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
            'REVOKE ALL PRIVILEGES ON TABLE public.dispositivo_push, public.notificacion, public.notificacion_entrega, public.preferencia_notificacion, public.cita_evento FROM %I',
            v_role
        );
    END LOOP;
    FOR v_tab IN SELECT unnest(ARRAY[
        'dispositivo_push','notificacion','notificacion_entrega',
        'preferencia_notificacion','cita_evento'
    ])
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
    FOREACH v_tab IN ARRAY ARRAY[
        'dispositivo_push','notificacion','notificacion_entrega',
        'preferencia_notificacion','cita_evento'
    ] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'Fase 3C.2: falta tabla %', v_tab;
        END IF;
        -- RLS desactivado por instruccion del propietario.
        IF (SELECT relrowsecurity FROM pg_class
             WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'Fase 3C.2: RLS no debe habilitarse en %', v_tab;
        END IF;
        FOR v_role IN SELECT rolname FROM pg_roles
                      WHERE rolname IN ('anon','authenticated')
        LOOP
            IF has_table_privilege(v_role, 'public.' || v_tab, 'SELECT')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'INSERT')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'UPDATE')
               OR has_table_privilege(v_role, 'public.' || v_tab, 'DELETE') THEN
                RAISE EXCEPTION 'Fase 3C.2: % conserva privilegios en %',
                    v_role, v_tab;
            END IF;
        END LOOP;
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_dispositivo_push_token_activo'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Fase 3C.2: falta unicidad parcial del token activo';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_notificacion_evento_clave'
         AND indexdef LIKE '%UNIQUE%'
    ) THEN
        RAISE EXCEPTION 'Fase 3C.2: falta unicidad de evento_clave';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname='uq_notificacion_entrega_notificacion_dispositivo'
    ) THEN
        RAISE EXCEPTION 'Fase 3C.2: falta unicidad notificacion/dispositivo';
    END IF;
END
$postflight$;
