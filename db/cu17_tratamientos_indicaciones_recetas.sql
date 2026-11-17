-- =========================================================
-- CU17 - REGISTRAR TRATAMIENTOS, INDICACIONES Y RECETAS
-- Archivo: db/cu17_tratamientos_indicaciones_recetas.sql
--
-- MOTIVO
--   Alinear Supabase con los modelos de CU17 (cambios pendientes verificados):
--     1. tratamiento usaba la columna `indicaciones`; el modelo define
--        `observaciones` (se renombra, no se duplica).
--     2. detalle_receta no tenía `presentacion` (VARCHAR(100) NULL).
--     3. La función RBAC de CU17 no existía en public.funcion:
--          Oftalmólogo   -> ESCRITURA
--          Administrador -> AMBAS
--          Paciente y Recepcionista -> SIN asignación
--     4. tratamiento.estado, receta.fecha_emision y receta.estado admitían
--        NULL en Supabase aunque el modelo SQLAlchemy las define
--        nullable=False (ya tienen default true / CURRENT_TIMESTAMP; aquí se
--        pasan a NOT NULL de forma idempotente).
--
--   public.indicacion NO se crea ni se modifica aquí: ya existe en el proyecto
--   real con id, consulta_clinica_id, descripcion, fecha_registro, PK y FK
--   ON DELETE CASCADE hacia consulta_clinica. Este script solo VERIFICA su
--   estructura (bloque 1) y falla de forma explícita si algo no coincidiera.
--
-- IDEMPOTENTE
--   Puede ejecutarse varias veces sin efectos adicionales (IF NOT EXISTS /
--   bloques DO con comprobaciones). Los ids NO se hardcodean: el módulo, la
--   función, el rol y la acción se resuelven por nombre / relación existente.
--
-- NO DESTRUCTIVO
--   No borra tablas, columnas ni datos. No crea triggers. No toca CU15/CU16,
--   CU18, CU19 ni CU22 (servicio_realizado queda intacto). public.indicacion
--   se deja exactamente igual (solo se verifica).
--
-- NOTA (rendimiento)
--   Los listados de CU17 filtran indicacion por consulta_clinica_id. Aquí no
--   se crea ningún índice porque indicacion no debe modificarse; si se
--   quisiera uno, aplicarlo aparte y a propósito:
--     CREATE INDEX IF NOT EXISTS ix_indicacion_consulta_clinica
--         ON public.indicacion (consulta_clinica_id);
--
-- CÓMO EJECUTARLO (controlado)
--   1. Abrir el SQL Editor de Supabase (o psql) apuntando al proyecto real.
--   2. Ejecutar el bloque completo; está envuelto en BEGIN/COMMIT.
--   3. Revisar las consultas de verificación del final.
--   4. Si algo no coincide, ejecutar ROLLBACK en lugar de COMMIT.
--
-- ESTADO VERIFICADO EN SUPABASE (solo lectura, antes de aplicar el script)
--   tratamiento      : 0 filas -> id, consulta_clinica_id, descripcion,
--                      indicaciones, fecha_inicio (date), fecha_fin (date),
--                      estado; FK ON DELETE CASCADE a consulta_clinica
--   receta           : 0 filas -> id, consulta_clinica_id, fecha_emision,
--                      observaciones, estado; FK ON DELETE CASCADE
--   detalle_receta   : 0 filas -> id, receta_id, medicamento, dosis,
--                      frecuencia, duracion, indicaciones; SIN presentacion
--   indicacion       : ya existente y correcta (0 filas) -> id,
--                      consulta_clinica_id, descripcion, fecha_registro;
--                      PK + FK ON DELETE CASCADE.
--   funcion          : CU15=17 y CU16=18 en modulo_id=3
--                      ('Pacientes e Historial Clínico'); CU17 ausente
--   Este bloque describe el estado ANTES de aplicar el script. Tras aplicarlo,
--   tratamiento expone `observaciones`, detalle_receta tiene `presentacion`,
--   las tres columnas del punto 4 quedan NOT NULL y la función de CU17 queda
--   otorgada al Oftalmólogo (ESCRITURA) y al Administrador (AMBAS).
-- =========================================================

BEGIN;

