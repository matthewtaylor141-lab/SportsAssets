-- LAB-A EDGE DECAY: why completed-game ENTER orders expired on an
-- UNREADABLE first book after their eligible instant (read-only, 7 days).
-- The paper simulator executes a marketable order on the FIRST book
-- observed at/after decided_at + delay and never retries on a later one;
-- this lists, for each such order, that first book's error, read basis and
-- source, and whether a READABLE book of the same market was observed
-- later inside the order's time to live.
\echo == 1 terminal reasons of completed-game ENTRY orders, 7 d
SELECT o.terminal_reason, count(*) AS n
  FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
 WHERE d.decided_at > now() - interval '7 days'
   AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND o.role = 'ENTRY'
 GROUP BY 1 ORDER BY 2 DESC;

\echo == 2 the first book at/after eligible for each order expired on an unreadable book
WITH x AS (
  SELECT o.order_id, o.us_market_slug, o.eligible_at, o.expires_at,
         d.pinnacle->>'decided_via' AS via
    FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE d.decided_at > now() - interval '7 days'
     AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER' AND o.role = 'ENTRY'
     AND o.terminal_reason = 'THE_OBSERVED_BOOK_WAS_UNREADABLE'),
f AS (
  SELECT x.*, b.obs_id, b.error, b.read_basis, b.source,
         extract(epoch FROM b.observed_at - x.eligible_at) AS after_eligible_s,
         (SELECT count(*) FROM paper_book_observations r
           WHERE r.us_market_slug = x.us_market_slug AND r.error IS NULL
             AND r.observed_at > b.observed_at
             AND r.observed_at <= x.expires_at) AS readable_later_in_ttl
    FROM x
    LEFT JOIN LATERAL (
      SELECT * FROM paper_book_observations b
       WHERE b.us_market_slug = x.us_market_slug
         AND b.observed_at >= x.eligible_at AND b.observed_at <= x.expires_at
       ORDER BY b.observed_at LIMIT 1) b ON true)
SELECT via, left(coalesce(error, '(none)'), 90) AS error, read_basis, source,
       count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY after_eligible_s) AS after_eligible_p50_s,
       count(*) FILTER (WHERE readable_later_in_ttl > 0) AS readable_book_later_in_ttl
  FROM f GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;

\echo == 3 all paper book reads, 7 d: unreadable share by read basis
SELECT read_basis, count(*) AS reads,
       count(*) FILTER (WHERE error IS NOT NULL) AS unreadable,
       round(100.0 * count(*) FILTER (WHERE error IS NOT NULL)
             / greatest(count(*), 1), 1) AS unreadable_pct
  FROM paper_book_observations
 WHERE observed_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

\echo == 4 unreadable reads by error text, 7 d
SELECT left(error, 100) AS error, count(*) AS n
  FROM paper_book_observations
 WHERE observed_at > now() - interval '7 days' AND error IS NOT NULL
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;
