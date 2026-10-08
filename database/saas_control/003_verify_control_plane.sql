-- PASO 7A.1 - Read-only verification. This file contains SELECTs only.

-- Schema and exact expected table set.
SELECT
    to_regnamespace('saas_control') IS NOT NULL AS schema_exists,
    (
        SELECT count(*) = 7
        FROM information_schema.tables
        WHERE table_schema = 'saas_control'
          AND table_type = 'BASE TABLE'
    ) AS exactly_seven_tables,
    (
        SELECT count(*) = 7
        FROM information_schema.tables t
        WHERE t.table_schema = 'saas_control'
          AND t.table_type = 'BASE TABLE'
          AND t.table_name IN (
              'empresa', 'plan_saas', 'suscripcion', 'tenant_database',
              'saas_usuario', 'saas_bitacora', 'provisionamiento_tenant'
          )
    ) AS expected_tables_present,
    (
        SELECT string_agg(table_name, ', ' ORDER BY table_name)
        FROM information_schema.tables
        WHERE table_schema = 'saas_control'
          AND table_type = 'BASE TABLE'
    ) AS tables_found;

-- Requested data cardinalities and status checks.
SELECT
    (SELECT count(*) FROM saas_control.empresa) AS empresas,
    (SELECT count(*) = 7 FROM saas_control.empresa) AS empresas_exactamente_siete,
    (SELECT count(*) > 5 FROM saas_control.empresa) AS empresas_mayor_que_cinco,
    (SELECT count(*) FROM saas_control.plan_saas) AS planes,
    (SELECT count(*) = 3 FROM saas_control.plan_saas) AS planes_exactamente_tres,
    (SELECT count(*) FROM saas_control.tenant_database) AS tenant_databases,
    (SELECT count(*) = 7 FROM saas_control.tenant_database) AS tenant_databases_exactamente_siete,
    (SELECT count(*) FROM saas_control.suscripcion WHERE estado = 'ACTIVA') AS suscripciones_activas,
    (SELECT count(*) >= 7 FROM saas_control.suscripcion WHERE estado = 'ACTIVA') AS suscripciones_activas_suficientes,
    (SELECT count(*) FROM saas_control.provisionamiento_tenant WHERE estado = 'PENDIENTE') AS provisionamientos_pendientes,
    (SELECT count(*) = 7 FROM saas_control.provisionamiento_tenant WHERE estado = 'PENDIENTE') AS provisionamientos_pendientes_exactos,
    (SELECT count(*) FROM saas_control.saas_usuario) AS usuarios_saas_seed;

-- One joined administrative view of the demo relationships.
SELECT
    e.codigo,
    e.nombre_comercial AS empresa,
    p.nombre AS plan,
    s.estado AS estado_suscripcion,
    td.database_name,
    td.estado AS estado_database,
    pt.estado AS estado_provisionamiento
FROM saas_control.empresa e
JOIN saas_control.suscripcion s ON s.empresa_id = e.id
JOIN saas_control.plan_saas p ON p.id = s.plan_id
JOIN saas_control.tenant_database td ON td.empresa_id = e.id
JOIN saas_control.provisionamiento_tenant pt ON pt.tenant_database_id = td.id
WHERE e.codigo IN (
    'VISION-CLARA', 'OFTALMO-NORTE', 'VISUAL-ORIENTAL', 'INSTITUTO-VISION',
    'OFTALMOCARE', 'VISTA-SUR', 'MEDICO-OCULAR'
)
ORDER BY e.codigo, s.fecha_inicio;

-- Backend-only schema privilege checks: both values should be FALSE.
SELECT
    has_schema_privilege('anon', 'saas_control', 'USAGE') AS anon_has_usage,
    has_schema_privilege('authenticated', 'saas_control', 'USAGE') AS authenticated_has_usage,
    NOT has_schema_privilege('anon', 'saas_control', 'USAGE') AS anon_without_usage,
    NOT has_schema_privilege('authenticated', 'saas_control', 'USAGE') AS authenticated_without_usage;

-- Confirm that sensitive credential/secret column names are absent.
SELECT
    count(*) AS forbidden_secret_columns_found,
    count(*) = 0 AS no_forbidden_secret_columns
FROM information_schema.columns
WHERE table_schema = 'saas_control'
  AND lower(column_name) IN (
      'password_db', 'database_password', 'service_role', 'jwt_secret', 'api_key'
  );
