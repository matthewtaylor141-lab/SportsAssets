-- Read only, one statement/snapshot. Never a claim about real execution.
-- Change the window to the exact serving cutover for release evidence.
WITH bounds AS (
  SELECT now() - interval '1 hour' AS since, now() AS read_at
), vals AS (
  SELECT v.id, v.decided_at, v.provider, v.probability, v.us_market_slug,
         v.refusals, v.settlement_comparison::jsonb->'reference_input' AS src
    FROM external_valuations v, bounds w
   WHERE v.decided_at >= w.since AND v.decided_at <= w.read_at
     AND v.experiment_id='EXT_PINNACLE_DEVIG_V1_SHADOW'
     AND v.record_purpose='ENTRY_DECISION'
), decisions AS (
  SELECT d.decision_id, d.valuation_id, d.verdict, d.refusal, d.strategy,
         d.us_market_slug, d.holding_side, d.pinnacle, v.src
    FROM paper_decisions d JOIN vals v ON v.id=d.valuation_id
   WHERE d.account_id='paper_acct_main'
     AND v.provider='pinnapi.com/raw-websocket'
), qualified AS (
  SELECT * FROM decisions
   WHERE pinnacle::jsonb->>'qualified'='true'
     AND coalesce(pinnacle::jsonb->'final_reference_check'->>'qualified','true')='true'
), fills AS (
  SELECT DISTINCT f.fill_id, f.group_id, f.us_market_slug, f.holding_side,
         f.qty, f.gross_usd, f.fee_usd, f.order_id
    FROM paper_fills f JOIN paper_orders o ON o.order_id=f.order_id
    JOIN decisions d ON d.decision_id=o.decision_id
   WHERE f.account_id='paper_acct_main' AND o.role='ENTRY'
     AND f.role='ENTRY' AND f.direction='BUY'
), latency AS (
  SELECT (src->'decision_check'->>'receipt_to_evaluation_ms')::double precision AS ms
    FROM vals
   WHERE provider='pinnapi.com/raw-websocket'
     AND src->'decision_check'->>'ok'='true'
     AND jsonb_typeof(src->'decision_check'->'receipt_to_evaluation_ms')='number'
)
SELECT jsonb_build_object(
  'read_at', (SELECT read_at FROM bounds),
  'since', (SELECT since FROM bounds),
  'verdict', CASE WHEN EXISTS(SELECT 1 FROM qualified)
                  THEN 'PRIMARY_SOURCE_EXERCISED'
                  ELSE 'PRIMARY_SOURCE_NOT_EXERCISED' END,
  'valuations', (SELECT count(*) FROM vals),
  'ws_source_valuations', (SELECT count(*) FROM vals WHERE provider='pinnapi.com/raw-websocket'),
  'ws_probabilities_available', (SELECT count(*) FROM vals WHERE provider='pinnapi.com/raw-websocket' AND probability IS NOT NULL),
  'paper_decisions_using_ws_rows', (SELECT count(*) FROM decisions),
  'primary_reference_qualified_decisions', (SELECT count(*) FROM qualified),
  'paper_entries', (SELECT count(*) FROM decisions WHERE verdict='ENTER'),
  'distinct_filled_position_groups', (SELECT count(DISTINCT group_id) FROM fills),
  'distinct_filled_contract_sides', (SELECT count(DISTINCT (us_market_slug,holding_side)) FROM fills),
  'initial_simulated_cost_including_fees', (SELECT sum(gross_usd+fee_usd) FROM fills),
  'receipt_to_collector_evaluation_ms', (SELECT jsonb_build_object(
    'n', count(*), 'p50', percentile_cont(.5) WITHIN GROUP (ORDER BY ms),
    'p95', percentile_cont(.95) WITHIN GROUP (ORDER BY ms), 'max', max(ms)) FROM latency),
  'latest_source_trace', (SELECT to_jsonb(t) FROM (
    SELECT id, decided_at, probability, us_market_slug, refusals, src
      FROM vals WHERE provider='pinnapi.com/raw-websocket' ORDER BY id DESC LIMIT 1) t),
  'latest_paper_trace', (SELECT to_jsonb(t) FROM (
    SELECT * FROM decisions ORDER BY valuation_id DESC,decision_id DESC LIMIT 1) t),
  'latency_scope', 'Collector evaluation only; not queue wait, order acceptance or fill latency.',
  'execution_scope', 'Simulated fills only. No live mirror results inferred.'
) AS pinnapi_primary_readback;
