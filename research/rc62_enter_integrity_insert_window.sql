-- READ-ONLY. RC6.2 enter-integrity rework: can the 13 exploration ENTERs
-- with neither an order nor a refusal (2026-10-06 20:04 .. 2026-10-09 16:12)
-- be told apart -- the order sequence cut AFTER the row was written (inside
-- the intent hook) versus the row's own statement cut while it waited on the
-- account row that a concurrent ledger submit held (the reviewer's window)?
-- (E1) is commit-time tracking on; (E2) per row: the statement start
-- (recorded_at = now()), its commit instant when tracked, the intent's
-- instant, the reactive attempt's finish, and the ledger activity on the
-- same account from 15 s before to 2 s after the row's start (paper orders
-- of any strategy, and LEDGER-stage refusal census rows -- each a submit
-- that held the account row while it ran). Every statement is a SELECT.

\echo E1 commit timestamp tracking
SELECT current_setting('track_commit_timestamp') AS track_commit_timestamp;

\echo E2 the 13 rows, their timings and the ledger activity around each
WITH c AS (
  SELECT d.decision_id, d.valuation_id, d.decided_at, d.recorded_at,
         d.xmin AS x
    FROM paper_decisions d
   WHERE d.account_id = 'paper_acct_main' AND d.verdict = 'ENTER'
     AND d.strategy = 'PINNACLE_EXPLORATION_PAPER'
     AND d.decided_at >= '2026-10-06 05:37:53+00'
     AND EXISTS (SELECT 1 FROM paper_audrey_findings f
                  WHERE f.subject = d.decision_id
                    AND f.kind = 'ENTER_WITHOUT_ORDER'))
SELECT c.decision_id, c.valuation_id,
       to_char(c.recorded_at, 'MM-DD HH24:MI:SS.MS') AS stmt_start,
       round(extract(epoch FROM c.recorded_at - c.decided_at)::numeric, 3)
         AS start_after_decided_s,
       CASE WHEN current_setting('track_commit_timestamp') = 'on'
            THEN round(extract(epoch FROM pg_xact_commit_timestamp(c.x)
                                          - c.recorded_at)::numeric, 3)
       END AS commit_after_start_s,
       (SELECT round(extract(epoch FROM i.created_at - c.recorded_at)::numeric, 3)
          FROM execution_intents i WHERE i.decision_id = c.decision_id)
         AS intent_after_start_s,
       (SELECT string_agg(p.state || ':' || round(((p.detail->>'finished_at')::float8
                 - extract(epoch FROM c.recorded_at))::numeric, 3), ' ')
          FROM pinnapi_reactive_attempts p
         WHERE p.created_at BETWEEN c.decided_at - interval '60 seconds'
                                AND c.decided_at + interval '5 seconds'
           AND p.detail->'valuation_ids' @> to_jsonb(c.valuation_id))
         AS reactive_finish_after_start_s,
       (SELECT string_agg(o.strategy || ':' || round(extract(epoch FROM
                 o.created_at - c.recorded_at)::numeric, 3), ' '
                 ORDER BY o.created_at)
          FROM paper_orders o
         WHERE o.account_id = 'paper_acct_main'
           AND o.created_at BETWEEN c.recorded_at - interval '15 seconds'
                                AND c.recorded_at + interval '2 seconds')
         AS orders_near_start_s,
       (SELECT string_agg(r.strategy || ':' || round(extract(epoch FROM
                 r.recorded_at - c.recorded_at)::numeric, 3), ' '
                 ORDER BY r.recorded_at)
          FROM paper_entry_refusal_census r
         WHERE r.account_id = 'paper_acct_main' AND r.stage = 'LEDGER'
           AND r.recorded_at BETWEEN c.recorded_at - interval '15 seconds'
                                 AND c.recorded_at + interval '2 seconds')
         AS ledger_refusals_near_start_s
  FROM c ORDER BY c.recorded_at;
