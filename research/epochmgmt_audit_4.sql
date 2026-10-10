-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 4:
-- the positions whose REDUCE was followed by a second filled REDUCE (a half sale repeated).
\echo == A every order and fill of the three positions, in time order
SELECT o.us_market_slug, o.holding_side, o.role, o.direction, o.qty, o.filled_qty, o.state, o.decided_at, o.order_id
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main'
   AND ((o.us_market_slug = 'aec-npb-trge-fsh-2026-10-05' AND o.holding_side = 'SHORT')
     OR (o.us_market_slug = 'atc-u21eq-ita-pol-2026-10-05-ita' AND o.holding_side = 'LONG'))
   AND o.role IN ('ENTRY', 'REDUCE', 'EXIT')
 ORDER BY o.us_market_slug, o.decided_at;

\echo == B the fills of those positions
SELECT f.us_market_slug, f.holding_side, f.direction, f.role, f.qty, f.recorded_at
  FROM paper_fills f
 WHERE f.account_id = 'paper_acct_main'
   AND ((f.us_market_slug = 'aec-npb-trge-fsh-2026-10-05' AND f.holding_side = 'SHORT')
     OR (f.us_market_slug = 'atc-u21eq-ita-pol-2026-10-05-ita' AND f.holding_side = 'LONG'))
   AND f.role <> 'STANDING_PROTECTION'
 ORDER BY f.us_market_slug, f.recorded_at;

\echo == C the two reviews that submitted the REDUCE orders of the first position: trigger, time, recommendation, target qty
SELECT r.reviewed_at, r.trigger, r.recommendation, r.action ->> 'taken' AS taken,
       r.action ->> 'order_id' AS order_id, r.action -> 'requested' ->> 'qty' AS requested_qty
  FROM paper_xavier_reviews r
 WHERE r.account_id = 'paper_acct_main' AND r.action ->> 'taken' IN ('SUBMIT_REDUCE', 'SUBMIT_EXIT')
   AND r.group_id IN (SELECT DISTINCT group_id FROM paper_orders
                       WHERE account_id = 'paper_acct_main' AND us_market_slug = 'aec-npb-trge-fsh-2026-10-05' AND holding_side = 'SHORT')
 ORDER BY r.reviewed_at;
