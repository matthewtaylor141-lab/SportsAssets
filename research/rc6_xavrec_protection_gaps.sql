-- READ-ONLY. RC6 lane xavier-records: how long held PAPER positions were
-- without a valid standing protection between one GTD protective order's
-- expiry and its replacement (72 h), and how late the simulator terminated
-- an expired order. An order past expires_at protects nothing
-- (bettor_paper_freshness.protection_state -> PROTECTION_EXPIRED) and the
-- packet is incomplete until the replacement rests. Every statement is a
-- SELECT.

\echo G1 standing protection orders that ended EXPIRED (72 h): lateness of the terminal state and the gap to the next protective order of the same position
WITH p AS (
  SELECT o.group_id, o.us_market_slug, o.holding_side, o.order_id, o.state,
         o.created_at, o.expires_at, o.terminal_at,
         lead(o.created_at) OVER (PARTITION BY o.group_id, o.us_market_slug,
                                  o.holding_side ORDER BY o.created_at)
           AS next_created
    FROM paper_orders o
   WHERE o.role = 'STANDING_PROTECTION'
     AND o.created_at > now() - interval '72 hours')
SELECT count(*) AS expired_orders,
       count(next_created) AS replaced,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
             terminal_at - expires_at))::numeric, 1) AS p50_terminal_late_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM
             terminal_at - expires_at))::numeric, 1) AS p95_terminal_late_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM
             next_created - expires_at))::numeric, 1) AS p50_gap_s,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM
             next_created - expires_at))::numeric, 1) AS p95_gap_s,
       round(max(extract(epoch FROM next_created - expires_at))::numeric, 1)
         AS max_gap_s,
       round(sum(greatest(0, extract(epoch FROM next_created - expires_at)))
             ::numeric, 1) AS total_gap_s
  FROM p WHERE state = 'EXPIRED';

\echo G2 the same, per strategy, with the protected time it interrupts
WITH p AS (
  SELECT o.group_id, o.state, o.created_at, o.expires_at, o.terminal_at,
         o.strategy,
         lead(o.created_at) OVER (PARTITION BY o.group_id, o.us_market_slug,
                                  o.holding_side ORDER BY o.created_at)
           AS next_created
    FROM paper_orders o
   WHERE o.role = 'STANDING_PROTECTION'
     AND o.created_at > now() - interval '72 hours')
SELECT strategy, count(*) FILTER (WHERE state = 'EXPIRED') AS expired,
       count(*) AS protective_orders,
       round(sum(extract(epoch FROM least(expires_at, coalesce(terminal_at,
             now())) - created_at))::numeric, 0) AS protected_s,
       round(sum(greatest(0, extract(epoch FROM next_created - expires_at)))
             FILTER (WHERE state = 'EXPIRED')::numeric, 0) AS expiry_gap_s
  FROM p GROUP BY 1 ORDER BY 2 DESC;

\echo G3 reviews of held positions (72 h) by the protection state they recorded
SELECT selection->'management_packet'->'protection'->>'state' AS protection,
       count(*) AS reviews, count(DISTINCT group_id) AS groups
  FROM paper_xavier_reviews
 WHERE reviewed_at > now() - interval '72 hours'
 GROUP BY 1 ORDER BY 2 DESC;