-- 1) public.indicacion: SOLO VERIFICACIÓN (no se crea ni se modifica) -----
--    La tabla ya existe en el proyecto real. Este bloque es de solo lectura:
--    comprueba tabla, columnas y FK ON DELETE CASCADE, y aborta la
--    transacción con un mensaje claro si algo no coincidiera. No añade ni
--    índices ni restricciones (la estructura queda intacta).
DO $$
DECLARE
    v_cols_faltantes text;
BEGIN
    IF to_regclass('public.indicacion') IS NULL THEN
        RAISE EXCEPTION 'CU17: public.indicacion no existe; crearla antes de aplicar este script';
    END IF;

    SELECT string_agg(esperada, ', ')
      INTO v_cols_faltantes
      FROM unnest(ARRAY['id', 'consulta_clinica_id', 'descripcion', 'fecha_registro'])
           AS esperada
     WHERE NOT EXISTS (
         SELECT 1
           FROM information_schema.columns AS c
          WHERE c.table_schema = 'public'
            AND c.table_name = 'indicacion'
            AND c.column_name = esperada
     );

    IF v_cols_faltantes IS NOT NULL THEN
        RAISE EXCEPTION USING MESSAGE =
            'CU17: public.indicacion no tiene las columnas esperadas. Faltan: '
            || v_cols_faltantes;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.indicacion'::regclass
           AND contype = 'f'
           AND confrelid = 'public.consulta_clinica'::regclass
           AND confdeltype = 'c'
    ) THEN
        RAISE EXCEPTION 'CU17: public.indicacion no tiene la FK ON DELETE CASCADE hacia consulta_clinica';
    END IF;
END $$;

-- 2) tratamiento.indicaciones -> tratamiento.observaciones ----------------
--    La tabla está vacía, por lo que no hay información clínica que migrar.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tratamiento'
          AND column_name = 'indicaciones'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tratamiento'
          AND column_name = 'observaciones'
    ) THEN
        ALTER TABLE public.tratamiento
            RENAME COLUMN indicaciones TO observaciones;
    END IF;
END $$;

-- 3) detalle_receta.presentacion (opcional, coherente con los VARCHAR(100)
--    de dosis / frecuencia / duracion) -----------------------------------
ALTER TABLE public.detalle_receta
    ADD COLUMN IF NOT EXISTS presentacion VARCHAR(100);

-- 4) NOT NULL de las columnas que el modelo declara nullable=False --------
--    tratamiento.estado, receta.fecha_emision y receta.estado ya tenían
--    default (true / CURRENT_TIMESTAMP) en Supabase, pero seguían admitiendo
--    NULL mientras el modelo SQLAlchemy las define nullable=False. Se fijan
--    aquí de forma idempotente: solo se actúa sobre las columnas que
--    information_schema siga marcando con is_nullable = 'YES'. Las tablas de
--    CU17 están vacías, así que no hay filas con NULL que bloqueen el ALTER.
DO $$
DECLARE
    v_col        text;
    v_tabla      text;
    v_columna    text;
    v_pendientes text;
BEGIN
    FOR v_col IN
        SELECT unnest(ARRAY[
            'tratamiento.estado',
            'receta.fecha_emision',
            'receta.estado'
        ])
    LOOP
        v_tabla   := split_part(v_col, '.', 1);
        v_columna := split_part(v_col, '.', 2);

        IF EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name   = v_tabla
               AND column_name  = v_columna
               AND is_nullable  = 'YES'
        ) THEN
            EXECUTE format(
                'ALTER TABLE public.%I ALTER COLUMN %I SET NOT NULL',
                v_tabla,
                v_columna
            );
        END IF;
    END LOOP;

    -- 4.1 Control final: si alguna columna no quedó NOT NULL se aborta toda
    --     la transacción (mismo criterio que en el bloque de RBAC, para no
    --     dejar el esquema a medias de forma silenciosa).
    SELECT string_agg(c.tabla || '.' || c.columna, ', ' ORDER BY c.tabla)
      INTO v_pendientes
      FROM (VALUES
                ('tratamiento', 'estado'),
                ('receta', 'fecha_emision'),
                ('receta', 'estado')
           ) AS c(tabla, columna)
     WHERE NOT EXISTS (
            SELECT 1
              FROM information_schema.columns AS ic
             WHERE ic.table_schema = 'public'
               AND ic.table_name   = c.tabla
               AND ic.column_name  = c.columna
               AND ic.is_nullable  = 'NO'
     );

    IF v_pendientes IS NOT NULL THEN
        RAISE EXCEPTION USING MESSAGE =
            'CU17: estas columnas no quedaron NOT NULL: ' || v_pendientes;
    END IF;
