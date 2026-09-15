-- ============================================================================
-- TWO SELECTION AUDITS (2026-09-11, read-only). Owner-specified.
--
-- AUDIT 1  PMUS mapper bridge:  BRIDGED vs UNBRIDGED
-- AUDIT 2  settlement retention: SETTLED_RETAINED vs SETTLEMENT_NOT_RETAINED
--
-- DENOMINATORS, defined once and used unchanged everywhere below.
--   conditions        distinct condition_id with >=1 canonical fill in window
--   acquisition_cost  sum(size*price) over canonical BUY fills
--   M                 min(qYbuy, qNbuy) per condition
--   pair_cost         vY + vN, where vX = sum(sh*px)/sum(sh) on leg X BUYs
--   matched_cost      M * pair_cost
--   residual_cost     acquisition_cost - matched_cost
--   MATCHED_GROSS_PNL M * (1 - pair_cost)
--   TRADING_PNL       sum(payout_x * netqty_x) - net_cost, settled only
--   RESIDUAL_PNL      TRADING_PNL - MATCHED_GROSS_PNL, BY SUBTRACTION
--
-- WHY RESIDUAL IS DEFINED BY SUBTRACTION. The owner requires
-- MATCHED + RESIDUAL = TRADING_PNL exactly. Defining residual as the remainder
-- makes that identity hold by construction rather than by luck. The price of
-- that choice is stated rather than hidden: the matched term is the GROSS PAIR
-- MARGIN LOCKED AT ACQUISITION and assumes the pair is held to settlement, so
-- where RN1 SOLD matched inventory the effect of that sale lands in the
-- residual term. Any reading of the residual must carry that.
--
-- WHY MATCHED ECONOMICS ARE NOT RESTRICTED TO SETTLED CONDITIONS.
-- M * (1 - vY - vN) does not depend on WHO WINS if the condition is binary and
-- the completed pair pays exactly $1. Statement 1 VALIDATES that invariant
-- against retained payout vectors instead of assuming it. Where it holds,
-- matched gross economics are evaluated on the broad population; RESIDUAL_PNL
-- still requires settlement. Two coverage regimes, kept apart.
--
-- market_type is UNAVAILABLE IN THIS SURFACE. A deterministic audited
-- classifier exists -- market_type_of(slug), copy_sports.py:272, covered by
-- test_market_type_parsing and test_kindless_feed_grammar_market_types, and it
-- fails closed to 'unknown'. It is PYTHON. RN1's slugs are the kindless
-- whale-feed grammar, i.e. the post-date-suffix branch, not the clean
-- atc/aec/asc/tsc prefix branch, so an SQL port would have to reimplement the
-- hard half and could silently diverge from the audited original. Reported as
-- market_slug presence only; market_type stays UNAVAILABLE.
--
-- detection source is reported and is labelled OUR INGESTION PROPERTY. It is a
-- property of our pipeline, never of RN1's strategy, and a later detection
-- lane cannot explain an earlier fill.
--
-- No causal language from accounting-derived variables. No repair of
-- game_start coverage is attempted anywhere.
--
-- Read-only: six SELECTs.
-- ============================================================================


\echo '== 1. BINARY-PAYOFF INVARIANT: does a retained payout vector sum to 1? =='
SELECT CASE
         WHEN abs(( (resolved_prices->>0)::float8
                  + (resolved_prices->>1)::float8 ) - 1.0) < 1e-9
           THEN '1 SUMS TO 1 (pair pays $1; matched economics settlement-free)'
         ELSE '2 DOES NOT SUM TO 1 (invariant fails; matched economics need care)'
       END AS invariant,
       count(*) AS markets,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 4) AS pct
  FROM markets
 WHERE resolved AND resolved_prices IS NOT NULL
   AND jsonb_typeof(resolved_prices) = 'array'
   AND jsonb_array_length(resolved_prices) = 2
 GROUP BY 1 ORDER BY 1;


