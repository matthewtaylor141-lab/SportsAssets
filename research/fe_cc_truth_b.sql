-- READ ONLY. Frontend root-cause audit (group frontend), part B: what the
-- decision feed says against what the paper book holds, so the Command
-- Center can be judged for truthfulness. SELECT statements only.
\echo == B1. decisions in the last 24 h by strategy, ENTER with and without a paper order ==
SELECT d.strategy,
       count(*) FILTER (WHERE d.verdict = 'ENTER') AS enter_24h,
       count(*) FILTER (WHERE d.verdict = 'REFUSE') AS refuse_24h,
       count(o.order_id) FILTER (WHERE d.verdict = 'ENTER') AS enter_with_order,
       max(d.decided_at) FILTER (WHERE d.verdict = 'ENTER') AS last_enter
  FROM paper_decisions d
  LEFT JOIN paper_orders o ON o.decision_id = d.decision_id
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY d.strategy
 ORDER BY 2 DESC;

\echo == B2. newest orders and fills ever ==
SELECT count(*) AS orders, max(created_at) AS newest_order, now() - max(created_at) AS order_age
  FROM paper_orders;
SELECT date_trunc('day', created_at) AS day, count(*) AS orders
  FROM paper_orders
 WHERE created_at > now() - interval '8 days'
 GROUP BY 1 ORDER BY 1 DESC;
SELECT date_trunc('day', decided_at) AS day,
       count(*) AS decisions,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter
  FROM paper_decisions
 WHERE decided_at > now() - interval '8 days'
 GROUP BY 1 ORDER BY 1 DESC;

\echo == B3. top refusals in the last hour ==
SELECT coalesce(refusal, 'ENTER') AS refusal, count(*) AS n
  FROM paper_decisions
 WHERE decided_at > now() - interval '1 hour'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 12;

\echo == B4. ENTER decisions in the last 24 h, hour by hour ==
SELECT date_trunc('hour', decided_at) AS hr, strategy,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter,
       count(*) AS decisions
  FROM paper_decisions
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2
 HAVING count(*) FILTER (WHERE verdict = 'ENTER') > 0
 ORDER BY 1 DESC, 2 LIMIT 40;

\echo == B5. the newest ENTER decision that has no paper order, with its disposition ==
SELECT d.decision_id, d.decided_at, d.strategy, d.intent, d.holding_side,
       left(coalesce(d.policy_decision->>'entry_disposition', d.policy_decision->>'disposition', 'none'), 120) AS disposition,
       left(d.provenance::text, 160) AS provenance
  FROM paper_decisions d
  LEFT JOIN paper_orders o ON o.decision_id = d.decision_id
 WHERE d.verdict = 'ENTER' AND o.order_id IS NULL
   AND d.decided_at > now() - interval '24 hours'
 ORDER BY d.decided_at DESC LIMIT 5;

\echo == B6. settlements and ledger realised totals ==
SELECT count(*) AS settlement_rows, count(DISTINCT position_key) AS positions_settled FROM paper_settlements;
SELECT kind, count(*) AS n, sum(cash_delta_usd) AS cash_delta FROM paper_ledger GROUP BY kind ORDER BY kind;
