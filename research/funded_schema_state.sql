-- WHY THE FUNDED SECTION READS AN ERROR IN PRODUCTION, asked of production.
--
-- The 07c67f3 boot log says migration 126 raised
-- `UndefinedFunctionError: function bettor_funded_order_is_outstanding(text)
-- does not exist` and the whole file rolled back, so `residual_qty` never
-- appeared and the funded command-centre section reports the failure. The same
-- file applies cleanly on a fresh database and under the runner's own outer
-- transaction, so the cause is pre-existing state in THIS database and nowhere
-- else. This asks what that state is.
--
-- READ-ONLY. Every statement below is a SELECT against the catalogue.

\echo == 1 . WHICH FUNDED MIGRATIONS THIS DATABASE HAS RECORDED ==
SELECT version, applied_at
  FROM schema_migrations
 WHERE version >= '120'
 ORDER BY version;

\echo
\echo == 2 . EVERY FUNCTION WHOSE NAME BEGINS bettor_funded, WITH ITS SIGNATURE ==
SELECT n.nspname                        AS schema,
       p.proname                        AS function,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       pg_get_function_result(p.oid)    AS returns,
       p.provolatile                    AS volatility
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE p.proname LIKE 'bettor_funded%'
 ORDER BY n.nspname, p.proname, arguments;

\echo
\echo == 3 . THE SEARCH PATH THIS SESSION RESOLVES AGAINST ==
SELECT current_setting('search_path') AS search_path,
       current_schema()               AS current_schema,
       current_user                   AS whoami;

\echo
\echo == 4 . EVERY INDEX ON THE TWO FUNDED TABLES, WITH ITS DEFINITION ==
SELECT schemaname AS schema, tablename AS table_name, indexname AS index_name,
       indexdef
  FROM pg_indexes
 WHERE tablename IN ('bettor_funded_intents', 'bettor_funded_fills',
                     'bettor_funded_economics', 'bettor_funded_discrepancies')
 ORDER BY tablename, indexname;

\echo
\echo == 5 . WHICH COLUMNS THE FUNDED TABLES ACTUALLY HAVE ==
SELECT c.table_schema AS schema, c.table_name, c.column_name, c.data_type,
       c.is_nullable
  FROM information_schema.columns c
 WHERE c.table_name IN ('bettor_funded_intents', 'bettor_funded_fills',
                        'bettor_funded_economics', 'bettor_funded_discrepancies')
 ORDER BY c.table_name, c.ordinal_position;

\echo
\echo == 6 . AND WHETHER THE FUNDED TABLES ARE DUPLICATED ACROSS SCHEMAS ==
SELECT n.nspname AS schema, c.relname AS relation, c.relkind
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relname LIKE 'bettor_funded%'
   AND c.relkind IN ('r', 'v', 'm', 'p')
 ORDER BY c.relname, n.nspname;

\echo
\echo == 7 . HOW MANY FUNDED ROWS EXIST AT ALL ==
SELECT (SELECT count(*) FROM bettor_funded_intents) AS intents,
       (SELECT count(*) FROM bettor_funded_fills)   AS fills;
