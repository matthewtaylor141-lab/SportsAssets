-- ============================================================================
-- MECHANISM ATTRIBUTION: DATA COVERAGE, NOT MECHANISM PRE-SELECTION
-- (2026-09-11, read-only.)
--
-- Run 60 died on statement 2 -- `column mk.us_market_slug does not exist` --
-- and that error IS the most valuable thing the probe produced. Statement 1
-- returned first and settles the schema question:
--
--   markets has:  condition_id, slug, event_slug, event_title, title, sport,
--                 closed, resolved, resolved_prices, resolved_at, updated_at
--   markets has NOT: us_market_slug, venue
--   us_premap has: market_slug, identifier, event_slug, game_start, kind,
--                  line, question, side_norm, signed, sports_type, team_*
--   us_premap has NOT: condition_id
--
-- So THERE IS NO KEY joining a Polymarket condition to a PMUS game_start.
-- The only bridges are OUR OWN MAPPER'S OUTPUT --
--   mirror_books(condition_id, us_market_slug)          NOT NULL
--   mirror_shadow(condition_id, us_market_slug)         nullable
--   mirror_candidate_refusals(condition_id, us_slug)    nullable
-- -- which exist only where our mapper resolved the condition. That is the
-- very population whose gap the coverage program was created to close, and it
-- is selected on sport / league / market type, which are themselves
-- explanatory variables. time-to-event and pregame-vs-live are therefore
-- available ONLY on a mapper-selected subset, and this file MEASURES that
-- subset rather than assuming it away.
--
-- This file reports, for the explanatory variables, coverage against four
-- denominators: conditions, deployed dollars, matched cost, and realized P&L.
-- The three static fields the owner also asked for -- exact source, causally
-- known at fill time vs ex post, and independent vs derived from the same
-- fills whose economics are being explained -- are not measurable in SQL and
-- are tabulated in research/MECH_VARIABLES.md instead.
--
-- NO MECHANISM IS PRE-SELECTED HERE. Nothing below tests pairing, direction or
-- liquidity provision against one another.
--
-- Read-only: five SELECTs.
-- ============================================================================


\echo '== 1. THE DENOMINATORS. Everything later is a fraction of these =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), inwin AS (
  SELECT * FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), leg AS (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.qbuy, 0)) AS q_all,
         sum(COALESCE(l.cbuy, 0)) AS deployed,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn,
         sum(COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0)) AS net_q,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost
    FROM leg l GROUP BY 1
), pnl AS (
  -- realized condition-level trading P&L, cash basis, only where the market
  -- resolved AND a payout vector is retained. Anything else is NULL, never 0.
  SELECT c.*,
         mk.resolved, mk.resolved_prices,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL THEN
           ( COALESCE((SELECT sum( (CASE WHEN l2.outcome_index = 0
                                         THEN (mk.resolved_prices->>0)::float8
                                         ELSE (mk.resolved_prices->>1)::float8 END)
                                   * (COALESCE(l2.qbuy,0) - COALESCE(l2.qsell,0)) )
                         FROM leg l2 WHERE l2.condition_id = c.condition_id), 0)
             - c.net_cost )
         END AS realized_pnl,
         LEAST(COALESCE(c.qy,0), COALESCE(c.qn,0)) AS m_pairs
    FROM cond c LEFT JOIN markets mk ON mk.condition_id = c.condition_id
)
SELECT count(*) AS conditions,
       round(sum(deployed)::numeric, 0) AS deployed_usd,
       round(sum( m_pairs * ( CASE WHEN qy > 0 THEN cy / qy END
                            + CASE WHEN qn > 0 THEN cn / qn END ) )::numeric, 0)
         AS matched_cost_usd,
       count(*) FILTER (WHERE realized_pnl IS NOT NULL) AS conds_with_realized,
       round(sum(realized_pnl)::numeric, 0) AS realized_pnl_usd,
       round(sum(abs(realized_pnl))::numeric, 0) AS abs_realized_pnl_usd
  FROM pnl;


