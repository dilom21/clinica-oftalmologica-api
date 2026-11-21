BEGIN TRANSACTION READ ONLY;

DO $$
DECLARE
    v_tab text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY[
        'plan_pago','plan_pago_servicio','cuota','intento_pago_cuota'
    ] LOOP
        IF to_regclass('public.' || v_tab) IS NULL THEN
            RAISE EXCEPTION 'Verificacion CU32/CU33: falta %', v_tab;
        END IF;
        IF NOT (SELECT relrowsecurity FROM pg_class
                 WHERE oid=to_regclass('public.' || v_tab)) THEN
            RAISE EXCEPTION 'Verificacion CU32/CU33: RLS deshabilitado en %', v_tab;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_pps_servicio_comprometido'
         AND lower(replace(indexdef, ' ', '')) LIKE '%where(activoistrue)%'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE schemaname='public'
         AND indexname='uq_intento_cuota_aplicada'
         AND lower(replace(indexdef, ' ', '')) LIKE '%where(aplicado_a_cuotaistrue)%'
    ) THEN
        RAISE EXCEPTION 'Verificacion CU32/CU33: indices parciales incompatibles';
    END IF;

    IF (SELECT count(*) FROM public.funcion f
        JOIN public.modulo m ON m.id=f.modulo_id
        WHERE lower(trim(m.nombre))='pagos' AND f.estado IS TRUE
          AND f.nombre IN ('Gestionar planes de pago','Gestionar cuotas')) <> 2 THEN
        RAISE EXCEPTION 'Verificacion CU32/CU33: RBAC incompleto';
    END IF;
    IF (SELECT count(*) FROM public.rol_funcion rf
        JOIN public.rol r ON r.id=rf.rol_id
        JOIN public.funcion f ON f.id=rf.funcion_id
        JOIN public.accion a ON a.id=rf.accion_id
        WHERE lower(trim(r.nombre)) IN ('administrador','recepcionista')
          AND f.nombre IN ('Gestionar planes de pago','Gestionar cuotas')
          AND upper(trim(a.nombre))='AMBAS') <> 4 THEN
        RAISE EXCEPTION 'Verificacion CU32/CU33: asignaciones RBAC incompletas';
    END IF;
END $$;

SELECT table_name, count(*) AS columnas
  FROM information_schema.columns
 WHERE table_schema='public'
   AND table_name IN ('plan_pago','plan_pago_servicio','cuota','intento_pago_cuota')
 GROUP BY table_name
 ORDER BY table_name;

ROLLBACK;
