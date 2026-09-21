-- SCHEMA PROBE -- what the observation and settlement tables actually hold.
--
-- WHY THIS RUNS FIRST. research-sql runs psql with ON_ERROR_STOP=1, so one
-- wrong column name aborts the whole file and returns nothing. Every
-- substantive query I want to run next -- depth shape, complement identity,
-- settlement predicates, maturity -- names columns I have not verified.
-- Guessing them and losing the run is the expensive version of asking.
--
-- It is also the direct lesson of the last three errors: depth declared
-- ABSENT_IN_CAPTURE_SCHEMA without querying the columns that held it, a
-- reconciliation blocker declared without checking the client's attributes,
-- and a fee schedule called missing while it sat implemented in research/.
-- Each time the assertion was made from the wrong table. This asks first.
--
-- READ ONLY. Catalog reads and counts. Nothing is altered.

\echo == 1. COLUMNS OF bettor_state_observations ==
SELECT ordinal_position AS pos, column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_name = 'bettor_state_observations'
 ORDER BY ordinal_position;

\echo
\echo == 2. COLUMNS OF bettor_state_settlements ==
SELECT ordinal_position AS pos, column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_name = 'bettor_state_settlements'
 ORDER BY ordinal_position;

\echo
\echo == 3. ANY OTHER bettor_ TABLE, and how many rows each holds ==
SELECT c.relname AS table_name, c.reltuples::bigint AS approx_rows
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname = 'public'
   AND c.relname LIKE 'bettor%'
 ORDER BY c.relname;

\echo
\echo == 4. ROW COUNTS AND THE OBSERVED WINDOW ==
SELECT count(*) AS rows,
       min(observed_at) AS earliest,
       max(observed_at) AS latest,
       count(DISTINCT market_id) AS markets
  FROM bettor_state_observations;

\echo
\echo == 5. ONE ROW IN FULL, as JSON, so every key is visible at once ==
-- Column names come back exactly as stored, including the depth keys whose
-- absence I previously asserted. Values are market data, not credentials.
SELECT jsonb_pretty(to_jsonb(o.*)) AS newest_row
  FROM bettor_state_observations o
 ORDER BY o.observed_at DESC
 LIMIT 1;

\echo
\echo == 6. ONE SETTLEMENT ROW IN FULL, if any exist ==
SELECT jsonb_pretty(to_jsonb(s.*)) AS newest_settlement
  FROM bettor_state_settlements s
 ORDER BY 1
 LIMIT 1;
