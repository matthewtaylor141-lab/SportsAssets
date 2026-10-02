-- Why no paper decision since the 50ca411 cutover (new writer 13:49:49Z)?
-- Read-only: valuations written, paper hook failures, runtime heartbeat.
\echo '== G0 · entry-experiment valuations per 5 min since 13:30 (id range) =='
SELECT date_trunc('minute', received_at) - (extract(minute FROM received_at)::int % 5) * interval '1 minute' AS bucket,
       experiment_id, count(*) AS n, min(id) AS first_id, max(id) AS last_id
  FROM external_valuations
 WHERE received_at >= '2026-10-02 13:30:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== G1 · paper hook failures since 13:30 =='
SELECT recorded_at, valuation_id, strategy, stage, outcome, elapsed_s, left(error, 300) AS error, detail
  FROM paper_hook_failures
 WHERE recorded_at >= '2026-10-02 13:30:00+00'
 ORDER BY recorded_at DESC LIMIT 30;

\echo '== G2 · paper runtime heartbeat and session control rows =='
SELECT key, left(value::text, 1500) AS value
  FROM ingestion_state
 WHERE key IN ('paper_session_last_pass', 'paper_session', 'paper.session.v1')
    OR key LIKE 'paper_session%' OR key LIKE 'paper.%'
 ORDER BY key;

\echo '== G3 · the latest decision rows of any strategy =='
SELECT decided_at, strategy, policy_version, verdict, refusal, valuation_id
  FROM paper_decisions WHERE account_id = 'paper_acct_main'
 ORDER BY decided_at DESC LIMIT 6;
