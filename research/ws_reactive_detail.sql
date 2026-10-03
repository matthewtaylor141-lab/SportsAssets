-- WebSocket-triggered valuations in detail, and the periodic collector cycles
-- around them (read-only). Cycle start = ext_candidate_outcomes.cycle_at;
-- the cycle's ledger write time (max recorded_at) is reported as its end.
\echo '== V1 · the WS-triggered valuation rows (lane refusals, purpose, price fields) =='
WITH ids AS (
  SELECT DISTINCT jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint AS id
    FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours')
SELECT v.id, v.provider, v.record_purpose, v.observed_at, v.received_at, v.age_s,
       v.sport_family, v.market, v.period, v.us_market_slug, v.probability,
       v.executable_price, v.cost_per_contract, v.decision, v.admissible,
       v.refusals, v.why, left(v.execution_estimate::text, 400) AS execution_estimate,
       left(v.calibration_only_evidence::text, 400) AS calibration_only_evidence
  FROM ids JOIN external_valuations v ON v.id = ids.id ORDER BY v.id;
\echo '== V2 · periodic collector cycles in the last 90 minutes (start, ledger write, events) =='
SELECT cycle_id, writer, min(cycle_at) AS cycle_started, max(recorded_at) AS ledger_written,
       count(*) AS events, count(*) FILTER (WHERE outcome = 'REFUSED') AS refused
  FROM ext_candidate_outcomes WHERE cycle_at > now() - interval '90 minutes'
 GROUP BY 1, 2 ORDER BY 3;
\echo '== V3 · each WS attempt placed against the cycle windows =='
WITH c AS (
  SELECT cycle_id, min(cycle_at) AS s, max(recorded_at) AS e
    FROM ext_candidate_outcomes WHERE cycle_at > now() - interval '6 hours' GROUP BY 1),
a AS (
  SELECT attempt_id, to_timestamp((detail->>'received_at')::float8) AS rx,
         to_timestamp((detail->>'finished_at')::float8) AS fin
    FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours')
SELECT a.attempt_id, a.rx AS ws_received_at, a.fin AS attempt_finished_at,
       (SELECT max(e) FROM c WHERE e <= a.rx) AS prev_cycle_ledger_written,
       (SELECT min(s) FROM c WHERE s >= a.rx) AS next_cycle_started,
       (SELECT string_agg(cycle_id, ',') FROM c WHERE s <= a.rx AND e >= a.rx) AS cycle_running_at_receipt
  FROM a ORDER BY a.rx;
