-- RC6.3 lifecycle-proof lane, readback 4 (SELECT only).
-- The SHADOW half of the paper capital authority forward-economics rule
-- (bettor_capital_authority.shadow_pnls, FORWARD_SINCE = 2026-10-05 04:00Z):
-- every settled, filled shadow counterfactual decided since then, per
-- strategy, as one JSON list per strategy, in the exact shadow_pnls shape.

\echo == H1 shadow_pnls per strategy since FORWARD_SINCE (json list of counterfactual_pnl_usd)
SELECT s.strategy, count(*) AS n, round(sum(o.counterfactual_pnl_usd)::numeric, 6) AS net,
       json_agg(o.counterfactual_pnl_usd::text ORDER BY s.shadow_id)::text AS pnls
  FROM paper_shadow_counterfactuals s
  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.account_id = 'paper_acct_main' AND s.decided_at >= to_timestamp(1791172800)
   AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') AND o.filled_qty > 0
 GROUP BY 1 ORDER BY 1;

\echo == H2 shadow counterfactuals decided per strategy per day since FORWARD_SINCE (all, settled or not)
SELECT s.strategy, date_trunc('day', s.decided_at) AS day, count(*) AS decided,
       count(o.shadow_id) AS with_outcome
  FROM paper_shadow_counterfactuals s
  LEFT JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.account_id = 'paper_acct_main' AND s.decided_at >= to_timestamp(1791172800)
 GROUP BY 1, 2 ORDER BY 1, 2;
