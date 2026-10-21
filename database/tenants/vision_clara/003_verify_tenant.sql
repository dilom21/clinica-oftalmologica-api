-- Paso 7D.1: read-only source-vs-target verification. Execute with psql.
-- Supply source_database and target_database as connection names/URLs without
-- passwords (prefer .pgpass or an approved password manager).
\if :{?source_database}
\else
  \echo 'Read-only precondition: source_database is required.'
  \quit 3
\endif
\if :{?target_database}
\else
  \echo 'Read-only precondition: target_database is required; tenant may not exist yet.'
  \quit 3
\endif

\connect :source_database
SELECT 'source' AS side, c.table_name, c.column_name, c.data_type,
       c.is_nullable, c.ordinal_position
FROM information_schema.columns AS c
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position;
SELECT 'source' AS side, con.conrelid::regclass::text AS table_name,
       con.conname, con.contype, pg_get_constraintdef(con.oid)
FROM pg_constraint AS con
JOIN pg_namespace AS n ON n.oid = con.connamespace
WHERE n.nspname = 'public' AND con.contype IN ('p', 'f', 'u')
ORDER BY 2, 3;
SELECT 'source' AS side, schemaname, tablename, indexname, indexdef
FROM pg_indexes WHERE schemaname = 'public' ORDER BY tablename, indexname;
SELECT 'source' AS side, n.nspname, p.oid::regprocedure::text
FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' ORDER BY 3;
SELECT 'source' AS side, n.nspname, c.relname, t.tgname
FROM pg_trigger AS t JOIN pg_class AS c ON c.oid = t.tgrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal ORDER BY 3, 4;
SELECT 'source' AS side, c.relname AS table_name, count(*)::bigint AS filas
FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
GROUP BY c.relname ORDER BY c.relname;

\connect :target_database
SELECT 'target' AS side, c.table_name, c.column_name, c.data_type,
       c.is_nullable, c.ordinal_position
FROM information_schema.columns AS c
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position;
SELECT 'target' AS side, con.conrelid::regclass::text AS table_name,
       con.conname, con.contype, pg_get_constraintdef(con.oid)
FROM pg_constraint AS con
JOIN pg_namespace AS n ON n.oid = con.connamespace
WHERE n.nspname = 'public' AND con.contype IN ('p', 'f', 'u')
ORDER BY 2, 3;
SELECT 'target' AS side, schemaname, tablename, indexname, indexdef
FROM pg_indexes WHERE schemaname = 'public' ORDER BY tablename, indexname;
SELECT 'target' AS side, n.nspname, p.oid::regprocedure::text
FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' ORDER BY 3;
SELECT 'target' AS side, n.nspname, c.relname, t.tgname
FROM pg_trigger AS t JOIN pg_class AS c ON c.oid = t.tgrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal ORDER BY 3, 4;
SELECT 'target' AS side, c.relname AS table_name, count(*)::bigint AS filas
FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
GROUP BY c.relname ORDER BY c.relname;
