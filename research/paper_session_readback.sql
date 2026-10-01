-- READ-ONLY. THE $500,000 PAPER SESSION, READ BACK FROM PRODUCTION.
--
-- Simulated execution against live market data. Nothing here writes.
--
-- S0 the paper pass heartbeat in ingestion_state
-- S1 control row and accounts (exactly one account, 500000 starting cash)
-- S2 sessions (exactly one ACTIVE expected) and session health heartbeat,
--    passes, errors, venue-mutation attempts (expected 0)
-- S3 the ledger: funding entries (exactly one INITIAL_FUNDING expected),
--    entries by kind, and the latest cash / reserved
-- S4 decisions by verdict/refusal (Derek), handoffs and Xavier reviews,
--    Audrey reports, equity snapshots
-- S5 agent status heartbeats for derek / xavier / audrey

\echo '== S0 · the paper pass heartbeat (last pass result, refusal and reason) =='
SELECT to_timestamp((value->>'written_at')::float8) AS written_at,
       value->>'refusal' AS refusal, left(value->>'why', 300) AS why,
       left(value::text, 1500) AS digest
  FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo '== S1 · control and accounts =='
SELECT control_key, enabled, updated_by, updated_at FROM paper_control;
SELECT account_id, starting_cash_usd, currency, data_label, created_at
  FROM paper_accounts;

\echo '== S2 · sessions and health =='
SELECT session_id, account_id, started_at, status, simulator_version, config_sha
  FROM paper_sessions ORDER BY started_at;
SELECT session_id, heartbeat_at,
       round(extract(epoch FROM now() - heartbeat_at)::numeric, 1) AS heartbeat_age_s,
       passes, errors, mutation_attempts, left(coalesce(last_error, ''), 160) AS last_error
  FROM paper_session_health;

\echo '== S3 · ledger =='
SELECT kind, count(*) AS entries, sum(cash_delta_usd) AS cash_delta,
       sum(reserved_delta_usd) AS reserved_delta
  FROM paper_ledger GROUP BY kind ORDER BY kind;
SELECT seq, kind, cash_after_usd, reserved_after_usd,
       cash_after_usd - reserved_after_usd AS available_usd, committed_at
  FROM paper_ledger ORDER BY seq DESC LIMIT 5;

\echo '== S4 · agent activity =='
SELECT verdict, coalesce(refusal, '') AS refusal, count(*) AS decisions,
       max(decided_at) AS newest
  FROM paper_decisions GROUP BY 1, 2 ORDER BY decisions DESC LIMIT 15;
SELECT count(*) AS handoffs FROM paper_handoffs;
SELECT count(*) AS xavier_reviews FROM paper_xavier_reviews;
SELECT count(*) AS audrey_reports FROM paper_audrey_reports;
SELECT count(*) AS equity_snapshots FROM paper_equity_snapshots;

\echo '== S5 · agent heartbeats =='
SELECT agent_id, state, activity, last_heartbeat_at,
       round(extract(epoch FROM now() - last_heartbeat_at)::numeric, 1) AS age_s
  FROM agent_status ORDER BY agent_id;
