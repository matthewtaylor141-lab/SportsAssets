-- LAB-A FAST LANE: the production execution time of each canonical
-- component's reads (canonical_components: Eddie's history, the Opportunity
-- Score's settlement lags and capital snapshot, the event start, Karen's open
-- challenges, Allie's exposure reads), measured with EXPLAIN ANALYZE on the
-- read replica (read-only SELECTs, executed once each). The statements are
-- the components' own SQL with their bound parameters written as literals:
-- now() for the clock, the newest completed-game ENTER's market and fixture.
-- Allie's hurdle read needs canonical_decision_intents (migration 225, not
-- deployed): it cannot be timed here and is reported UNAVAILABLE.
\echo == E1 eddie.history_stats orders (14 d, LIMIT 2000)
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT order_type, state, queue_ahead_qty,
       extract(epoch FROM created_at - decided_at) AS submit_s
  FROM paper_orders WHERE created_at BETWEEN now() - interval '14 days' AND now()
 ORDER BY created_at DESC LIMIT 2000;

\echo == E2 eddie.history_stats time to fill
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT o.order_type, extract(epoch FROM min(f.filled_at) - o.created_at) AS ttf_s
  FROM paper_fills f JOIN paper_orders o USING (order_id)
 WHERE f.filled_at BETWEEN now() - interval '14 days' AND now()
 GROUP BY o.order_id, o.order_type, o.created_at
 ORDER BY o.created_at DESC LIMIT 2000;

\echo == E3 eddie.history_stats markouts (lateral later book)
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT f.fill_id, f.holding_side, f.price, o.order_type,
       b0.bids AS b0b, b0.offers AS b0o, b1.bids AS b1b, b1.offers AS b1o
  FROM paper_fills f JOIN paper_orders o USING (order_id)
  LEFT JOIN paper_book_observations b0 ON b0.obs_id = f.book_obs_id
  LEFT JOIN LATERAL (SELECT bids, offers FROM paper_book_observations b
        WHERE b.us_market_slug = f.us_market_slug
          AND b.observed_at BETWEEN f.filled_at + make_interval(secs => 30.0)
                                AND f.filled_at + make_interval(secs => 300.0)
        ORDER BY b.observed_at LIMIT 1) b1 ON true
 WHERE f.filled_at BETWEEN now() - interval '14 days' AND now()
 ORDER BY f.filled_at DESC LIMIT 2000;

\echo == O1 opportunity / allie: settlement lag samples (180 d, lateral us_premap)
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT extract(epoch FROM s.recorded_at)::float8 AS rec,
       extract(epoch FROM s.settled_at - g.game_start)::float8 AS lag
  FROM (SELECT DISTINCT ON (us_market_slug) us_market_slug, recorded_at, settled_at
          FROM paper_settlements
         WHERE settled_at >= now() - interval '180 days'
           AND outcome IN ('WON', 'LOST')
         ORDER BY us_market_slug, settled_at) s
  JOIN LATERAL (SELECT game_start FROM us_premap
                 WHERE market_slug = s.us_market_slug AND game_start IS NOT NULL
                 ORDER BY updated_at DESC NULLS LAST LIMIT 1) g ON true
 LIMIT 20000;

\echo == O2 opportunity / allie: capital snapshot
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT extract(epoch FROM computed_at)::float8 AS t,
       payload->'idle_capital_usd' AS v
  FROM pos_snapshots WHERE component = 'CAPITAL' AND book = 'PAPER'
   AND computed_at >= coalesce((SELECT max(computed_at) FROM pos_snapshots
         WHERE component = 'CAPITAL' AND book = 'PAPER' AND computed_at <= now()),
       now())
 ORDER BY computed_at ASC LIMIT 2000;

\echo == S1 event start (us_premap by market slug)
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT DISTINCT ON (market_slug) market_slug,
       extract(epoch FROM game_start)::float8 AS t
  FROM us_premap
 WHERE market_slug = (SELECT us_market_slug FROM paper_decisions
                       WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                         AND verdict = 'ENTER' ORDER BY decided_at DESC LIMIT 1)
   AND game_start IS NOT NULL
 ORDER BY market_slug, updated_at DESC NULLS LAST;

\echo == K1 karen: open challenges on the market / strategy
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT count(*) FILTER (WHERE d.us_market_slug = 'x') AS on_market,
       count(*) FILTER (WHERE d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER') AS on_strategy,
       count(*) AS total_open
  FROM karen_challenges k
  LEFT JOIN paper_decisions d
    ON k.target_kind = 'paper_decisions' AND d.decision_id = k.target_id
 WHERE k.state = 'OPEN' AND k.challenged_at <= now();

\echo == A1 allie: open exposure on the fixture
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT count(DISTINCT o.group_id) AS n,
       coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd
  FROM paper_orders o
 WHERE o.role = 'ENTRY'
   AND o.fixture = (SELECT fixture FROM paper_decisions
                     WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
                       AND verdict = 'ENTER' ORDER BY decided_at DESC LIMIT 1)
   AND o.filled_qty > 0
   AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id);

\echo == A2 allie: open book exposure
EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY ON)
SELECT coalesce(sum(o.filled_qty * o.limit_price), 0)
  FROM paper_orders o
 WHERE o.role = 'ENTRY' AND o.filled_qty > 0
   AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id);

\echo == T table sizes behind the components
SELECT (SELECT count(*) FROM paper_orders) AS paper_orders,
       (SELECT count(*) FROM paper_fills) AS paper_fills,
       (SELECT count(*) FROM paper_book_observations) AS paper_books,
       (SELECT count(*) FROM us_premap) AS us_premap,
       (SELECT count(*) FROM paper_settlements) AS paper_settlements,
       (SELECT count(*) FROM karen_challenges) AS karen_challenges,
       to_regclass('canonical_decision_intents') AS cdi;
