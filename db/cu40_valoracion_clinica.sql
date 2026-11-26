-- =========================================================================
-- CU40: Registrar y Actualizar valoración de la clínica (Móvil)
-- Script SQL idempotente para bases de datos PostgreSQL / Supabase (tenants).
--
-- Diseño simplificado (Opción A):
--   - Se crea ÚNICAMENTE la tabla `public.valoracion_clinica`.
--   - NO se modifica el esquema de `public.notificacion`,
--     `public.notificacion_entrega`, `public.dispositivo_push` ni
--     `public.preferencia_notificacion`.
--   - Los avisos post-atención (fin de consulta / emisión de receta) reutilizan
--     `public.notificacion` existente con:
--       * tipo = 'ACTUALIZACION_OPERATIVA' (ya permitido por ck_notificacion_tipo)
--       * evento_clave = 'VALORACION_CONSULTA:<consulta_clinica_id>' (UNIQUE)
--       * datos JSON = {"accion": "VALORAR_ATENCION", "consulta_clinica_id": ...}
--
-- Aislamiento multitenant:
--   Cada empresa/clínica posee su propia base de datos tenant resuelta en el
--   servidor mediante el JWT tenant, garantizando que las valoraciones de una
--   empresa jamás se mezclen con otras empresas.
-- =========================================================================

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

-- -------------------------------------------------------------------------
-- 1. Única tabla nueva: public.valoracion_clinica
--    Cada consulta clínica admite como máximo UNA valoración
--    (`uq_valoracion_clinica_consulta`), actualizable sin crear duplicados.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.valoracion_clinica (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paciente_id BIGINT NOT NULL,
    consulta_clinica_id BIGINT NOT NULL,
    oftalmologo_id BIGINT NOT NULL,
    puntuacion SMALLINT NOT NULL,
    comentario TEXT,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_valoracion_clinica_paciente FOREIGN KEY (paciente_id)
        REFERENCES public.paciente(id) ON DELETE RESTRICT,
    CONSTRAINT fk_valoracion_clinica_consulta FOREIGN KEY (consulta_clinica_id)
        REFERENCES public.consulta_clinica(id) ON DELETE RESTRICT,
    CONSTRAINT fk_valoracion_clinica_oftalmologo FOREIGN KEY (oftalmologo_id)
        REFERENCES public.oftalmologo(id) ON DELETE RESTRICT,
    CONSTRAINT uq_valoracion_clinica_consulta UNIQUE (consulta_clinica_id),
    CONSTRAINT ck_valoracion_clinica_puntuacion CHECK (puntuacion BETWEEN 1 AND 5),
    CONSTRAINT ck_valoracion_clinica_comentario_len CHECK (
        comentario IS NULL OR char_length(comentario) <= 1000
    )
);

CREATE INDEX IF NOT EXISTS ix_valoracion_clinica_paciente_fecha
    ON public.valoracion_clinica (paciente_id, fecha_actualizacion DESC);

CREATE INDEX IF NOT EXISTS ix_valoracion_clinica_oftalmologo
    ON public.valoracion_clinica (oftalmologo_id);

COMMENT ON TABLE public.valoracion_clinica IS
    'CU40: Valoraciones de pacientes sobre la atención médica y la clínica (una por consulta clínica, actualizable sin duplicados).';

-- -------------------------------------------------------------------------
-- 2. Seguridad Supabase / PostgreSQL (RLS habilitado, sin acceso Data API)
-- -------------------------------------------------------------------------
ALTER TABLE public.valoracion_clinica ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.valoracion_clinica NO FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.valoracion_clinica FROM PUBLIC;

DO $security$
DECLARE
    v_role text;
    v_seq text;
BEGIN
    FOR v_role IN SELECT rolname FROM pg_roles
                  WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.valoracion_clinica FROM %I',
            v_role
        );
    END LOOP;

    v_seq := pg_get_serial_sequence('public.valoracion_clinica', 'id');
    IF v_seq IS NOT NULL THEN
        EXECUTE format('REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM PUBLIC', v_seq);
        FOR v_role IN SELECT rolname FROM pg_roles
                      WHERE rolname IN ('anon', 'authenticated')
        LOOP
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE %s FROM %I',
                v_seq, v_role
            );
        END LOOP;
    END IF;
END
$security$;

-- -------------------------------------------------------------------------
-- 3. Catálogo RBAC opcional (idempotente)
-- -------------------------------------------------------------------------
DO $rbac$
DECLARE
    v_modulo bigint;
    v_ambas bigint;
    v_paciente bigint;
    v_funcion bigint;
BEGIN
    SELECT min(id) INTO v_modulo FROM public.modulo
     WHERE lower(trim(nombre)) = 'autenticación y seguridad' AND estado IS TRUE;
    SELECT min(id) INTO v_ambas FROM public.accion
     WHERE upper(trim(nombre)) = 'AMBAS' AND estado IS TRUE;
    SELECT min(id) INTO v_paciente FROM public.rol
     WHERE lower(trim(nombre)) = 'paciente' AND estado IS TRUE;

    IF v_modulo IS NOT NULL AND v_ambas IS NOT NULL AND v_paciente IS NOT NULL THEN
        INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
        VALUES (
            v_modulo,
            'Registrar y actualizar valoración de la clínica',
            'CU40: Permite al paciente móvil registrar y actualizar valoraciones de sus atenciones médicas',
            TRUE
        )
        ON CONFLICT (modulo_id, nombre) DO NOTHING;

        SELECT id INTO v_funcion FROM public.funcion
         WHERE modulo_id = v_modulo
           AND nombre = 'Registrar y actualizar valoración de la clínica'
           AND estado IS TRUE;

        IF v_funcion IS NOT NULL THEN
            INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id, descripcion)
            VALUES (
                v_paciente,
                v_funcion,
                v_ambas,
                'Asignación inicial CU40 para pacientes móviles'
            )
            ON CONFLICT (rol_id, funcion_id) DO NOTHING;
        END IF;
    END IF;
END
$rbac$;

COMMIT;
