-- Paso 7D.1: read-only source inventory. Execute with psql.
-- Output contract: table | filas_origen. No row contents are selected.
\pset tuples_only on
\pset format unaligned
SELECT format(
    'SELECT %L AS "table", count(*)::bigint AS filas_origen FROM public.%I;',
    c.relname, c.relname
)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.relname
\gexec
