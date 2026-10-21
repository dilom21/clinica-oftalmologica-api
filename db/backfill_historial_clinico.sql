-- =========================================================
-- Regularización de datos: historial_clinico obligatorio (1 a 1)
-- Archivo: db/backfill_historial_clinico.sql
--
-- MOTIVO
--   Los pacientes registrados ANTES de la corrección de CU07 y del registro
--   móvil (POST /seguridad/registro-paciente) no generaban su historial
--   clínico. En producción quedaron pacientes sin historial (se reportaron
--   9 de 10 pacientes). Esta corrección de código crea el historial desde
--   ahora en adelante; este script regulariza los pacientes antiguos.
--
-- ALCANCE
--   - Solo contiene un INSERT ... SELECT idempotente.
--   - NO ejecuta DELETE ni UPDATE masivo.
--   - NO crea triggers ni tablas nuevas.
--   - NO modifica datos clínicos existentes.
--   - No se envían ids manuales: `historial_clinico.id` se genera con el
--     default de la tabla (identity/serial en Supabase).
--
-- CÓMO EJECUTARLO (controlado, una sola vez)
--   1. Abrir el SQL Editor de Supabase (o psql) apuntando al proyecto real.
--   2. Ejecutar el bloque completo; está envuelto en BEGIN/COMMIT.
--   3. Revisar el resultado de las consultas de verificación.
--   4. Si la verificación no es la esperada, ejecutar ROLLBACK en lugar de
--      COMMIT (o no guardar la transacción).
--
-- RESULTADO ESPERADO
--   Debe agregar exactamente un historial por cada paciente sin historial
--   (los 9 pacientes antiguos), con:
--       paciente_id            = paciente.id
--       fecha_apertura         = paciente.fecha_registro
--       observaciones_generales = NULL
--       estado                 = TRUE
--   El historial actual de Juan Pérez no se toca (ya existe y queda excluido
--   por el NOT EXISTS).
-- =========================================================

BEGIN;

-- 0) Foto previa: pacientes sin historial (debe coincidir con los 9 reportados).
SELECT count(*) AS pacientes_sin_historial_antes
FROM public.paciente AS p
WHERE NOT EXISTS (
    SELECT 1
    FROM public.historial_clinico AS h
    WHERE h.paciente_id = p.id
);

-- 1) Regularización idempotente.
--    Se puede ejecutar varias veces: el NOT EXISTS evita duplicados y
--    ON CONFLICT (paciente_id) DO NOTHING protege además contra el UNIQUE.
INSERT INTO public.historial_clinico (
    paciente_id,
    fecha_apertura,
    observaciones_generales,
    estado
)
SELECT
    p.id,
    p.fecha_registro,
    NULL,
    TRUE
FROM public.paciente AS p
WHERE NOT EXISTS (
    SELECT 1
    FROM public.historial_clinico AS h
    WHERE h.paciente_id = p.id
)
ON CONFLICT (paciente_id) DO NOTHING;

-- 2) Verificación posterior: debe devolver 0.
SELECT count(*) AS pacientes_sin_historial_despues
FROM public.paciente AS p
WHERE NOT EXISTS (
    SELECT 1
    FROM public.historial_clinico AS h
    WHERE h.paciente_id = p.id
);

-- 3) Verificación de duplicados: no debe devolver filas.
SELECT h.paciente_id, count(*) AS historiales
FROM public.historial_clinico AS h
GROUP BY h.paciente_id
HAVING count(*) > 1;

-- 4) Verificación de coherencia de fechas:
--    no debe devolver filas (fecha_apertura == paciente.fecha_registro).
SELECT p.id AS paciente_id, p.fecha_registro, h.fecha_apertura
FROM public.paciente AS p
JOIN public.historial_clinico AS h ON h.paciente_id = p.id
WHERE h.fecha_apertura <> p.fecha_registro;

COMMIT;

-- Si algo no coincide, en lugar de COMMIT ejecutar:
-- ROLLBACK;
