-- =========================================================
-- CU18 - REGISTRAR RESULTADOS DE EXAMENES OFTALMOLOGICOS
-- Archivo: db/cu18_examenes_resultados.sql
--
-- Alinea las dos tablas ya existentes (no las crea ni las recrea), conserva
-- la cardinalidad examen 1 -> N resultados y configura el permiso de CU18:
--   Oftalmologo   -> ESCRITURA
--   Administrador -> AMBAS
--   Paciente / Recepcionista -> sin asignacion
--
-- No borra datos, no crea triggers, no toca RLS ni integra Storage.
-- archivo_url permanece nullable y no se agrega UNIQUE sobre examen_id.
-- =========================================================

BEGIN;

SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

-- 1) Precondiciones: tablas, columnas y FK reales ------------------------
DO $$
DECLARE
    v_tabla text;
    v_columnas_faltantes text;
BEGIN
    IF to_regclass('public.examen_oftalmologico') IS NULL THEN
        RAISE EXCEPTION 'CU18: public.examen_oftalmologico no existe';
    END IF;
    IF to_regclass('public.resultado_examen') IS NULL THEN
        RAISE EXCEPTION 'CU18: public.resultado_examen no existe';
    END IF;

    FOR v_tabla IN SELECT unnest(ARRAY[
        'examen_oftalmologico', 'resultado_examen'
    ]) LOOP
        SELECT string_agg(e.columna, ', ' ORDER BY e.columna)
          INTO v_columnas_faltantes
          FROM (
              SELECT unnest(
                  CASE v_tabla
                      WHEN 'examen_oftalmologico' THEN ARRAY[
                          'id', 'consulta_clinica_id', 'nombre_examen',
                          'fecha_solicitud', 'observaciones', 'estado'
                      ]
                      ELSE ARRAY[
                          'id', 'examen_id', 'fecha_resultado', 'resultado',
                          'archivo_url', 'estado'
                      ]
                  END
              ) AS columna
          ) AS e
         WHERE NOT EXISTS (
             SELECT 1
               FROM information_schema.columns AS c
              WHERE c.table_schema = 'public'
                AND c.table_name = v_tabla
                AND c.column_name = e.columna
         );

        IF v_columnas_faltantes IS NOT NULL THEN
            RAISE EXCEPTION USING MESSAGE =
                format('CU18: public.%I carece de columnas: %s',
                       v_tabla, v_columnas_faltantes);
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.examen_oftalmologico'::regclass
           AND contype = 'f'
           AND confrelid = 'public.consulta_clinica'::regclass
           AND confdeltype = 'c'
    ) THEN
        RAISE EXCEPTION 'CU18: falta FK ON DELETE CASCADE de examen_oftalmologico a consulta_clinica';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'public.resultado_examen'::regclass
           AND contype = 'f'
           AND confrelid = 'public.examen_oftalmologico'::regclass
           AND confdeltype = 'c'
    ) THEN
        RAISE EXCEPTION 'CU18: falta FK ON DELETE CASCADE de resultado_examen a examen_oftalmologico';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_constraint AS c
          JOIN pg_attribute AS a
            ON a.attrelid = c.conrelid
           AND a.attnum = ANY(c.conkey)
         WHERE c.conrelid = 'public.resultado_examen'::regclass
           AND c.contype = 'u'
           AND a.attname = 'examen_id'
    ) THEN
        RAISE EXCEPTION 'CU18: resultado_examen.examen_id no debe ser UNIQUE';
    END IF;
END $$;

-- 2) Alineacion de nulabilidad y defaults -------------------------------
-- No se inventan valores clinicos. Si apareciera un NULL inesperado, el
-- script aborta antes de endurecer la columna.
DO $$
DECLARE
    v_nulos bigint;
BEGIN
    SELECT count(*) INTO v_nulos
      FROM public.examen_oftalmologico
     WHERE fecha_solicitud IS NULL OR estado IS NULL;
    IF v_nulos > 0 THEN
        RAISE EXCEPTION 'CU18: examen_oftalmologico contiene % fila(s) con fecha_solicitud/estado NULL', v_nulos;
    END IF;

    SELECT count(*) INTO v_nulos
      FROM public.resultado_examen
     WHERE fecha_resultado IS NULL OR resultado IS NULL OR estado IS NULL;
    IF v_nulos > 0 THEN
        RAISE EXCEPTION 'CU18: resultado_examen contiene % fila(s) con fecha_resultado/resultado/estado NULL', v_nulos;
    END IF;
