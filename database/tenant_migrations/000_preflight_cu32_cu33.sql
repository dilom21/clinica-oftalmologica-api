BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_missing text;
BEGIN
    SELECT string_agg(x.nombre, ', ' ORDER BY x.nombre) INTO v_missing
      FROM (VALUES
          ('public.usuario'), ('public.paciente'), ('public.consulta_clinica'),
          ('public.servicio_realizado'), ('public.pago'), ('public.pago_detalle'),
          ('public.notificacion_pago'), ('public.bitacora'), ('public.modulo'),
          ('public.funcion'), ('public.accion'), ('public.rol'), ('public.rol_funcion')
      ) x(nombre)
     WHERE to_regclass(x.nombre) IS NULL;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'Preflight CU32/CU33: faltan tablas: %', v_missing;
    END IF;
END $$;

SELECT current_database() AS database_name,
       count(*) FILTER (WHERE table_name IN (
           'plan_pago','plan_pago_servicio','cuota','intento_pago_cuota'
       )) AS tablas_cu32_cu33_existentes
  FROM information_schema.tables
 WHERE table_schema='public';

ROLLBACK;
