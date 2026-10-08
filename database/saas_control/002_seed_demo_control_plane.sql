-- PASO 7A.1 - Idempotent academic/demo data for the SaaS Control Plane.
-- All contacts, NITs and domains below are fictitious; no password or database secret is stored.

BEGIN;

INSERT INTO saas_control.plan_saas (
    codigo, nombre, descripcion, precio_mensual, moneda,
    limite_usuarios, limite_almacenamiento_mb, estado
)
VALUES
    ('BASICO', 'Básico', 'Plan DEMO académico para operaciones iniciales.', 99.00, 'BOB', 10, 5120, TRUE),
    ('PROFESIONAL', 'Profesional', 'Plan DEMO académico para clínicas en crecimiento.', 249.00, 'BOB', 50, 20480, TRUE),
    ('EMPRESARIAL', 'Empresarial', 'Plan DEMO académico para organizaciones multi-sede.', 499.00, 'BOB', 200, 102400, TRUE)
ON CONFLICT (codigo) DO UPDATE SET
    nombre = EXCLUDED.nombre,
    descripcion = EXCLUDED.descripcion,
    precio_mensual = EXCLUDED.precio_mensual,
    moneda = EXCLUDED.moneda,
    limite_usuarios = EXCLUDED.limite_usuarios,
    limite_almacenamiento_mb = EXCLUDED.limite_almacenamiento_mb,
    estado = EXCLUDED.estado;

INSERT INTO saas_control.empresa (
    codigo, slug, razon_social, nombre_comercial, nit, correo,
    telefono, direccion, estado
)
VALUES
    ('VISION-CLARA', 'vision-clara', 'Centro Oftalmológico Visión Clara DEMO S.R.L.', 'Centro Oftalmológico Visión Clara', 'DEMO-NIT-7001', 'contacto@vision-clara.example', '+591 70000001', 'Av. Demo 701, Ciudad Ficticia', 'ACTIVA'),
    ('OFTALMO-NORTE', 'oftalmo-norte', 'Clínica Oftalmológica Norte DEMO S.R.L.', 'Clínica Oftalmológica Norte', 'DEMO-NIT-7002', 'contacto@oftalmo-norte.example', '+591 70000002', 'Calle Simulada 702, Ciudad Ficticia', 'ACTIVA'),
    ('VISUAL-ORIENTAL', 'visual-oriental', 'Centro Visual Oriental DEMO S.R.L.', 'Centro Visual Oriental', 'DEMO-NIT-7003', 'contacto@visual-oriental.example', '+591 70000003', 'Pasaje Académico 703, Ciudad Ficticia', 'ACTIVA'),
    ('INSTITUTO-VISION', 'instituto-vision', 'Instituto de la Visión DEMO S.R.L.', 'Instituto de la Visión', 'DEMO-NIT-7004', 'contacto@instituto-vision.example', '+591 70000004', 'Av. Universitaria 704, Ciudad Ficticia', 'ACTIVA'),
    ('OFTALMOCARE', 'oftalmocare', 'OftalmoCare DEMO S.R.L.', 'OftalmoCare', 'DEMO-NIT-7005', 'contacto@oftalmocare.example', '+591 70000005', 'Calle Clínica 705, Ciudad Ficticia', 'ACTIVA'),
    ('VISTA-SUR', 'vista-sur', 'Clínica Vista Sur DEMO S.R.L.', 'Clínica Vista Sur', 'DEMO-NIT-7006', 'contacto@vista-sur.example', '+591 70000006', 'Av. Meridiano 706, Ciudad Ficticia', 'ACTIVA'),
    ('MEDICO-OCULAR', 'medico-ocular', 'Centro Médico Ocular DEMO S.R.L.', 'Centro Médico Ocular', 'DEMO-NIT-7007', 'contacto@medico-ocular.example', '+591 70000007', 'Boulevard Ficticio 707, Ciudad Ficticia', 'ACTIVA')
ON CONFLICT (codigo) DO UPDATE SET
    slug = EXCLUDED.slug,
    razon_social = EXCLUDED.razon_social,
    nombre_comercial = EXCLUDED.nombre_comercial,
    nit = EXCLUDED.nit,
    correo = EXCLUDED.correo,
    telefono = EXCLUDED.telefono,
    direccion = EXCLUDED.direccion,
    estado = EXCLUDED.estado,
    fecha_actualizacion = now();

INSERT INTO saas_control.suscripcion (
    empresa_id, plan_id, fecha_inicio, fecha_fin, estado, renovacion_auto
)
SELECT e.id, p.id, DATE '2026-01-01', DATE '2026-12-31', 'ACTIVA', TRUE
FROM (VALUES
    ('VISION-CLARA', 'EMPRESARIAL'),
    ('OFTALMO-NORTE', 'PROFESIONAL'),
    ('VISUAL-ORIENTAL', 'PROFESIONAL'),
    ('INSTITUTO-VISION', 'EMPRESARIAL'),
    ('OFTALMOCARE', 'BASICO'),
    ('VISTA-SUR', 'BASICO'),
    ('MEDICO-OCULAR', 'PROFESIONAL')
) AS demo(codigo_empresa, codigo_plan)
JOIN saas_control.empresa e ON e.codigo = demo.codigo_empresa
JOIN saas_control.plan_saas p ON p.codigo = demo.codigo_plan
ON CONFLICT (empresa_id, plan_id, fecha_inicio, fecha_fin) DO UPDATE SET
    estado = EXCLUDED.estado,
    renovacion_auto = EXCLUDED.renovacion_auto;

INSERT INTO saas_control.tenant_database (
    empresa_id, database_name, estado
)
SELECT e.id, demo.database_name, 'PENDIENTE'
FROM (VALUES
    ('VISION-CLARA', 'tenant_vision_clara'),
    ('OFTALMO-NORTE', 'tenant_oftalmo_norte'),
    ('VISUAL-ORIENTAL', 'tenant_visual_oriental'),
    ('INSTITUTO-VISION', 'tenant_instituto_vision'),
    ('OFTALMOCARE', 'tenant_oftalmocare'),
    ('VISTA-SUR', 'tenant_vista_sur'),
    ('MEDICO-OCULAR', 'tenant_medico_ocular')
) AS demo(codigo_empresa, database_name)
JOIN saas_control.empresa e ON e.codigo = demo.codigo_empresa
ON CONFLICT (empresa_id) DO UPDATE SET
    database_name = EXCLUDED.database_name,
    estado = 'PENDIENTE',
    fecha_provisionamiento = NULL,
    ultima_verificacion = NULL;

INSERT INTO saas_control.provisionamiento_tenant (
    empresa_id, tenant_database_id, estado, intentos
)
SELECT e.id, td.id, 'PENDIENTE', 0
FROM saas_control.empresa e
JOIN saas_control.tenant_database td ON td.empresa_id = e.id
WHERE e.codigo IN (
    'VISION-CLARA', 'OFTALMO-NORTE', 'VISUAL-ORIENTAL', 'INSTITUTO-VISION',
    'OFTALMOCARE', 'VISTA-SUR', 'MEDICO-OCULAR'
)
ON CONFLICT (tenant_database_id) DO UPDATE SET
    empresa_id = EXCLUDED.empresa_id,
    estado = 'PENDIENTE',
    intentos = 0,
    paso_actual = NULL,
    fecha_inicio = NULL,
    fecha_fin = NULL,
    mensaje_error = NULL;

-- Deliberately no saas_usuario row is inserted in 7A.1.
COMMIT;
