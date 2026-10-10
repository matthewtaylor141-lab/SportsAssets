-- READ-ONLY. Root-cause audit, group deploy-runtime, part 2: (a) the one
-- migration version applied out of version order, (b) the five production
-- tables that no migration creates (columns, constraints, indexes, rows), (c)
-- the dedicated plane's two heartbeat rows and the learn-loop switch. SELECT only.

\echo == 1 versions applied earlier than the version before them
SELECT version, applied_at, prev_version, prev_at
  FROM (SELECT version, applied_at,
               lag(version) OVER (ORDER BY version) AS prev_version,
               lag(applied_at) OVER (ORDER BY version) AS prev_at
          FROM schema_migrations) x
 WHERE applied_at < prev_at ORDER BY version;

\echo == 2 columns of the five tables no migration creates
SELECT c.table_name, c.ordinal_position AS pos, c.column_name, c.data_type, c.is_nullable, left(coalesce(c.column_default, ''), 60) AS column_default
  FROM information_schema.columns c
 WHERE c.table_schema = 'public'
   AND c.table_name IN ('bettor_learn_model', 'bettor_learn_prediction', 'bettor_incentive_journal', 'pmus_activity_archive')
 ORDER BY c.table_name, c.ordinal_position;

\echo == 3 constraints and indexes of those tables
SELECT conrelid::regclass::text AS on_table, conname AS name, left(pg_get_constraintdef(oid), 140) AS definition
  FROM pg_constraint
 WHERE conrelid::regclass::text IN ('bettor_learn_model', 'bettor_learn_prediction', 'bettor_incentive_journal', 'pmus_activity_archive')
 UNION ALL
SELECT tablename, indexname, left(indexdef, 140)
  FROM pg_indexes
 WHERE schemaname = 'public'
   AND tablename IN ('bettor_learn_model', 'bettor_learn_prediction', 'bettor_incentive_journal', 'pmus_activity_archive')
 ORDER BY 1, 2;

\echo == 4 rows and size of all five (planner estimates) and us_premap shape
SELECT c.relname, c.reltuples::bigint AS est_rows, pg_total_relation_size(c.oid) AS total_bytes
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relname IN ('bettor_learn_model', 'bettor_learn_prediction', 'bettor_incentive_journal', 'pmus_activity_archive', 'us_premap')
 ORDER BY 1;

\echo == 5 the learn-loop switch and the plane heartbeat rows
SELECT key, left(value::text, 80) AS value FROM ingestion_state WHERE key IN ('rn1x_learn') ORDER BY 1;
SELECT service, status, round(extract(epoch FROM now() - beat_at))::bigint AS age_s,
       left(detail::text, 900) AS detail_head
  FROM service_heartbeats WHERE service IN ('universal_market_plane') ORDER BY 1;
