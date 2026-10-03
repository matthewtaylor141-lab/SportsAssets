-- Candidate 19 readback (read-only): the serving writer build, migration 198,
-- the V3 investment policy's PinnAPI-only qualification on WS decisions,
-- Audrey's NOT_MIRRORED rows, the mirror control and its latest snapshot.
\echo '== R0 · migrations 195-198 =='
SELECT version FROM schema_migrations WHERE version::text ~ '^(195|196|197|198)' ORDER BY 1;
\echo '== R1 · writer builds (collector cycle; reactive attempts last 2 h) =='
SELECT key, value->'writer'->>'build' AS cycle_writer_build, value->>'state' AS state,
       to_timestamp(coalesce((value->>'at')::float8, (value->>'beat_at')::float8)) AS at
  FROM ingestion_state WHERE key IN ('ext_pinnacle_last_cycle', 'pinnapi_feed_last');
SELECT detail->'writer'->>'build' AS writer_build, state, count(*) AS attempts, max(created_at) AS last_at
  FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '2 hours'
 GROUP BY 1, 2 ORDER BY 4 DESC;
\echo '== R2 · completed-game decisions by policy version and refusal (last 2 h) =='
SELECT policy_version, verdict, refusal, count(*) AS n, max(decided_at) AS last_at
  FROM paper_decisions
 WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND decided_at > now() - interval '2 hours'
 GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 20;
\echo '== R3 · V3 decisions with their probability authority (latest 15) =='
SELECT d.decision_id, d.decided_at, d.verdict, d.refusal, v.provider, v.outcome_books,
       d.pinnacle->'probability_authority'->>'basis' AS authority_basis,
       d.pinnacle->'probability_authority'->>'evidence' AS evidence,
       d.pinnacle->'probability_authority'->>'outcome_books' AS authority_books,
       d.pinnacle->'contract_match'->'checks' @> '[{"check":"probability_qualified_by_the_lane","passed":true}]'::jsonb
         AS probability_qualified,
       round(d.p_pinnacle::numeric, 4) AS p_pinnacle, d.limit_price, d.proposed_qty,
       d.policy_decision->>'gross_edge_pp' AS gross_edge_pp,
       d.policy_decision->>'net_expected_profit_usd' AS net_ev_usd
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
   AND d.policy_version = 'PINNACLE_COMPLETED_GAME_PAPER_V3'
 ORDER BY d.decided_at DESC LIMIT 15;
\echo '== R4 · Audrey reconciliation statuses =='
SELECT status, count(*) AS n, max(reconciled_at) AS last_at FROM smalllive_reconciliations GROUP BY 1;
\echo '== R5 · mirror control, latest snapshot, orders by state =='
SELECT enabled, stopped, cutover_at, left(account_fingerprint, 12) AS fp, scale, max_order_usd, revision
  FROM execmirror_control;
SELECT at, left(balances::text, 160) AS balances, jsonb_array_length(coalesce(positions, '[]'::jsonb)) AS positions,
       left(reconciliation::text, 200) AS reconciliation
  FROM execmirror_snapshots ORDER BY at DESC LIMIT 1;
SELECT state, exclusion, strategy, count(*) AS n, max(created_at) AS last_at
  FROM execmirror_orders GROUP BY 1, 2, 3 ORDER BY 5 DESC;
\echo '== R6 · market-data subscription block (last cycle) =='
SELECT left((value->'market_subscription')::text, 700) AS market_subscription
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
