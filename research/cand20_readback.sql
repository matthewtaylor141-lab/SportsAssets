-- Candidate 20 (closeout) readback, read-only: migrations, writer builds,
-- actual-lane admission on every execution intent, V3 decisions, the mirror
-- control and latest account snapshot, Audrey, latency.
\echo '== R0 · migrations 198-203 =='
SELECT version FROM schema_migrations WHERE version::text ~ '^(198|199|200|201|202|203)' ORDER BY 1;
\echo '== R0b · admission guard objects =='
SELECT conname, convalidated FROM pg_constraint WHERE conname = 'execution_intents_admission_ck';
SELECT tgname FROM pg_trigger WHERE tgname = 'execmirror_orders_intent_admitted';
\echo '== R1 · writer builds (collector cycle; reactive attempts last 2 h) =='
SELECT key, value->'writer'->>'build' AS cycle_writer_build, value->>'state' AS state,
       to_timestamp(coalesce((value->>'at')::float8, (value->>'beat_at')::float8)) AS at
  FROM ingestion_state WHERE key IN ('ext_pinnacle_last_cycle', 'pinnapi_feed_last');
SELECT detail->'writer'->>'build' AS writer_build, state, count(*) AS attempts, max(created_at) AS last_at
  FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '2 hours'
 GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 8;
\echo '== R2 · V3 decisions by verdict/refusal (last 2 h) =='
SELECT policy_version, verdict, refusal, count(*) AS n, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND decided_at > now() - interval '2 hours'
 GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 12;
\echo '== R3 · execution intents: admission (latest 20) =='
SELECT intent_id, strategy, policy_version, live_eligible, actual_state, actual_refusal,
       live_eligibility->'admission'->>'verdict' AS admission,
       live_eligibility->'admission'->'refusals' AS admission_refusals,
       live_eligibility->'admission'->'book_currency'->>'verdict' AS book_currency,
       live_eligibility->'admission'->'settlement'->>'compatibility' AS settlement,
       created_at
  FROM execution_intents ORDER BY created_at DESC LIMIT 20;
SELECT actual_state, actual_refusal, live_eligible, count(*) AS n, max(created_at) AS last_at
  FROM execution_intents GROUP BY 1, 2, 3 ORDER BY 4 DESC;
\echo '== R3b · any live-eligible intent without LIVE_ADMISSIBLE admission (must be 0 after 200) =='
SELECT count(*) AS violations FROM execution_intents
 WHERE live_eligible AND created_at > (SELECT coalesce(max(applied_at), now()) FROM schema_migrations
                                         WHERE version::text LIKE '200%')
   AND coalesce(live_eligibility->'admission'->>'verdict', '') <> 'LIVE_ADMISSIBLE';
\echo '== R4 · actual orders from intents (must be 0 while stopped) =='
SELECT state, count(*) FROM execmirror_orders WHERE execution_intent_id IS NOT NULL GROUP BY 1;
\echo '== R5 · mirror control and latest snapshot =='
SELECT enabled, stopped, cutover_at, left(account_fingerprint, 12) AS fp, scale, max_order_usd, revision
  FROM execmirror_control;
SELECT at, left(balances::text, 200) AS balances,
       jsonb_array_length(coalesce(positions, '[]'::jsonb)) AS positions,
       left(reconciliation::text, 200) AS reconciliation
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 1;
\echo '== R6 · Audrey reconciliation statuses =='
SELECT status, count(*) AS n, max(reconciled_at) AS last_at FROM smalllive_reconciliations GROUP BY 1;
\echo '== R7 · market-data subscription block (last cycle) =='
SELECT left((value->'market_subscription')::text, 700) AS market_subscription
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo '== R8 · Xavier policy artifact (migration 201) and collaboration loop (203) =='
SELECT policy_id, version, status, left(sha256, 16) AS sha256_prefix, created_at
  FROM agent_policy_artifacts ORDER BY created_at;
SELECT count(*) AS findings FROM agent_findings;
\echo '== R9 · migrations applied since 199 (with time) =='
SELECT version, applied_at FROM schema_migrations WHERE version::text ~ '^(199|200|201|202|203)' ORDER BY 1;
