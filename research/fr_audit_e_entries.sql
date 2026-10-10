-- Freshness root-cause audit (group freshness), read only.
--   E1  ENTER decisions per day and strategy, and what became of them
--   E2  PAPER orders per day by role and state
--   E3  the terminal reasons of the entry orders of the last 4 days
--   E4  the PMX re-send gap of priority symbols (last 12 h) in 15 s bins
--   E5  the stream age of the plane's current priority books: how old is a
--       book the plane counts current (receipt age at the pass), per snapshot
--       digest of the last 3 h
\echo === E1. ENTER decisions per day and strategy, with their orders and fills ===
SELECT date_trunc('day', d.decided_at) AS day, d.strategy, count(*) AS enter_decisions,
       count(o.order_id) AS with_order,
       count(*) FILTER (WHERE o.state = 'FILLED') AS filled,
       count(*) FILTER (WHERE o.order_id IS NULL) AS no_order
  FROM paper_decisions d
  LEFT JOIN paper_orders o ON o.decision_id = d.decision_id AND o.role = 'ENTRY'
 WHERE d.verdict = 'ENTER' AND d.decided_at > now() - interval '6 days'
 GROUP BY 1, 2 ORDER BY 1, 2;
\echo === E2. PAPER orders per day by role and state ===
SELECT date_trunc('day', created_at) AS day, role, state, count(*) AS n
  FROM paper_orders WHERE created_at > now() - interval '6 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
\echo === E3. terminal reasons of ENTRY orders, last 4 days ===
SELECT terminal_reason, state, count(*) AS n, min(created_at) AS first_at, max(created_at) AS last_at
  FROM paper_orders WHERE role = 'ENTRY' AND created_at > now() - interval '4 days'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;
\echo === E4. gap between distinct PMX receipt instants of one priority symbol, 15 s bins, last 12 h ===
WITH b AS (
  SELECT k.key AS sym, (k.value ->> 'received_at')::float8 AS rcv
    FROM market_plane_events ev, jsonb_each(ev.payload -> 'books') AS k
   WHERE ev.kind = 'PRIORITY_PMX_BOOKS' AND ev.at > now() - interval '12 hours'),
d AS (SELECT DISTINCT sym, rcv FROM b WHERE rcv IS NOT NULL),
g AS (SELECT sym, rcv - lag(rcv) OVER (PARTITION BY sym ORDER BY rcv) AS gap FROM d)
SELECT (floor(gap / 15) * 15)::int AS gap_from_s, count(*) AS n
  FROM g WHERE gap IS NOT NULL AND gap < 900 GROUP BY 1 ORDER BY 1;
\echo === E5. the number of priority books per PRIORITY_PMX_BOOKS event, last 3 h (books inside the bound) ===
SELECT date_trunc('hour', at) AS hr, count(*) AS events,
       round(avg((SELECT count(*) FROM jsonb_object_keys(payload -> 'books')))::numeric, 1) AS avg_books
  FROM market_plane_events WHERE kind = 'PRIORITY_PMX_BOOKS' AND at > now() - interval '3 hours'
 GROUP BY 1 ORDER BY 1;
