-- ============================================================================
-- WILL THE BRIDGE RUN? Measure it, after two blind attempts.
-- (2026-09-11, read-only.)
--
-- Runs 51 and 53 both ran past their ceilings and returned nothing. Between
-- them I made two structural fixes that were correct in themselves -- the SELL
-- statement now restricts the shadow bids before unioning, and three GROUP BYs
-- over `b` became one labelled pass -- and neither fixed it. That is two
-- attempts on reasoning rather than measurement, which is exactly the pattern
-- the check A post-mortem was written to stop, so this measures instead.
--
-- Nothing here runs the bridge. Four cheap statements plus one EXPLAIN that
-- PLANS the heavy join WITHOUT executing it.
--
-- THE PRIME SUSPECT, stated before the evidence so the evidence can refute it:
-- the bridge joins copy_probes DIRECTLY on p.trade_id = <fill id>, and
-- copy_probes has NO INDEX ON trade_id -- 963,364 rows / 588 MB, the deferred
-- recommendation in research/SCHEMA_RECOMMENDATIONS.md. Check A hit precisely
-- this and solved it by collecting the probed ids ONCE into a set and hash
-- joining. Here I went straight back to the direct join on the unindexed
-- column, and then hung a LATERAL jsonb_array_elements off the result. If the
-- planner picks a nested loop over that join, every one of the ~31k TTI events
-- scans 588 MB.
--
-- Read-only: five statements, none of which runs the bridge.
-- ============================================================================


\echo '== 1. HOW BIG IS THE JOIN? probes, ladders, and the LATERAL expansion =='
SELECT count(*) AS copy_probes_rows,
       count(*) FILTER (WHERE trade_id IS NOT NULL) AS with_trade_id,
       count(*) FILTER (WHERE book_ok AND best_ask IS NOT NULL) AS usable,
       count(*) FILTER (WHERE depth IS NOT NULL
                          AND jsonb_typeof(depth) = 'array'
                          AND jsonb_array_length(depth) > 0) AS WITH_A_LADDER,
       COALESCE(sum(jsonb_array_length(depth)) FILTER (
         WHERE depth IS NOT NULL AND jsonb_typeof(depth) = 'array'), 0)
         AS TOTAL_LADDER_LEVELS,
       round(avg(jsonb_array_length(depth)) FILTER (
         WHERE depth IS NOT NULL AND jsonb_typeof(depth) = 'array'
           AND jsonb_array_length(depth) > 0)::numeric, 2) AS avg_levels,
       pg_size_pretty(pg_total_relation_size('copy_probes')) AS total_size
  FROM copy_probes;


\echo '== 2. IS THERE AN INDEX ON copy_probes.trade_id? (the check A cause) =='
SELECT indexname, indexdef
  FROM pg_indexes WHERE tablename = 'copy_probes' ORDER BY indexname;


\echo '== 3. THE TTI EVENT COUNT the join is driven from =='
-- Deliberately NOT the full TTI derivation: just the canonical RN1 BUY fills in
-- the window, which bounds it from above. If the upper bound is small the join
-- shape matters less than the plan; if the plan is a nested loop it matters
-- regardless.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
)
SELECT count(*) AS canonical_rn1_buy_fills_in_window
  FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
                        AND e.cf = b.feed;


\echo '== 4. HOW MANY OF THOSE FILLS HAVE AN OWN-FILL PROBE WITH A LADDER? =='
-- The set-then-hash-join shape check A settled on, used here as the
-- measurement so the measurement itself cannot be the slow thing.
WITH laddered AS (
  SELECT DISTINCT p.trade_id
    FROM copy_probes p
   WHERE p.trade_id IS NOT NULL AND p.book_ok AND p.best_ask IS NOT NULL
     AND p.depth IS NOT NULL AND jsonb_typeof(p.depth) = 'array'
     AND jsonb_array_length(p.depth) > 0
), base AS (
  SELECT t.id, t.tx_hash, t.asset,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.id FROM base b JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
                                    AND e.cf = b.feed
)
SELECT (SELECT count(*) FROM canon) AS canonical_fills,
       (SELECT count(*) FROM laddered) AS probes_with_a_ladder,
       count(*) AS FILLS_WITH_AN_OWN_LADDER,
       round((100.0 * count(*) / NULLIF((SELECT count(*) FROM canon), 0))::numeric, 2)
         AS PCT_OF_FILLS
  FROM canon c JOIN laddered l ON l.trade_id = c.id;


\echo '== 5. THE PLAN, not the run: how does it intend to join the probes? =='
-- Read for a Nested Loop whose inner side is a Seq Scan on copy_probes. That
-- is the check A shape and it would explain both cancelled runs.
EXPLAIN
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM base GROUP BY 1, 2
), canon AS (
  SELECT b.id, b.ts FROM base b JOIN env e ON e.tx_hash = b.tx_hash
                                          AND e.asset = b.asset AND e.cf = b.feed
), ev AS (
  SELECT c.id, c.ts, p.probe_at, p.depth
    FROM canon c LEFT JOIN copy_probes p ON p.trade_id = c.id
), lad AS (
  SELECT e.id, lv.ord, (lv.lvl->>0)::float8 AS lvl_px, (lv.lvl->>1)::float8 AS lvl_sz
    FROM ev e CROSS JOIN LATERAL jsonb_array_elements(e.depth) WITH ORDINALITY AS lv(lvl, ord)
   WHERE e.depth IS NOT NULL
)
SELECT id, count(*) AS levels, sum(lvl_sz) AS depth_sh, min(lvl_px) AS best
  FROM lad GROUP BY id;