\echo '== 2. COVERAGE OF EACH EXPLANATORY VARIABLE, on all four denominators =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side, t.source,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), inwin AS (
  SELECT * FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), leg AS (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS deployed,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost
    FROM leg l GROUP BY 1
), pnl AS (
  SELECT c.*,
         LEAST(COALESCE(c.qy,0), COALESCE(c.qn,0)) AS m_pairs,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL THEN
           ( COALESCE((SELECT sum( (CASE WHEN l2.outcome_index = 0
                                         THEN (mk.resolved_prices->>0)::float8
                                         ELSE (mk.resolved_prices->>1)::float8 END)
                                   * (COALESCE(l2.qbuy,0) - COALESCE(l2.qsell,0)) )
                         FROM leg l2 WHERE l2.condition_id = c.condition_id), 0)
             - c.net_cost )
         END AS realized_pnl,
         mk.sport, mk.event_slug, mk.slug, mk.resolved, mk.resolved_prices
    FROM cond c LEFT JOIN markets mk ON mk.condition_id = c.condition_id
), bridge AS (
  -- the ONLY condition_id -> PMUS slug bridges that exist, all of them our
  -- own mapper's output and therefore mapper-selected.
  SELECT condition_id, max(us_market_slug) AS us_slug FROM mirror_books
   WHERE us_market_slug IS NOT NULL GROUP BY 1
  UNION
  SELECT condition_id, max(us_market_slug) FROM mirror_shadow
   WHERE us_market_slug IS NOT NULL GROUP BY 1
  UNION
  SELECT condition_id, max(us_slug) FROM mirror_candidate_refusals
   WHERE us_slug IS NOT NULL GROUP BY 1
), br1 AS (
  SELECT condition_id, min(us_slug) AS us_slug FROM bridge GROUP BY 1
), gs AS (
  SELECT b.condition_id, min(up.game_start) AS game_start
    FROM br1 b JOIN us_premap up ON up.market_slug = b.us_slug
   WHERE up.game_start IS NOT NULL GROUP BY 1
), flags AS (
  SELECT p.*,
         (p.sport IS NOT NULL AND p.sport <> 'unclassified') AS has_sport,
         (p.slug IS NOT NULL)                                AS has_slug,
         (br1.condition_id IS NOT NULL)                      AS has_us_bridge,
         (gs.condition_id IS NOT NULL)                       AS has_game_start,
         (p.resolved AND p.resolved_prices IS NOT NULL)      AS has_settlement,
         (p.m_pairs * ( CASE WHEN p.qy > 0 THEN p.cy / p.qy END
                      + CASE WHEN p.qn > 0 THEN p.cn / p.qn END )) AS matched_cost
    FROM pnl p
    LEFT JOIN br1 ON br1.condition_id = p.condition_id
    LEFT JOIN gs  ON gs.condition_id  = p.condition_id
), tot AS (
  SELECT count(*)::float8 AS n, sum(deployed) AS dep,
         sum(matched_cost) AS mc, sum(abs(realized_pnl)) AS apnl FROM flags
)
SELECT x.variable,
       x.conds, round((100.0 * x.conds / t.n)::numeric, 2) AS pct_conditions,
       round((100.0 * x.dep  / NULLIF(t.dep, 0))::numeric, 2)  AS pct_deployed,
       round((100.0 * x.mc   / NULLIF(t.mc, 0))::numeric, 2)   AS pct_matched_cost,
       round((100.0 * x.apnl / NULLIF(t.apnl, 0))::numeric, 2) AS pct_abs_realized_pnl
  FROM tot t CROSS JOIN LATERAL (
      SELECT '1 entry price / trade size / leg prices  (trades)' AS variable,
             count(*) AS conds, sum(deployed) AS dep,
             sum(matched_cost) AS mc, sum(abs(realized_pnl)) AS apnl FROM flags
    UNION ALL
      SELECT '2 detection lane  (trades.source)',
             count(*), sum(deployed), sum(matched_cost), sum(abs(realized_pnl)) FROM flags
    UNION ALL
      SELECT '3 sport classified  (markets.sport)',
             count(*), sum(deployed), sum(matched_cost), sum(abs(realized_pnl))
        FROM flags WHERE has_sport
    UNION ALL
      SELECT '4 market slug present  (markets.slug)',
             count(*), sum(deployed), sum(matched_cost), sum(abs(realized_pnl))
        FROM flags WHERE has_slug
    UNION ALL
      SELECT '5 settlement retained  (markets.resolved_prices)',
             count(*), sum(deployed), sum(matched_cost), sum(abs(realized_pnl))
        FROM flags WHERE has_settlement
    UNION ALL
      SELECT '6 PMUS slug bridge  (OUR MAPPER OUTPUT ONLY, selected)',
             count(*), sum(deployed), sum(matched_cost), sum(abs(realized_pnl))
        FROM flags WHERE has_us_bridge
    UNION ALL
      SELECT '7 game_start reachable  (time-to-event, pregame/live)',
             count(*), sum(deployed), sum(matched_cost), sum(abs(realized_pnl))
        FROM flags WHERE has_game_start
  ) x
 ORDER BY x.variable;


\echo '== 3. IS THE MAPPER BRIDGE SELECTED? bridged vs unbridged, side by side =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), inwin AS (
  SELECT * FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
), bridge AS (
  SELECT condition_id FROM mirror_books      WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT CASE WHEN br1.condition_id IS NOT NULL
            THEN '1 BRIDGED to a PMUS slug (mapper resolved it)'
            ELSE '2 NOT BRIDGED (no mapper output for this condition)' END AS bridge_class,
       count(DISTINCT i.condition_id) AS conditions,
       count(*) AS buy_fills,
       round(sum(i.sh * i.px)::numeric, 0) AS buy_notional,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY i.px)::numeric, 4) AS p50_price,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY i.sh)::numeric, 1) AS p50_size,
       round((100.0 * count(*) FILTER (WHERE i.feed = 'venue') / count(*))::numeric, 2)
         AS pct_venue_feed,
       count(DISTINCT mk.sport) AS distinct_sports
  FROM inwin i
  LEFT JOIN br1 ON br1.condition_id = i.condition_id
  LEFT JOIN markets mk ON mk.condition_id = i.condition_id
 GROUP BY 1 ORDER BY 1;