END $$;

ALTER TABLE public.examen_oftalmologico
    ALTER COLUMN fecha_solicitud SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN fecha_solicitud SET NOT NULL,
    ALTER COLUMN estado SET DEFAULT TRUE,
    ALTER COLUMN estado SET NOT NULL;

ALTER TABLE public.resultado_examen
    ALTER COLUMN fecha_resultado SET DEFAULT CURRENT_TIMESTAMP,
    ALTER COLUMN fecha_resultado SET NOT NULL,
    ALTER COLUMN resultado SET NOT NULL,
    ALTER COLUMN estado SET DEFAULT TRUE,
    ALTER COLUMN estado SET NOT NULL;

-- La FK examen_oftalmologico.consulta_clinica_id ya estaba indexada. Esta
-- segunda FK no lo estaba y se usa en todos los listados/cascadas de CU18.
CREATE INDEX IF NOT EXISTS idx_resultado_examen_examen
    ON public.resultado_examen (examen_id);

-- 3) RBAC CU18 -----------------------------------------------------------
DO $$
DECLARE
    v_modulo_id bigint;
    v_funcion_id bigint;
    v_accion_escritura bigint;
    v_accion_ambas bigint;
    v_rol_oftalmologo bigint;
    v_rol_administrador bigint;
    v_nombre constant text :=
        'Registrar resultados de exámenes oftalmológicos';
BEGIN
    -- Resolver el mismo modulo clinico de CU13/CU15/CU16/CU17 por nombres.
    SELECT modulo_id INTO v_modulo_id
      FROM public.funcion
     WHERE nombre IN (
         'Consultar historial clínico',
         'Registrar consulta clínica',
         'Registrar diagnóstico',
         'Registrar tratamientos, indicaciones y recetas'
     )
     ORDER BY CASE nombre
         WHEN 'Consultar historial clínico' THEN 1
         WHEN 'Registrar consulta clínica' THEN 2
         WHEN 'Registrar diagnóstico' THEN 3
         ELSE 4
     END, id
     LIMIT 1;

    IF v_modulo_id IS NULL THEN
        RAISE EXCEPTION 'CU18: no se pudo resolver el modulo clinico';
    END IF;

    SELECT id INTO v_funcion_id
      FROM public.funcion
     WHERE modulo_id = v_modulo_id AND nombre = v_nombre
     ORDER BY id
     LIMIT 1;

    IF v_funcion_id IS NULL THEN
        INSERT INTO public.funcion (modulo_id, nombre, descripcion, estado)
        VALUES (
            v_modulo_id,
            v_nombre,
            'Permite registrar exámenes oftalmológicos y uno o más resultados asociados a una consulta clínica.',
            TRUE
        )
        RETURNING id INTO v_funcion_id;
    ELSE
        UPDATE public.funcion
           SET estado = TRUE
         WHERE id = v_funcion_id AND estado IS DISTINCT FROM TRUE;
    END IF;

    SELECT id INTO v_accion_escritura
      FROM public.accion
     WHERE upper(nombre) = 'ESCRITURA' AND estado IS TRUE
     ORDER BY id LIMIT 1;
    SELECT id INTO v_accion_ambas
      FROM public.accion
     WHERE upper(nombre) = 'AMBAS' AND estado IS TRUE
     ORDER BY id LIMIT 1;

    IF v_accion_escritura IS NULL OR v_accion_ambas IS NULL THEN
        RAISE EXCEPTION 'CU18: faltan acciones activas ESCRITURA/AMBAS';
    END IF;

    SELECT id INTO v_rol_oftalmologo
      FROM public.rol
     WHERE left(lower(nombre), 6) = 'oftalm' AND estado IS TRUE
     ORDER BY id LIMIT 1;
    SELECT id INTO v_rol_administrador
      FROM public.rol
     WHERE left(lower(nombre), 9) = 'administr' AND estado IS TRUE
     ORDER BY id LIMIT 1;

    IF v_rol_oftalmologo IS NULL OR v_rol_administrador IS NULL THEN
        RAISE EXCEPTION 'CU18: faltan roles activos Oftalmologo/Administrador';
    END IF;

    INSERT INTO public.rol_funcion (
        rol_id, funcion_id, accion_id, descripcion
    ) VALUES (
        v_rol_oftalmologo,
        v_funcion_id,
        v_accion_escritura,
        'CU18 Registrar resultados de examenes: ESCRITURA'
    )
    ON CONFLICT (rol_id, funcion_id) DO UPDATE
       SET accion_id = EXCLUDED.accion_id,
           descripcion = EXCLUDED.descripcion;

    INSERT INTO public.rol_funcion (
        rol_id, funcion_id, accion_id, descripcion
    ) VALUES (
        v_rol_administrador,
        v_funcion_id,
        v_accion_ambas,
        'CU18 Registrar resultados de examenes: AMBAS'
    )
    ON CONFLICT (rol_id, funcion_id) DO UPDATE
       SET accion_id = EXCLUDED.accion_id,
           descripcion = EXCLUDED.descripcion;

    IF EXISTS (
        SELECT 1
          FROM public.rol_funcion AS rf
          JOIN public.rol AS r ON r.id = rf.rol_id
         WHERE rf.funcion_id = v_funcion_id
           AND (left(lower(r.nombre), 8) = 'paciente'
                OR left(lower(r.nombre), 9) = 'recepcion')
    ) THEN
        RAISE EXCEPTION 'CU18: Paciente/Recepcionista no deben tener el permiso CU18';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM public.rol_funcion
         WHERE rol_id = v_rol_oftalmologo
           AND funcion_id = v_funcion_id
           AND accion_id = v_accion_escritura
    ) OR NOT EXISTS (
        SELECT 1 FROM public.rol_funcion
         WHERE rol_id = v_rol_administrador
           AND funcion_id = v_funcion_id
           AND accion_id = v_accion_ambas
    ) THEN
        RAISE EXCEPTION 'CU18: las postcondiciones RBAC no se cumplieron';
    END IF;
