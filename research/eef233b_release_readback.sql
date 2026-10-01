-- READ-ONLY. POST-RELEASE READBACK FOR eef233b (Derek same-day bootstrap fix).
-- R1 migration 181 recorded; R2 the partial unique index exists and the old
-- one-per-day constraint is gone; R3 today's research model runs (append-only;
-- non-decisive rows allowed); R4 the paper session, its single funding entry,
-- balances and decisions are unchanged by the release.

\echo '== R1 · migrations 180-181 =='
SELECT version, applied_at, left(content_sha, 12) AS sha FROM schema_migrations
 WHERE version >= '180' ORDER BY version;

\echo '== R2 · indexes and constraints on derek_research_model_runs =='
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'derek_research_model_runs' ORDER BY 1;
SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint
 WHERE conrelid = 'derek_research_model_runs'::regclass ORDER BY 1;

\echo '== R3 · research model runs, newest 10 =='
SELECT run_id, run_day, ran_at, outcome, recorded_at FROM derek_research_model_runs
 ORDER BY ran_at DESC LIMIT 10;

\echo '== R4 · paper session, funding and balances =='
SELECT session_id, started_at, status FROM paper_sessions ORDER BY started_at;
SELECT session_id, heartbeat_at, passes, errors, mutation_attempts FROM paper_session_health;
SELECT kind, count(*) AS entries, sum(cash_delta_usd) AS cash_delta FROM paper_ledger GROUP BY 1 ORDER BY 1;
SELECT seq, kind, cash_after_usd, reserved_after_usd, cash_after_usd - reserved_after_usd AS available_usd
  FROM paper_ledger ORDER BY seq DESC LIMIT 3;
SELECT verdict, coalesce(refusal, '') AS refusal, count(*) AS decisions, max(decided_at) AS newest
  FROM paper_decisions GROUP BY 1, 2 ORDER BY 3 DESC;
SELECT count(*) AS paper_orders FROM paper_orders;
SELECT count(*) AS paper_fills FROM paper_fills;
