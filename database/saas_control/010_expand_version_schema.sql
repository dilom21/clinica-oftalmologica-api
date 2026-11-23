-- SI2: Ampliacion de version_schema en el Control Plane.
-- Preserva los datos existentes y permite reejecucion segura.

BEGIN;

DO $guard$
DECLARE
    v_tenant_type TEXT;
    v_backup_type TEXT;
BEGIN
    SELECT format_type(a.atttypid, a.atttypmod)
      INTO v_tenant_type
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'saas_control'
       AND c.relname = 'tenant_database'
       AND a.attname = 'version_schema'
       AND a.attnum > 0
       AND NOT a.attisdropped;

    SELECT format_type(a.atttypid, a.atttypmod)
      INTO v_backup_type
      FROM pg_attribute a
      JOIN pg_class c ON c.oid = a.attrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'saas_control'
       AND c.relname = 'backup_tenant'
       AND a.attname = 'version_schema'
       AND a.attnum > 0
       AND NOT a.attisdropped;

    IF v_tenant_type IS NULL OR
       v_backup_type IS NULL THEN
        RAISE EXCEPTION
            'No se encontraron las columnas esperadas del Control Plane';
    END IF;

    IF v_tenant_type NOT IN (
        'character varying(30)',
        'character varying(64)'
    ) THEN
        RAISE EXCEPTION
            'Tipo inesperado tenant_database.version_schema: %',
            v_tenant_type;
    END IF;

    IF v_backup_type NOT IN (
        'character varying(30)',
        'character varying(64)'
    ) THEN
        RAISE EXCEPTION
            'Tipo inesperado backup_tenant.version_schema: %',
            v_backup_type;
    END IF;
END;
$guard$;

ALTER TABLE saas_control.tenant_database
    ALTER COLUMN version_schema TYPE VARCHAR(64);

ALTER TABLE saas_control.backup_tenant
    ALTER COLUMN version_schema TYPE VARCHAR(64);

COMMIT;