\echo '== 2. AUDIT 1: BRIDGED vs UNBRIDGED, full canonical population =='
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
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
    FROM leg l GROUP BY 1
), m AS (
  SELECT c.*,
         LEAST(c.qy, c.qn) AS mq,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost
    FROM cond c
), fills AS (
  SELECT condition_id, count(*) AS n_fills,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY sh) AS p50_sz,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY sh) AS p90_sz,
         avg(sh) AS avg_sz,
         sum(CASE WHEN feed = 'venue' THEN 1 ELSE 0 END)::float8 / count(*) AS venue_frac,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY px) AS p50_px,
         sum(CASE WHEN px < 0.25 THEN sh * px ELSE 0 END) AS acq_lo,
         sum(CASE WHEN px >= 0.75 THEN sh * px ELSE 0 END) AS acq_hi
    FROM inwin WHERE side = 'BUY' GROUP BY 1
), bridge AS (
  SELECT condition_id FROM mirror_books      WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT CASE WHEN br1.condition_id IS NOT NULL THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS conditions,
       sum(f.n_fills) AS fills,
       round(sum(m.acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(m.mq * m.pair_cost)::numeric, 0) AS matched_cost,
       round(sum(m.mq)::numeric, 0) AS matched_qty,
       round((100.0 * sum(m.mq * m.pair_cost) / NULLIF(sum(m.acq_cost), 0))::numeric, 2)
         AS matched_pct_of_acq_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY m.pair_cost)::numeric, 4)
         AS p50_pair_cost,
       round((100.0 * sum(m.mq * (1.0 - m.pair_cost))
              / NULLIF(sum(m.mq * m.pair_cost), 0))::numeric, 3)
         AS gross_matched_edge_pct_of_matched_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY f.p50_sz)::numeric, 1) AS p50_fill_size,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY f.p90_sz)::numeric, 1) AS p90_fill_size,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY f.p50_px)::numeric, 4) AS p50_price,
       round((100.0 * sum(f.acq_lo) / NULLIF(sum(m.acq_cost), 0))::numeric, 2)
         AS pct_acq_under_25c,
       round((100.0 * sum(f.acq_hi) / NULLIF(sum(m.acq_cost), 0))::numeric, 2)
         AS pct_acq_over_75c,
       round((100.0 * sum(f.venue_frac * f.n_fills) / NULLIF(sum(f.n_fills), 0))::numeric, 2)
         AS pct_venue_lane_OUR_INGESTION,
       count(*) FILTER (WHERE mk.slug IS NOT NULL) AS have_market_slug
  FROM m
  JOIN fills f ON f.condition_id = m.condition_id
  LEFT JOIN br1 ON br1.condition_id = m.condition_id
  LEFT JOIN markets mk ON mk.condition_id = m.condition_id
 GROUP BY 1 ORDER BY 1;


\echo '== 3. AUDIT 1 realized economics, SAME-SETTLEMENT-SUBSET denominators =='
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
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
    FROM leg l GROUP BY 1
), s AS (
  SELECT c.*,
         LEAST(c.qy, c.qn) AS mq,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL THEN
           ( COALESCE((SELECT sum( (CASE WHEN l2.outcome_index = 0
                                         THEN (mk.resolved_prices->>0)::float8
                                         ELSE (mk.resolved_prices->>1)::float8 END)
                                   * (COALESCE(l2.qbuy,0) - COALESCE(l2.qsell,0)) )
                         FROM leg l2 WHERE l2.condition_id = c.condition_id), 0)
             - c.net_cost )
         END AS trading_pnl
    FROM cond c LEFT JOIN markets mk ON mk.condition_id = c.condition_id
), bridge AS (
  SELECT condition_id FROM mirror_books      WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT CASE WHEN br1.condition_id IS NOT NULL THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS conditions_all,
       count(*) FILTER (WHERE s.trading_pnl IS NOT NULL) AS conditions_settled,
       round((100.0 * count(*) FILTER (WHERE s.trading_pnl IS NOT NULL)
              / count(*))::numeric, 2) AS pct_settled,
       round(sum(s.acq_cost) FILTER (WHERE s.trading_pnl IS NOT NULL)::numeric, 0)
         AS acq_cost_settled,
       round(sum(s.mq * s.pair_cost) FILTER (WHERE s.trading_pnl IS NOT NULL)::numeric, 0)
         AS matched_cost_settled,
       round((100.0 * sum(s.mq * (1.0 - s.pair_cost)) FILTER (WHERE s.trading_pnl IS NOT NULL)
              / NULLIF(sum(s.mq * s.pair_cost) FILTER (WHERE s.trading_pnl IS NOT NULL), 0)
             )::numeric, 3) AS matched_roi_pct,
       round((100.0 * sum(s.trading_pnl - s.mq * (1.0 - s.pair_cost))
                       FILTER (WHERE s.trading_pnl IS NOT NULL)
              / NULLIF(sum(s.acq_cost - s.mq * s.pair_cost)
                       FILTER (WHERE s.trading_pnl IS NOT NULL), 0)
             )::numeric, 3) AS residual_roi_pct,
       round((100.0 * sum(s.trading_pnl) FILTER (WHERE s.trading_pnl IS NOT NULL)
              / NULLIF(sum(s.acq_cost) FILTER (WHERE s.trading_pnl IS NOT NULL), 0)
             )::numeric, 3) AS total_roi_pct
  FROM s LEFT JOIN br1 ON br1.condition_id = s.condition_id
 GROUP BY 1 ORDER BY 1;


\echo '== 4. AUDIT 2: SETTLED_RETAINED vs NOT_RETAINED, pre-settlement variables =='
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
         sum(sh)      FILTER (WHERE side = 'BUY') AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY') AS cbuy
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
    FROM leg l GROUP BY 1
), fills AS (
  SELECT condition_id, count(*) AS n_fills, max(ts) AS last_fill,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY sh) AS p50_sz,
         percentile_cont(0.9) WITHIN GROUP (ORDER BY sh) AS p90_sz,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY px) AS p50_px,
         sum(CASE WHEN feed = 'venue' THEN 1 ELSE 0 END)::float8 / count(*) AS venue_frac,
         sum(CASE WHEN px < 0.25 THEN sh * px ELSE 0 END) AS acq_lo
    FROM inwin WHERE side = 'BUY' GROUP BY 1
), bridge AS (
  SELECT condition_id FROM mirror_books      WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT CASE
         WHEN mk.resolved AND mk.resolved_prices IS NOT NULL
           THEN '1 SETTLED_RETAINED'
         WHEN mk.condition_id IS NULL
           THEN '4 NO MARKETS ROW AT ALL'
         WHEN mk.resolved AND mk.resolved_prices IS NULL
           THEN '3 RESOLVED_BUT_SETTLEMENT_MISSING'
         ELSE '2 NOT_YET_RESOLVED'
       END AS retention_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_conditions,
       round(sum(c.acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(LEAST(c.qy, c.qn) * (CASE WHEN c.qy > 0 AND c.qn > 0
                                           THEN c.cy / c.qy + c.cn / c.qn END))::numeric, 0)
         AS matched_cost,
       round(sum(LEAST(c.qy, c.qn))::numeric, 0) AS matched_qty,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY
              (CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END))::numeric, 4)
         AS p50_pair_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY f.n_fills)::numeric, 1) AS p50_fills,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY f.p50_sz)::numeric, 1) AS p50_fill_size,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY f.p90_sz)::numeric, 1) AS p90_fill_size,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY f.p50_px)::numeric, 4) AS p50_price,
       round((100.0 * sum(f.acq_lo) / NULLIF(sum(c.acq_cost), 0))::numeric, 2) AS pct_acq_under_25c,
       round((100.0 * sum(f.venue_frac * f.n_fills) / NULLIF(sum(f.n_fills), 0))::numeric, 2)
         AS pct_venue_lane_OUR_INGESTION,
       round((100.0 * count(*) FILTER (WHERE br1.condition_id IS NOT NULL)
              / count(*))::numeric, 2) AS pct_pmus_bridged,
       max(f.last_fill) AS latest_last_fill
  FROM cond c
  JOIN fills f ON f.condition_id = c.condition_id
  LEFT JOIN markets mk ON mk.condition_id = c.condition_id
  LEFT JOIN br1 ON br1.condition_id = c.condition_id
 GROUP BY 1 ORDER BY 1;