END $$;

-- 5) RBAC: función de CU17 y permiso del Oftalmólogo ----------------------
DO $$
DECLARE
    v_modulo_id   bigint;
    v_funcion_id  bigint;
    v_rol_id      bigint;
    v_accion_esc  bigint;
    v_accion_amb  bigint;
    v_accion_act  bigint;
    v_nombre      text := 'Registrar tratamientos, indicaciones y recetas';
BEGIN
    -- 5.1 Módulo de CU15/CU16 resuelto por nombre (no se hardcodea el id).
    SELECT modulo_id INTO v_modulo_id
      FROM public.funcion
     WHERE nombre = 'Registrar diagnóstico'
     ORDER BY id
     LIMIT 1;

    IF v_modulo_id IS NULL THEN
        SELECT modulo_id INTO v_modulo_id
          FROM public.funcion
         WHERE nombre = 'Registrar consulta clínica'
         ORDER BY id
         LIMIT 1;
    END IF;

    IF v_modulo_id IS NULL THEN
        RAISE EXCEPTION
            'CU17: no se encontró el módulo de "Registrar consulta clínica"/"Registrar diagnóstico"';
    END IF;

    -- 5.2 Función de CU17 dentro de ese módulo (por nombre).
    SELECT id INTO v_funcion_id
      FROM public.funcion
     WHERE modulo_id = v_modulo_id
       AND nombre = v_nombre
     ORDER BY id
     LIMIT 1;

    IF v_funcion_id IS NULL THEN
        INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
        VALUES (
            v_modulo_id,
            v_nombre,
            'Permite al oftalmólogo registrar tratamientos, indicaciones y recetas asociados a una consulta clínica.',
            TRUE
        )
        RETURNING id INTO v_funcion_id;
    END IF;

    -- 5.3 Acciones resueltas por nombre.
    SELECT id INTO v_accion_esc
      FROM public.accion WHERE upper(nombre) = 'ESCRITURA' ORDER BY id LIMIT 1;
    SELECT id INTO v_accion_amb
      FROM public.accion WHERE upper(nombre) = 'AMBAS' ORDER BY id LIMIT 1;

    IF v_accion_esc IS NULL THEN
        RAISE EXCEPTION 'CU17: no existe la acción ESCRITURA en public.accion';
    END IF;

    -- 5.4 Rol Oftalmólogo -> ESCRITURA (mínimo exigido por el requisito).
    --     El rol se resuelve por nombre sin acentos y con left() en lugar de
    --     LIKE, para que el script también sea ejecutable desde clientes que
    --     escanean placeholders (psycopg, asyncpg, JDBC).
    SELECT id INTO v_rol_id
      FROM public.rol
     WHERE left(lower(nombre), 6) = 'oftalm'
     ORDER BY id
     LIMIT 1;

    IF v_rol_id IS NOT NULL THEN
        SELECT accion_id INTO v_accion_act
          FROM public.rol_funcion
         WHERE rol_id = v_rol_id
           AND funcion_id = v_funcion_id
         ORDER BY id
         LIMIT 1;

        IF v_accion_act IS NULL THEN
            INSERT INTO public.rol_funcion (rol_id, funcion_id, accion_id)
            VALUES (v_rol_id, v_funcion_id, v_accion_esc);
        ELSIF v_accion_act <> v_accion_esc
              AND (v_accion_amb IS NULL OR v_accion_act <> v_accion_amb) THEN
            UPDATE public.rol_funcion
               SET accion_id = v_accion_esc
             WHERE rol_id = v_rol_id
               AND funcion_id = v_funcion_id;
        END IF;
    END IF;

    -- 5.5 Rol Administrador -> AMBAS, por coherencia con CU15/CU16 del mismo
    --     módulo (el Administrador ya tiene CU15 y CU16 en AMBAS).
    --     A Paciente y Recepcionista NO se les otorga CU17.
    --     'administrador' -> prefijo de 9 caracteres ('administr').
    SELECT id INTO v_rol_id
      FROM public.rol
     WHERE left(lower(nombre), 9) = 'administr'
     ORDER BY id
     LIMIT 1;

    IF v_rol_id IS NOT NULL AND v_accion_amb IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.rol_funcion
             WHERE rol_id = v_rol_id
               AND funcion_id = v_funcion_id
        ) THEN
            INSERT INTO public.rol_funcion (
                rol_id, funcion_id, accion_id, descripcion
            )
            VALUES (
                v_rol_id,
                v_funcion_id,
                v_accion_amb,
                'CU17 Registrar tratamientos, indicaciones y recetas: AMBAS'
            );
        END IF;
    END IF;

    -- 5.6 Control final: si la asignación no queda como se exige, se aborta
    --     toda la transacción (nada de fallos silenciosos).
    IF NOT EXISTS (
        SELECT 1
          FROM public.rol_funcion AS rf
          JOIN public.rol AS r ON r.id = rf.rol_id
         WHERE rf.funcion_id = v_funcion_id
           AND left(lower(r.nombre), 6) = 'oftalm'
           AND (rf.accion_id = v_accion_esc
                OR (v_accion_amb IS NOT NULL AND rf.accion_id = v_accion_amb))
    ) THEN
        RAISE EXCEPTION 'CU17: el rol Oftalmólogo no quedó con ESCRITURA (ni AMBAS) en la función de CU17';
    END IF;

    IF v_accion_amb IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM public.rol_funcion AS rf
          JOIN public.rol AS r ON r.id = rf.rol_id
         WHERE rf.funcion_id = v_funcion_id
           AND left(lower(r.nombre), 9) = 'administr'
           AND rf.accion_id = v_accion_amb
    ) THEN
        RAISE EXCEPTION 'CU17: el rol Administrador no quedó con AMBAS en la función de CU17';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.rol_funcion AS rf
          JOIN public.rol AS r ON r.id = rf.rol_id
         WHERE rf.funcion_id = v_funcion_id
           AND (left(lower(r.nombre), 8) = 'paciente'
                OR left(lower(r.nombre), 9) = 'recepcion')
    ) THEN
        RAISE EXCEPTION 'CU17: Paciente y Recepcionista no deben tener la función de CU17';
    END IF;
