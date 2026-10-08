-- Paso 7D.1: read-only dependency audit for public objects. Execute with psql.
-- The result contains names/definitions, never business row contents.

SELECT 'foreign_key' AS object_kind, con.conname AS object_name,
       ns.nspname AS referenced_schema, rel.relname AS referenced_object
FROM pg_constraint AS con
JOIN pg_class AS src ON src.oid = con.conrelid
JOIN pg_namespace AS src_ns ON src_ns.oid = src.relnamespace
JOIN pg_class AS rel ON rel.oid = con.confrelid
JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
WHERE src_ns.nspname = 'public' AND con.contype = 'f'
  AND ns.nspname <> 'public'
UNION ALL
SELECT 'view', c.relname, n.nspname, pg_get_viewdef(c.oid, true)
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('v', 'm')
  AND pg_get_viewdef(c.oid, true) ~* '(auth|storage|saas_control|realtime|extensions)[.]'
UNION ALL
SELECT 'routine', p.oid::regprocedure::text, n.nspname, p.prosrc
FROM pg_proc AS p
JOIN pg_namespace AS n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND (pg_get_functiondef(p.oid) ~* '(auth|storage|saas_control|realtime|extensions)[.]'
       OR pg_get_functiondef(p.oid) ~* 'search_path')
UNION ALL
SELECT 'trigger', t.tgname, n.nspname, pg_get_triggerdef(t.oid)
FROM pg_trigger AS t
JOIN pg_class AS c ON c.oid = t.tgrelid
JOIN pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
  AND pg_get_triggerdef(t.oid) ~* '(auth|storage|saas_control|realtime|extensions)[.]'
UNION ALL
SELECT 'default_or_policy', c.relname, n.nspname,
       coalesce(pg_get_expr(ad.adbin, ad.adrelid), '') || ' ' ||
       coalesce(pol.polname, '') || ' ' || coalesce(pg_get_expr(pol.polqual, pol.polrelid), '') || ' ' ||
       coalesce(pg_get_expr(pol.polwithcheck, pol.polrelid), '')
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef AS ad ON ad.adrelid = c.oid
LEFT JOIN pg_policy AS pol ON pol.polrelid = c.oid
WHERE n.nspname = 'public'
  AND (coalesce(pg_get_expr(ad.adbin, ad.adrelid), '') || ' ' ||
       coalesce(pol.polname, '') || ' ' || coalesce(pg_get_expr(pol.polqual, pol.polrelid), '') || ' ' ||
       coalesce(pg_get_expr(pol.polwithcheck, pol.polrelid), '')
        ~* '(auth|storage|saas_control|realtime|extensions)[.]')
UNION ALL
SELECT 'catalog_dependency', c.relname, src_ns.nspname,
       pg_identify_object(d.refclassid, d.refobjid, d.refobjsubid)
FROM pg_depend AS d
JOIN pg_class AS c ON c.oid = d.objid
JOIN pg_namespace AS src_ns ON src_ns.oid = c.relnamespace
WHERE src_ns.nspname = 'public'
  AND pg_identify_object(d.refclassid, d.refobjid, d.refobjsubid) ~* '(auth|storage|saas_control|realtime|extensions)[.]'
ORDER BY 1, 2;
