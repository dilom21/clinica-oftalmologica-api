"""Guarded CU26+CU27+CU29 migration for existing SI2 clinical tenant DBs.

Run --plan first. --execute requires explicit plan hash and environment opt-in.
No tenant is changed during --plan. Only tenant DB schema and control-plane
version are changed on --execute. No clinical rows are read, copied, or changed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / 'database' / 'tenant_migrations' / 'versions' / '20261024150000_cu26_cu27_cu29.sql'
SCHEMA_VERSION = 'v3-sprint3-cu26-cu27-cu29'
MIGRATION_VERSION = '20261024150000'
MIGRATION_NAME = 'cu26_cu27_cu29_inventario_compras'
ALLOW_ENV = 'SPRINT3_TENANT_MIGRATIONS_ALLOW'
LOCK_KEY = 20261024150000
NEW_TABLES = ('categoria','producto','lote','inventario','movimiento_inventario','compra','detalle_compra')
PROVIDER_FIELDS = {
    'id': ('integer', None, 'NO'),
    'razon_social': ('character varying',150,'NO'),
    'nit': ('character varying',30,'YES'),
    'telefono': ('character varying',20,'YES'),
    'correo': ('character varying',100,'YES'),
    'direccion': ('character varying',255,'YES'),
    'contacto': ('character varying',100,'YES'),
    'estado': ('boolean',None,'YES'),
}
EXPECTED_FK = {
 'fk_producto_categoria': ('producto', 'categoria'),
 'fk_lote_producto': ('lote','producto'),
 'fk_inventario_lote': ('inventario','lote'),
 'fk_movimiento_inventario_inventario': ('movimiento_inventario','inventario'),
 'fk_movimiento_inventario_usuario': ('movimiento_inventario','usuario'),
 'fk_compra_proveedor': ('compra','proveedores'),
 'fk_compra_usuario': ('compra','usuario'),
 'fk_detalle_compra_compra': ('detalle_compra','compra'),
 'fk_detalle_compra_lote': ('detalle_compra','lote'),
}

class Refusal(Exception): pass

def dependencies():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env', override=False)
    except ImportError:
        pass
    try:
        import psycopg
        from sqlalchemy.engine import make_url
    except ImportError as ex:
        raise Refusal('Install backend requirements: psycopg and sqlalchemy') from ex
    raw = os.environ.get('DATABASE_URL')
    if not raw:
        raise Refusal('DATABASE_URL is missing from the environment or backend .env')
    try:
        url = make_url(raw)
    except Exception as ex:
        raise Refusal('DATABASE_URL cannot be parsed') from ex
    if url.database != 'postgres':
        raise Refusal('DATABASE_URL must point to source/control database postgres')
    if url.port == 6543:
        raise Refusal('Transaction pooler 6543 is not allowed; use session pooler 5432')
    if not (url.host and url.username and url.password):
        raise Refusal('DATABASE_URL has missing connection components')
    def connect(db_name):
        if not re.fullmatch(r'(postgres|tenant_[a-z0-9_]{1,56})',db_name):
            raise Refusal('Unsafe database name in control-plane record')
        opts={'host':url.host,'port':url.port or 5432,'user':url.username,
              'password':url.password,'dbname':db_name,'connect_timeout':12,
              'application_name':'si2_sprint3_tenant_migrator','autocommit':True,
              'sslmode':url.query.get('sslmode','require')}
        try:
            return psycopg.connect(**opts)
        except Exception as ex:
            raise Refusal('Unable to connect to a tenant database; check network, credentials and port') from ex
    return connect

def db_targets(control, name):
    with control.cursor() as cur:
        cur.execute('''SELECT td.id,td.database_name,td.estado,td.version_schema
                         FROM saas_control.tenant_database td
                         ORDER BY td.database_name''')
        allrows=cur.fetchall()
    targets=[(int(row[0]),str(row[1]),str(row[2]),str(row[3])) for row in allrows]
    byname={row[1]:row for row in targets}
    if name:
        if name not in byname: raise Refusal('The named database is not registered in saas_control')
        chosen=[byname[name]]
    else:
        chosen=[r for r in targets if r[2]=='ACTIVA']
    if not chosen: raise Refusal('No active tenant matches the scope')
    for row in chosen:
        if row[2]!='ACTIVA': raise Refusal('A selected tenant is not ACTIVE')
        if row[3] not in ('v2-cu31',SCHEMA_VERSION):
            raise Refusal('Tenant has an unsupported schema version; investigate before migration')
        if not re.fullmatch(r'tenant_[a-z0-9_]{1,56}',row[1]):
            raise Refusal('Noncanonical tenant name in control plane')
    return chosen

def existing_tables(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT table_name FROM information_schema.tables
                         WHERE table_schema='public' AND table_type='BASE TABLE'
                           AND table_name = ANY(%s)''',[list(NEW_TABLES)+['proveedores','usuario']])
        return frozenset(row[0] for row in cur.fetchall())

def verify_provider(conn):
    with conn.cursor() as cur:
        cur.execute('''SELECT column_name,data_type,character_maximum_length,is_nullable,column_default
                        FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='proveedores'
                        ORDER BY ordinal_position''')
        cols=cur.fetchall()
        shape={r[0]:(r[1],r[2],r[3]) for r in cols}
        if shape != PROVIDER_FIELDS:
            raise Refusal('Existing proveedores table has different columns/types/nullability')
        if not cols[0][4] or 'nextval(' not in cols[0][4]:
            raise Refusal('Existing proveedores.id lacks expected sequence default')
        cur.execute('''SELECT EXISTS(
                SELECT 1 FROM pg_indexes WHERE schemaname='public'
                 AND tablename='proveedores' AND indexname='ix_proveedores_nit'
                   AND indexdef ILIKE 'CREATE UNIQUE INDEX%')''')
        if not cur.fetchone()[0]:
            raise Refusal('Existing proveedores table lacks the canonical unique NIT index')

def tenant_state(conn):
    present=existing_tables(conn)
    if 'usuario' not in present: raise Refusal('Tenant has no public.usuario')
    with conn.cursor() as cur:
        cur.execute('''SELECT data_type FROM information_schema.columns
                        WHERE table_schema='public' AND table_name='usuario' AND column_name='id' ''')
        row=cur.fetchone()
        if row is None or row[0]!='bigint':
            raise Refusal('Tenant usuario.id must be BIGINT')
        cur.execute("SELECT to_regclass('app_migrations.schema_migration') IS NOT NULL")
        if not cur.fetchone()[0]:
            raise Refusal('CU31 migration ledger is missing. Do not bootstrap blindly.')
        cur.execute('''SELECT version,checksum_sha256 FROM app_migrations.schema_migration
                         WHERE version IN ('20261007211200','20261007211247','20261019000000',%s)
                         ORDER BY version''',(MIGRATION_VERSION,))
        ledger={str(x[0]):str(x[1]).strip() for x in cur.fetchall()}
        if not all(v in ledger for v in ('20261007211200','20261007211247','20261019000000')):
            raise Refusal('Existing CU31 ledger is incomplete; reconcile before Sprint 3')
        if 'proveedores' in present: verify_provider(conn)
    found=sorted(set(NEW_TABLES)&present)
    if found and len(found)!=len(NEW_TABLES):
        raise Refusal('Partial CU26/CU27/CU29 schema detected. Manual reconciliation required.')
    return {
      'proveedores':'exists' if 'proveedores' in present else 'missing',
      'new_tables':'all' if len(found)==len(NEW_TABLES) else 'none',
      'checksum':ledger.get(MIGRATION_VERSION),
    }

def check_complete(conn):
    present=existing_tables(conn)
    if not set(NEW_TABLES+('proveedores',)).issubset(present):
        raise Refusal('Post-migration tables are incomplete')
    verify_provider(conn)
    with conn.cursor() as cur:
        cur.execute('''SELECT con.conname, con.conrelid::regclass::text, con.confrelid::regclass::text
                         FROM pg_constraint con
                         WHERE con.contype='f' AND con.conname = ANY(%s)''',[list(EXPECTED_FK)])
        items={x[0]:(x[1].removeprefix('public.'),x[2].removeprefix('public.')) for x in cur.fetchall()}
        if items != EXPECTED_FK:
            raise Refusal('Post-migration FK graph does not match the approved design')
        cur.execute('''SELECT relname,relrowsecurity FROM pg_class c
                         JOIN pg_namespace n ON n.oid=c.relnamespace
                         WHERE n.nspname='public' AND relname = ANY(%s)''',[list(NEW_TABLES)])
        if {r[0]:r[1] for r in cur.fetchall()} != {k:True for k in NEW_TABLES}:
            raise Refusal('Post-migration RLS is incomplete')
        cur.execute('''SELECT EXISTS(
             SELECT 1 FROM pg_indexes WHERE schemaname='public'
               AND tablename='movimiento_inventario'
               AND indexname='uq_movimiento_entrada_detalle_compra'
               AND indexdef ILIKE 'CREATE UNIQUE INDEX%')''')
        if not cur.fetchone()[0]:
            raise Refusal('Purchase receipt idempotency index is missing')

def prepare(args, connect, checksum):
    with connect('postgres') as control:
        targets=db_targets(control,args.database)
    details=[]
    for id_,name,state,version in targets:
        with connect(name) as tenant:
            status=tenant_state(tenant)
            if status['checksum'] and status['checksum']!=checksum:
                raise Refusal('A tenant has conflicting applied migration checksum')
            if status['checksum'] and status['new_tables']!='all':
                raise Refusal('Tenant migration ledger exists but tables are missing')
            if status['new_tables']=='all' and not status['checksum']:
                raise Refusal('All Sprint 3 tables exist without migration ledger; reconcile manually')
            if status['new_tables']=='all':check_complete(tenant)
            if version==SCHEMA_VERSION and not status['checksum']:
                raise Refusal('Control-plane schema_version advanced but ledger is missing')
            details.append({'id':id_,'database':name,'version':version,'status':status})
    payload={'migration_version':MIGRATION_VERSION,'schema_version':SCHEMA_VERSION,
             'sql_sha256':checksum,'tenants':details}
    plan_hash=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return payload,plan_hash

def migrate_one(connect,record,sql,checksum):
    name=record['database']
    with connect(name) as tenant:
        state=tenant_state(tenant)
        if state != record['status']:
            raise Refusal('Tenant schema or ledger changed since planning')
        if state['checksum']:
            check_complete(tenant)
            return 'already_applied'
        with tenant.transaction():
            with tenant.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout='5s'")
                cur.execute("SET LOCAL statement_timeout='120s'")
                cur.execute('SELECT pg_try_advisory_xact_lock(%s)',(LOCK_KEY,))
                if not cur.fetchone()[0]: raise Refusal('Tenant already has a concurrent Sprint 3 migration')
                # Only trusted local SQL from this package; no user input embedded.
                cur.execute(sql)
                cur.execute('''INSERT INTO app_migrations.schema_migration
                        (version,nombre,checksum_sha256,duracion_ms,runner_version)
                        VALUES (%s,%s,%s,0,%s)''',(
                    MIGRATION_VERSION,MIGRATION_NAME,checksum,'si2-sprint3-migrator-v1'))
            check_complete(tenant)
    return 'applied'

def update_control(connect,record):
    if record['version']==SCHEMA_VERSION:return False
    with connect('postgres') as control:
        with control.transaction():
            with control.cursor() as cur:
                cur.execute('''UPDATE saas_control.tenant_database
                                 SET version_schema=%s,ultima_verificacion=now()
                                 WHERE id=%s AND database_name=%s
                                 AND estado='ACTIVA' AND version_schema=%s''',(
                          SCHEMA_VERSION,record['id'],record['database'],record['version']))
                if cur.rowcount!=1:raise Refusal('Control-plane version changed during execution')
    return True

def main():
    parser=argparse.ArgumentParser(description='SI2 Sprint 3 guarded migration for tenants')
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan',action='store_true',help='Read-only inspection; no writes')
    mode.add_argument('--execute',action='store_true',help='Apply approved schema and update version')
    scope=parser.add_mutually_exclusive_group(required=True)
    scope.add_argument('--database',help='One registered active tenant only')
    scope.add_argument('--all',action='store_true',help='All registered active tenants')
    parser.add_argument('--confirm-hash',help='Exact SHA256 printed by --plan')
    args=parser.parse_args()
    try:
        if not MIGRATION.is_file():raise Refusal('Migration SQL file is missing')
        sql=MIGRATION.read_text(encoding='utf-8')
        checksum=hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        connect=dependencies()
        payload,plan_hash=prepare(args,connect,checksum)
        if args.plan:
            if args.confirm_hash:raise Refusal('Do not provide --confirm-hash to --plan')
            print(json.dumps({'mode':'plan','plan_hash':plan_hash,**payload},indent=2,sort_keys=True))
            return 0
        if os.getenv(ALLOW_ENV)!='1':raise Refusal('Execution requires '+ALLOW_ENV+'=1')
        if not args.confirm_hash or args.confirm_hash!=plan_hash:
            raise Refusal('Execution requires the exact current --plan hash')
        for record in payload['tenants']:
            result=migrate_one(connect,record,sql,checksum)
            updated=update_control(connect,record)
            print(json.dumps({'database':record['database'],'migration':result,'version_updated':updated}))
        print('SPRINT3_TENANT_MIGRATION_OK')
        return 0
    except Refusal as ex:
        print('REFUSED: '+str(ex),file=sys.stderr)
        return 4
    except Exception as ex:
        # Avoid printing exception details that can include credentials/hostnames.
        print('FAILED: migration stopped; inspect DB state read-only and retry plan',file=sys.stderr)
        return 3

if __name__=='__main__':
    raise SystemExit(main())