END $$;

-- =========================================================
-- VERIFICACIÓN (dentro de la misma transacción)
-- =========================================================

-- 6.1 Esquema final de las cuatro tablas de CU17.
SELECT table_name, column_name, data_type, character_maximum_length,
       is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('tratamiento', 'indicacion', 'receta', 'detalle_receta')
ORDER BY table_name, ordinal_position;

-- 6.2 Función de CU17 y a quién está otorgada.
SELECT f.id AS funcion_id, f.nombre AS funcion, f.modulo_id, m.nombre AS modulo,
       r.nombre AS rol, a.nombre AS accion
FROM public.funcion AS f
JOIN public.modulo AS m ON m.id = f.modulo_id
LEFT JOIN public.rol_funcion AS rf ON rf.funcion_id = f.id
LEFT JOIN public.rol AS r ON r.id = rf.rol_id
LEFT JOIN public.accion AS a ON a.id = rf.accion_id
WHERE f.nombre = 'Registrar tratamientos, indicaciones y recetas'
ORDER BY r.nombre;

-- 6.3 Debe devolver 0 filas (Paciente/Recepcionista no tienen CU17).
SELECT r.nombre AS rol_no_autorizado
FROM public.rol AS r
JOIN public.rol_funcion AS rf ON rf.rol_id = r.id
JOIN public.funcion AS f ON f.id = rf.funcion_id
WHERE f.nombre = 'Registrar tratamientos, indicaciones y recetas'
  AND (left(lower(r.nombre), 8) = 'paciente'
       OR left(lower(r.nombre), 9) = 'recepcion');

-- 6.4 Las tablas de CU17 deben seguir vacías (no se insertan datos clínicos).
SELECT 'tratamiento' AS tabla, count(*) AS filas FROM public.tratamiento
UNION ALL
SELECT 'indicacion', count(*) FROM public.indicacion
UNION ALL
SELECT 'receta', count(*) FROM public.receta
UNION ALL
SELECT 'detalle_receta', count(*) FROM public.detalle_receta;

-- 6.5 Las tres columnas del punto 4 deben salir con is_nullable = 'NO'
--     (antes permitían NULL aunque el modelo las declara nullable=False).
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND ((table_name = 'tratamiento' AND column_name = 'estado')
    OR (table_name = 'receta' AND column_name IN ('fecha_emision', 'estado')))
ORDER BY table_name, column_name;

COMMIT;

-- Si algo no coincide, en lugar de COMMIT ejecutar:
-- ROLLBACK;

