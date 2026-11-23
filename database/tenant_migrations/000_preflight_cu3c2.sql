-- Preflight de Fase 3C.2 (solo lectura).
--
-- Se ejecuta antes de cualquier tenant (incluidos v3/v4/v5/v6), por lo que solo
-- exige tablas presentes en todas las versiones de origen soportadas. Falla
-- cerrado si el destino no es una base tenant esperada, si falta un
-- prerrequisito o si las tablas nuevas ya existen (instalacion parcial).
BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_missing text;
    v_existing text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.usuario'), ('public.rol'), ('public.paciente'),
          ('public.cita'), ('public.bitacora'), ('public.modulo'),
          ('public.funcion'), ('public.accion'), ('public.rol_funcion')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'Preflight Fase 3C.2: faltan tablas: %', v_missing;
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
            'Preflight Fase 3C.2: tablas ya existentes (parcial o duplicada): %',
            v_existing;
    END IF;
END $$;

SELECT current_database() AS database_name,
       count(*) FILTER (WHERE table_name IN (
           'dispositivo_push','notificacion','notificacion_entrega',
           'preferencia_notificacion','cita_evento'
       )) AS tablas_fase3c2_existentes,
       count(*) FILTER (WHERE table_name IN (
           'pago','pago_detalle','notificacion_pago','cita','usuario'
       )) AS tablas_base_existentes
  FROM information_schema.tables
 WHERE table_schema='public';

ROLLBACK;