END $$;

-- 4) Verificaciones finales ---------------------------------------------
SELECT table_name, column_name, data_type, character_maximum_length,
       is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('examen_oftalmologico', 'resultado_examen')
ORDER BY table_name, ordinal_position;

SELECT c.conrelid::regclass AS tabla, c.conname, c.contype,
       pg_get_constraintdef(c.oid) AS definicion
FROM pg_constraint AS c
WHERE c.conrelid IN (
    'public.examen_oftalmologico'::regclass,
    'public.resultado_examen'::regclass
)
ORDER BY c.conrelid::regclass::text, c.conname;

SELECT f.id AS funcion_id, f.nombre AS funcion, m.nombre AS modulo,
       r.nombre AS rol, a.nombre AS accion
FROM public.funcion AS f
JOIN public.modulo AS m ON m.id = f.modulo_id
LEFT JOIN public.rol_funcion AS rf ON rf.funcion_id = f.id
LEFT JOIN public.rol AS r ON r.id = rf.rol_id
LEFT JOIN public.accion AS a ON a.id = rf.accion_id
WHERE f.nombre = 'Registrar resultados de exámenes oftalmológicos'
ORDER BY r.nombre;

-- Debe devolver cero filas.
SELECT r.nombre AS rol_no_autorizado
FROM public.rol AS r
JOIN public.rol_funcion AS rf ON rf.rol_id = r.id
JOIN public.funcion AS f ON f.id = rf.funcion_id
WHERE f.nombre = 'Registrar resultados de exámenes oftalmológicos'
  AND (left(lower(r.nombre), 8) = 'paciente'
       OR left(lower(r.nombre), 9) = 'recepcion');

-- Deben seguir en cero: la migracion no inserta datos clinicos.
SELECT 'examen_oftalmologico' AS tabla, count(*) AS filas
FROM public.examen_oftalmologico
UNION ALL
SELECT 'resultado_examen', count(*) FROM public.resultado_examen;

COMMIT;

-- Para dry-run, ejecutar una copia cambiando el COMMIT anterior por ROLLBACK.
