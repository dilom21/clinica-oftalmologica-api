"""Inspección CU29 de metadatos/RBAC, siempre READ ONLY; no consulta compras."""

import hashlib
import json
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from app.core.config import DATABASE_URL


def main():
    url = make_url(DATABASE_URL).set(drivername="postgresql")

    def conectar(database):
        return psycopg.connect(
            url.set(database=database).render_as_string(hide_password=False),
            connect_timeout=10,
            options="-c default_transaction_read_only=on -c statement_timeout=15000",
        )

    with conectar(url.database) as conn:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SELECT current_setting('server_version')")
            print(json.dumps({"server_version": cur.fetchone()[0]}))
            cur.execute("SELECT database_name,version_schema FROM saas_control.tenant_database ORDER BY database_name")
            tenants = cur.fetchall()
    migration = Path("database/tenant_migrations/versions/20261024150000_cu26_cu27_cu29.sql")
    checksum = hashlib.sha256(migration.read_bytes()).hexdigest()
    for database, version in tenants:
        with conectar(database) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                result = {"tenant": database, "version_schema": version}
                cur.execute("SELECT version,checksum_sha256 FROM app_migrations.schema_migration ORDER BY version")
                ledger = dict(cur.fetchall())
                result["ledger_versions"] = list(ledger)
                result["cu29_checksum_ok"] = ledger.get("20261024150000") == checksum
                cur.execute("""SELECT table_name,column_name,data_type,is_nullable,column_default,is_identity,
                    identity_generation,character_maximum_length,numeric_precision,numeric_scale
                    FROM information_schema.columns WHERE table_schema='public'
                    AND table_name IN ('compra','detalle_compra') ORDER BY table_name,ordinal_position""")
                result["columns"] = cur.fetchall()
                cur.execute("""SELECT c.relname,k.conname,pg_get_constraintdef(k.oid)
                    FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname IN ('compra','detalle_compra','movimiento_inventario')
                    ORDER BY c.relname,k.conname""")
                result["constraints"] = cur.fetchall()
                cur.execute("""SELECT tablename,indexname,indexdef FROM pg_indexes
                    WHERE schemaname='public' AND tablename IN ('compra','detalle_compra','movimiento_inventario')
                    ORDER BY tablename,indexname""")
                result["indexes"] = cur.fetchall()
                cur.execute("""SELECT m.nombre,f.estado,m.estado,r.nombre,r.estado,a.nombre,a.estado
                    FROM public.funcion f JOIN public.modulo m ON m.id=f.modulo_id
                    LEFT JOIN public.rol_funcion rf ON rf.funcion_id=f.id
                    LEFT JOIN public.rol r ON r.id=rf.rol_id
                    LEFT JOIN public.accion a ON a.id=rf.accion_id
                    WHERE f.nombre='Registrar compras a proveedor' ORDER BY m.nombre,r.nombre""")
                result["rbac"] = cur.fetchall()
                print(json.dumps(result, ensure_ascii=True, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Los errores de conexión pueden contener DSN: nunca imprimirlos.
        print(json.dumps({"error_type": type(error).__name__, "sqlstate": getattr(error, "sqlstate", None)}))
        raise SystemExit(1)
