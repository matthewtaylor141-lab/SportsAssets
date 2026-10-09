-- READ-ONLY. RC6.2 enter-integrity: WHY 13 exploration ENTER decisions since
-- the quarantine (2026-10-06 05:37:53Z) have neither a paper order nor a
-- refusal (census row or PAPER_RISK_REFUSED finding), only an
-- ENTER_WITHOUT_ORDER finding. For each: the decision row's timing
-- (decided_at = decision clock, recorded_at = INSERT transaction instant),
-- the decision path (decided_via), the execution intent (written between the
-- INSERT and the order), every evaluation attempt / hook failure on the same
-- valuation, the sibling strategies' decisions on it and the reactive
-- evaluation attempt that carried the valuation (state, deadline, elapsed).
-- Contrast: the timing of the exploration ENTERs that DID reach the ledger.
-- Every statement is a SELECT.

\echo C0 the 13: decision timing, path, intent and the finding
WITH c AS (
  SELECT d.* FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
     AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id))
SELECT c.decision_id, c.valuation_id, c.session_id,
       to_char(c.decided_at, 'MM-DD HH24:MI:SS.MS') AS decided,
       round(extract(epoch FROM c.recorded_at - c.decided_at)::numeric, 3) AS insert_after_s,
       c.pinnacle->>'decided_via' AS via,
       (SELECT round(extract(epoch FROM i.created_at - c.recorded_at)::numeric, 3)
          FROM execution_intents i WHERE i.decision_id = c.decision_id) AS intent_after_insert_s,
       (SELECT i.actual_state FROM execution_intents i WHERE i.decision_id = c.decision_id) AS intent_state,
       (SELECT string_agg(f.kind || '@' || to_char(f.found_at, 'HH24:MI:SS'), ' ')
          FROM paper_audrey_findings f WHERE f.subject = c.decision_id) AS findings,
       (SELECT count(*) FROM paper_profitability_evaluations e WHERE e.decision_id = c.decision_id) AS bind_evals,
       (SELECT count(*) FROM paper_shadow_counterfactuals s WHERE s.decision_id = c.decision_id) AS shadows,
       c.proposed_qty, c.limit_price
  FROM c ORDER BY c.decided_at;

\echo C1 every evaluation attempt on the 13 valuations (all strategies)
WITH c AS (
  SELECT d.decision_id, d.valuation_id, d.decided_at FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
     AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id))
SELECT c.valuation_id, a.strategy, a.via, a.attempt_no, a.outcome, a.verdict,
       left(coalesce(a.refusal, ''), 50) AS refusal, a.elapsed_s,
       round(extract(epoch FROM a.at - c.decided_at)::numeric, 3) AS at_after_decided_s,
       left(a.detail::text, 160) AS detail
  FROM c JOIN paper_evaluation_attempts a ON a.valuation_id = c.valuation_id
 ORDER BY c.decided_at, a.at;

\echo C2 every hook failure on the 13 valuations
WITH c AS (
  SELECT d.decision_id, d.valuation_id, d.decided_at FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
     AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id))
SELECT c.valuation_id, h.strategy, h.stage, h.outcome, h.elapsed_s,
       round(extract(epoch FROM h.recorded_at - c.decided_at)::numeric, 3) AS after_decided_s,
       left(coalesce(h.error, ''), 140) AS error, left(h.detail::text, 140) AS detail
  FROM c JOIN paper_hook_failures h ON h.valuation_id = c.valuation_id
 ORDER BY c.decided_at, h.recorded_at;

\echo C3 the sibling decisions on the 13 valuations (all strategies)
WITH c AS (
  SELECT d.decision_id, d.valuation_id, d.decided_at FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
     AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id))
SELECT c.valuation_id, s.strategy, s.verdict, left(coalesce(s.refusal, ''), 50) AS refusal,
       s.pinnacle->>'decided_via' AS via,
       round(extract(epoch FROM s.decided_at - c.decided_at)::numeric, 3) AS decided_rel_s,
       round(extract(epoch FROM s.recorded_at - s.decided_at)::numeric, 3) AS insert_after_s,
       EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = s.decision_id) AS has_order,
       (SELECT string_agg(r.stage || ':' || r.refusal, ' ') FROM paper_entry_refusal_census r
         WHERE r.decision_id = s.decision_id) AS census
  FROM c JOIN paper_decisions s ON s.valuation_id = c.valuation_id
 ORDER BY c.decided_at, s.decided_at;

