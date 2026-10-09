-- READ-ONLY. RC6 lane ev-audit: is the twin's fresh-order producer blocked by
-- a defect, or by the lifecycle quarantine? Every ENTER decision since the
-- first quarantine (2026-10-06 05:37Z) per strategy and UTC day: with a
-- paper order, or refused at the ledger and why (paper_audrey_findings
-- PAPER_RISK_REFUSED_THE_ORDER detail.refusal). SELECT only.

\echo == 1 ENTER decisions since 2026-10-06 05:37Z: with / without a paper order
SELECT date_trunc('day', d.decided_at)::date d, coalesce(d.strategy, '-') strategy,
       count(*) enter_decisions,
       count(o.order_id) with_order
  FROM paper_decisions d
  LEFT JOIN paper_orders o ON o.decision_id = d.decision_id
 WHERE d.verdict = 'ENTER' AND d.decided_at >= '2026-10-06 05:37:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == 2 the ledger refusal of those orders (findings), by refusal
SELECT date_trunc('day', f.found_at)::date d,
       coalesce(f.detail->>'strategy', '-') strategy,
       coalesce(f.detail->>'refusal', '-') refusal, count(*) n
  FROM paper_audrey_findings f
 WHERE f.kind = 'PAPER_RISK_REFUSED_THE_ORDER'
   AND f.found_at >= '2026-10-06 00:00:00+00'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, n DESC;

\echo == 3 open PAPER positions now (the only remaining source of EXIT / REDUCE IOC orders)
SELECT count(*) open_groups FROM (
  SELECT f.group_id,
         sum(CASE WHEN f.direction = 'BUY' THEN f.qty ELSE -f.qty END) q
    FROM paper_fills f
   WHERE f.account_id = 'paper_acct_main'
     AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = f.group_id)
   GROUP BY f.group_id) x
 WHERE q > 1e-9;
