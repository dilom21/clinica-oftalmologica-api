-- PROPUESTA NO EJECUTADA.
-- Historial propio de migraciones de la aplicacion por base tenant.
CREATE SCHEMA IF NOT EXISTS app_migrations;

CREATE TABLE IF NOT EXISTS app_migrations.schema_migration (
    version varchar(20) PRIMARY KEY,
    nombre varchar(150) NOT NULL,
    checksum_sha256 char(64) NOT NULL,
    aplicada_en timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duracion_ms bigint NOT NULL,
    runner_version varchar(50) NOT NULL,
    CONSTRAINT ck_schema_migration_checksum
        CHECK (checksum_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_schema_migration_duracion
        CHECK (duracion_ms >= 0)
);

REVOKE ALL ON SCHEMA app_migrations FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA app_migrations FROM PUBLIC;

DO $$
DECLARE
    v_rol text;
BEGIN
    FOR v_rol IN
        SELECT rolname FROM pg_roles
         WHERE rolname IN ('anon', 'authenticated')
    LOOP
        EXECUTE format('REVOKE ALL ON SCHEMA app_migrations FROM %I', v_rol);
        EXECUTE format(
            'REVOKE ALL ON ALL TABLES IN SCHEMA app_migrations FROM %I', v_rol
        );
    END LOOP;
END $$;
