-- READ-ONLY. RC6.2 enter-integrity, part 2: (a) the one exploration ENTER of
-- the 13 (valuation 23582) whose reactive attempt was not found within
-- [-30 s, +5 s]: every reactive attempt naming it, any time; (b) the other
-- ENTERs since 2026-10-06 00:00 with neither an order nor a refusal (9
-- completed-game, 1 exploration before the quarantine): their path, finding,
-- intent and the reactive attempt that carried them. Every statement is a
-- SELECT.

\echo D1 every reactive attempt naming valuation 23582
SELECT p.attempt_id, p.state, p.created_at, p.event_id,
       round(((p.detail->>'finished_at')::float8 - (p.detail->>'evaluation_started_at')::float8)::numeric, 3) AS ran_s,
       p.detail->>'deadline_s' AS deadline_s, p.detail->'valuation_ids' AS valuation_ids
  FROM pinnapi_reactive_attempts p
 WHERE p.created_at BETWEEN '2026-10-06 20:00+00' AND '2026-10-06 21:00+00'
   AND p.detail->'valuation_ids' @> '23582'::jsonb;

\echo D2 valuation 23582 itself
SELECT id, decided_at, experiment_id, us_market_slug, record_purpose
  FROM external_valuations WHERE id = 23582;

\echo D3 the other ENTERs since 2026-10-06 00:00 with neither an order nor a refusal: path, finding, intent, reactive attempt
WITH c AS (
  SELECT d.* FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.verdict = 'ENTER'
     AND d.decided_at >= '2026-10-06 00:00+00' AND d.decided_at < '2026-10-06 05:37:53+00'
     AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census r WHERE r.decision_id = d.decision_id)
     AND NOT EXISTS (SELECT 1 FROM paper_audrey_findings f WHERE f.subject = d.decision_id
                      AND f.kind = 'PAPER_RISK_REFUSED_THE_ORDER'))
SELECT c.strategy, c.valuation_id, to_char(c.decided_at, 'MM-DD HH24:MI:SS.MS') AS decided,
       c.pinnacle->>'decided_via' AS via,
       round(extract(epoch FROM c.recorded_at - c.decided_at)::numeric, 3) AS insert_after_s,
       (SELECT round(extract(epoch FROM i.created_at - c.recorded_at)::numeric, 3)
          FROM execution_intents i WHERE i.decision_id = c.decision_id) AS intent_after_insert_s,
       (SELECT string_agg(f.kind, ' ') FROM paper_audrey_findings f WHERE f.subject = c.decision_id) AS findings,
       (SELECT string_agg(p.state || ':' || round(((p.detail->>'finished_at')::float8
                 - extract(epoch FROM c.decided_at))::numeric, 3), ' ')
          FROM pinnapi_reactive_attempts p
         WHERE p.created_at BETWEEN c.decided_at - interval '60 seconds' AND c.decided_at + interval '5 seconds'
           AND p.detail->'valuation_ids' @> to_jsonb(c.valuation_id)) AS reactive_state_and_finish_after_decided_s,
       (SELECT string_agg(a.via || ':' || a.outcome, ' ') FROM paper_evaluation_attempts a
         WHERE a.valuation_id = c.valuation_id AND a.strategy = c.strategy) AS attempts
  FROM c ORDER BY c.decided_at;

\echo D4 all strategies since 2026-09-28: ENTERs named ENTER_WITHOUT_ORDER by the backstop, per strategy and day
SELECT d.strategy, date_trunc('day', d.decided_at) AS day, count(*) AS enter_without_order_named
  FROM paper_decisions d
  JOIN paper_audrey_findings f ON f.subject = d.decision_id AND f.kind = 'ENTER_WITHOUT_ORDER'
 WHERE d.account_id = 'paper_acct_main' AND d.verdict = 'ENTER'
   AND d.decided_at >= '2026-09-28 00:00+00'
 GROUP BY 1, 2 ORDER BY 2, 1;