\echo C4 the reactive evaluation attempt that carried each of the 13 valuations
WITH c AS (
  SELECT d.decision_id, d.valuation_id, d.decided_at FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'
     AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id))
SELECT c.valuation_id, p.state, p.event_id,
       p.detail->>'deadline_s' AS deadline_s,
       round(((p.detail->>'finished_at')::float8 - (p.detail->>'evaluation_started_at')::float8)::numeric, 3) AS ran_s,
       round(((p.detail->>'finished_at')::float8 - extract(epoch FROM c.decided_at))::numeric, 3) AS finished_after_decided_s,
       jsonb_array_length(coalesce(p.detail->'valuation_ids', '[]'::jsonb)) AS n_valuations,
       p.detail->>'session_wait_s' AS session_wait_s,
       p.detail->>'held' AS held, p.detail->>'hot' AS hot,
       left(p.detail->'writer'->>'build', 8) AS build
  FROM c JOIN pinnapi_reactive_attempts p
    ON p.created_at BETWEEN c.decided_at - interval '30 seconds' AND c.decided_at + interval '5 seconds'
   AND p.detail->'valuation_ids' @> to_jsonb(c.valuation_id)
 ORDER BY c.decided_at;

\echo C5 contrast: exploration ENTERs since the quarantine by path, with census/order/neither, and INSERT -> census timing
WITH d AS (
  SELECT d.decision_id, d.decided_at, d.recorded_at, d.pinnacle->>'decided_via' AS via
    FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:53+00'),
j AS (
  SELECT d.*, (SELECT min(r.recorded_at) FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id) AS census_at,
         EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id) AS has_order
    FROM d)
SELECT via, count(*) AS enter, count(*) FILTER (WHERE has_order) AS with_order,
       count(*) FILTER (WHERE census_at IS NOT NULL) AS with_census,
       count(*) FILTER (WHERE NOT has_order AND census_at IS NULL) AS neither,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - decided_at))::numeric, 3) AS p50_insert_after_s,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM recorded_at - decided_at))::numeric, 3) AS p99_insert_after_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM census_at - recorded_at))::numeric, 3) AS p50_census_after_insert_s,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM census_at - recorded_at))::numeric, 3) AS p99_census_after_insert_s,
       round(max(extract(epoch FROM census_at - recorded_at))::numeric, 3) AS max_census_after_insert_s
  FROM j GROUP BY via ORDER BY 2 DESC;

\echo C6 reactive evaluation attempts since the quarantine by state, with the run time of the TIMEOUT / CANCELLED ones
SELECT state, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY ((detail->>'finished_at')::float8 - (detail->>'evaluation_started_at')::float8))::numeric, 3) AS p50_ran_s,
       round(max((detail->>'finished_at')::float8 - (detail->>'evaluation_started_at')::float8)::numeric, 3) AS max_ran_s,
       min(detail->>'deadline_s') AS deadline_s,
       count(*) FILTER (WHERE jsonb_array_length(coalesce(detail->'valuation_ids', '[]'::jsonb)) > 0) AS with_valuations
  FROM pinnapi_reactive_attempts
 WHERE created_at >= '2026-10-06 05:37:53+00'
 GROUP BY 1 ORDER BY 2 DESC;

\echo C7 exploration attempts since the quarantine by via and outcome (TIMEOUT / ERROR rows carry the cut)
SELECT via, outcome, count(*) AS n, round(max(elapsed_s)::numeric, 3) AS max_elapsed_s,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter_rows
  FROM paper_evaluation_attempts
 WHERE strategy = 'PINNACLE_EXPLORATION_PAPER' AND at >= '2026-10-06 05:37:53+00'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo C8 completed-game and maker ENTERs with neither an order nor a refusal since 2026-10-06 (same defect elsewhere?)
SELECT d.strategy, count(*) AS neither,
       min(d.decided_at) AS first, max(d.decided_at) AS last
  FROM paper_decisions d
 WHERE d.account_id = 'paper_acct_main' AND d.verdict = 'ENTER'
   AND d.decided_at >= '2026-10-06 00:00+00'
   AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
   AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id)
   AND NOT EXISTS (SELECT 1 FROM paper_audrey_findings f WHERE f.subject = d.decision_id
                    AND f.kind = 'PAPER_RISK_REFUSED_THE_ORDER')
 GROUP BY 1 ORDER BY 2 DESC;