\echo '== 5. AUDIT 2 CENSORING: retention by calendar week of last fill =='
WITH base AS (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
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
), c AS (
  SELECT condition_id, max(ts) AS last_fill,
         sum(CASE WHEN side = 'BUY' THEN sh * px ELSE 0 END) AS acq_cost
    FROM inwin GROUP BY 1
)
SELECT date_trunc('week', c.last_fill)::date AS week_of_last_fill,
       count(*) AS conditions,
       round(sum(c.acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * count(*) FILTER (WHERE mk.resolved AND mk.resolved_prices IS NOT NULL)
              / count(*))::numeric, 2) AS pct_settled_retained,
       round((100.0 * count(*) FILTER (WHERE mk.condition_id IS NOT NULL
                                         AND NOT mk.resolved)
              / count(*))::numeric, 2) AS pct_not_yet_resolved,
       round((100.0 * count(*) FILTER (WHERE mk.resolved AND mk.resolved_prices IS NULL)
              / count(*))::numeric, 2) AS pct_resolved_no_payout,
       round((100.0 * count(*) FILTER (WHERE mk.condition_id IS NULL)
              / count(*))::numeric, 2) AS pct_no_markets_row
  FROM c LEFT JOIN markets mk ON mk.condition_id = c.condition_id
 GROUP BY 1 ORDER BY 1;


\echo '== 6. SPORT COMPOSITION by conditions, acquisition cost, matched cost =='
WITH base AS (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
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
         sum(sh)      FILTER (WHERE side = 'BUY') AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY') AS cbuy
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         LEAST(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END),
               max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END)) AS mq,
         CASE WHEN max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) > 0
               AND max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) > 0
              THEN max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END)
                   / max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END)
                 + max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END)
                   / max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END)
         END AS pair_cost
    FROM leg l GROUP BY 1
), bridge AS (
  SELECT condition_id FROM mirror_books      WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT COALESCE(mk.sport, 'NO MARKETS ROW') AS sport,
       CASE WHEN br1.condition_id IS NOT NULL THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS conditions,
       round(sum(c.acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(c.acq_cost) / sum(sum(c.acq_cost)) OVER ())::numeric, 2)
         AS pct_of_all_acq_cost,
       round(sum(c.mq * c.pair_cost)::numeric, 0) AS matched_cost,
       count(*) FILTER (WHERE mk.resolved AND mk.resolved_prices IS NOT NULL)
         AS conds_settled_retained
  FROM cond c
  LEFT JOIN markets mk ON mk.condition_id = c.condition_id
  LEFT JOIN br1 ON br1.condition_id = c.condition_id
 GROUP BY 1, 2 ORDER BY 4 DESC NULLS LAST;
