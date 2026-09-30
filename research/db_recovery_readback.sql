-- READ-ONLY. DATABASE RECOVERY AFTER THE 2026-09-30 STORAGE INCREASE.
--
-- Answers one question: did the instance come back with the data it had,
-- or was anything reset or replaced? It reads extents (counts and the
-- oldest/newest timestamps) of the application ledger and the agent
-- tables, the migration ledger, and when the server last started. It
-- writes nothing, and it selects no credential, balance detail or message
-- body.
--
-- R1 server identity, start time, database size
-- R2 migration ledger (a reset database would have few or none)
-- R3 the application ledger the Command Centre reads (mirror_books /
--    mirror_orders): the exact read that returned LEDGER_READ_FAILED
-- R4 decision tables (oldest/newest rows) and the largest tables
-- R5 funded activity (none is expected; funded submission stays disabled)

\echo '== R1 · server =='
SELECT now() AS server_time, pg_postmaster_start_time() AS server_started_at,
       current_setting('server_version') AS version,
       pg_size_pretty(pg_database_size(current_database())) AS database_size;

\echo '== R2 · migrations =='
SELECT count(*) AS migrations_applied, min(version) AS first, max(version) AS last,
       max(applied_at) AS last_applied_at
  FROM schema_migrations;

\echo '== R3 · application ledger (the Command Centre read) =='
SELECT count(*) AS books, count(*) FILTER (WHERE state <> 'closed') AS open_books,
       min(opened_at) AS oldest_opened, max(updated_at) AS newest_update
  FROM mirror_books;
SELECT id, state, updated_at
  FROM mirror_books WHERE state <> 'closed' ORDER BY updated_at DESC LIMIT 3;
SELECT count(*) AS orders, min(placed_at) AS oldest, max(placed_at) AS newest
  FROM mirror_orders;

\echo '== R4 · decision tables and the largest tables =='
-- Only tables in the serving schema (dff544c); the agent tables arrive with
-- the foundation release's migrations 152-156 and are read after it.
SELECT 'bettor_xavier_decisions' AS tbl, count(*) AS n, min(decided_at) AS oldest,
       max(decided_at) AS newest FROM bettor_xavier_decisions
UNION ALL SELECT 'ingestion_state', count(*), NULL, NULL FROM ingestion_state;
SELECT relname, n_live_tup, last_autoanalyze
  FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 15;

\echo '== R5 · funded activity =='
SELECT (SELECT count(*) FROM bettor_funded_intents) AS funded_intents,
       (SELECT count(*) FROM bettor_funded_fills)   AS funded_fills;
