-- ============================================================================
-- B. REMAINDER CONTAMINATION AUDIT (2026-09-11, read-only). Owner-specified.
--
-- Run 62 reported a quantity as `residual_roi_pct` and the owner refused to
-- let it be called directional residual P&L. It is a SUBTRACTION REMAINDER:
--
--     NON_MATCHED_REMAINDER_PNL = TRADING_PNL - MATCHED_GROSS_PNL
--
-- where MATCHED_GROSS_PNL = M * (1 - pair_cost) is the GROSS PAIR MARGIN
-- LOCKED AT ACQUISITION AND ASSUMED HELD TO SETTLEMENT. Anything that breaks
-- that assumption, or that moves cash outside the two terms, lands silently in
-- the remainder. This file measures how much.
--
-- The word DIRECTIONAL_RESIDUAL_PNL is earned only if the contamination is
-- shown immaterial or is explicitly removed. Nothing below assumes it is.
--
-- ---------------------------------------------------------------------------
-- TWO CONTAMINATION SOURCES ARE SETTLED BY THE SCHEMA BEFORE ANY QUERY RUNS.
-- Both are reported as STRUCTURAL FACTS, and one of them is an UNMEASURABLE,
-- which is not the same as an absent one.
--
--   FEES ARE NOT IN TRADING_PNL. The trade ledger (migration 001_init.sql,
--   trades) carries id, whale_id, tx_hash, asset, condition_id, side, outcome,
--   outcome_index, size, price, notional, market_title, market_slug,
--   event_slug, sport, ts, source, detected_at, enriched_at, dedupe_key.
--   THERE IS NO FEE COLUMN. TRADING_PNL as computed from this ledger is
--   therefore GROSS OF ALL FEES AND OF ANY REBATE, on both sides. So fees are
--   not a contaminant of the remainder -- they are ABSENT FROM BOTH TERMS.
--   Whatever RN1 actually paid or received is outside this ledger entirely.
--
--   POSITION MERGES AND REDEMPTIONS CANNOT BE REPRESENTED AT ALL. The ledger
--   constrains side with CHECK (side IN ('BUY', 'SELL')). There is no row
--   shape for converting a complete YES+NO set back into collateral before
--   resolution, and none for an early redemption. So if RN1 does that, it is
--   INVISIBLE HERE -- it does not appear misclassified, it does not appear at
--   all. The consequence is precise: the in-window ledger would show the pair
--   still held, MATCHED_GROSS_PNL would still book the acquisition margin, and
--   the true cash event would be missing from TRADING_PNL. That is an
--   unquantifiable gap in this dataset, and it is stated as unquantifiable
--   rather than assumed to be zero. Statement 1 confirms the side vocabulary
--   empirically as well as by constraint.
--
-- ---------------------------------------------------------------------------
-- WHAT IS MEASURABLE, AND IS MEASURED BELOW.
--
--   1 SELLS. A sale of inventory already counted in M breaks held-to-
--     settlement. Statement 2 splits by whether the sold quantity can be
--     accounted for by the UNMATCHED remainder alone. The test is asymmetric
--     and is read that way: sell_qty > residual_qty PROVES paired inventory
--     was sold; sell_qty <= residual_qty proves nothing, it merely fails to
--     prove the opposite, because the aggregate ignores order within the
--     condition.
--
--   2 WINDOW-EDGE TRUNCATION. Every figure in runs 58-63 is computed on fills
--     inside 2026-08-05 00:00Z .. 2026-09-11 12:00Z. If a condition also has
--     canonical fills outside that window, the in-window ledger is a SLICE of
--     the position, not the position: M is understated or overstated, and
--     TRADING_PNL pays settlement on a net quantity that is not his true net.
--     Statement 3 measures it, and reports the decisive case separately -- a
--     leg whose in-window SELLs exceed its in-window BUYs, which is direct
--     proof of inventory acquired before the window.
--
--   3 EXCLUDED ROWS. condition_id IS NULL and outcome_index NOT IN (0,1) are
--     dropped by every query in this series. Statement 1 sizes what is lost.
--
-- ---------------------------------------------------------------------------
-- THE CLEAN COHORT, and the honest name for it.
--
--   NO_SELL_NO_SPECIAL_DISPOSITION is operationalized as: settled, structurally
--   eligible, BOTH legs bought, ZERO SELL shares in the window, and NO
--   canonical fills outside the window. The redemption half of the owner's
--   label CANNOT BE VERIFIED from this ledger for the reason given above, so
--   the cohort is reported as NO_SELL_FULLY_IN_WINDOW and the unverifiable
--   half is named rather than quietly folded in.
--
-- Statement 4 compares that cohort against the full settled cohort on the
-- owner's exact list. Statements 5 and 6 carry the comparison into sport,
-- statement 6 being the discriminating test proposed in THE_TENSION.md: does
-- the bridged/unbridged matched-edge gap survive WITHIN sport, or is it the
-- Tennis composition difference (bridged 62.3% vs unbridged 43.3%)?
--
-- COALESCE NOTE. Earlier files relied on sum() skipping NULL matched cost on
-- single-leg conditions. That gives the right total but makes PER-ROW
-- subtraction undefined, and this file subtracts per row. So matched cost and
-- matched gross P&L are COALESCEd to 0 at the row level, which is the correct
-- value for a condition with no completed pair.
--
-- sport is markets.sport, the same class-B source run 62 used. Conditions with
-- no markets row are reported as their own bucket, never dropped.
--
-- Read-only: six SELECTs.
-- ============================================================================


\echo '== 1. SIDE VOCABULARY and the rows this series excludes (raw, in window) =='
SELECT t.side,
       count(*) AS ledger_rows,
       count(*) FILTER (WHERE t.condition_id IS NULL) AS dropped_no_condition_id,
       count(*) FILTER (WHERE t.outcome_index IS NULL) AS dropped_null_outcome_index,
       count(*) FILTER (WHERE t.outcome_index IS NOT NULL
                          AND t.outcome_index NOT IN (0, 1))
         AS dropped_outcome_index_outside_0_1,
       count(*) FILTER (WHERE abs(t.notional::float8
                                  - t.size::float8 * t.price::float8) > 0.01)
         AS stored_notional_disagrees_over_1c,
       round(sum(t.size::float8 * t.price::float8)::numeric, 0) AS gross_notional_usd,
       round(sum(t.size::float8 * t.price::float8)
               FILTER (WHERE t.condition_id IS NULL
                          OR t.outcome_index IS NULL
                          OR t.outcome_index NOT IN (0, 1))::numeric, 0)
         AS dropped_notional_usd
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
   AND t.ts >= timestamptz '2026-08-05 00:00Z'
   AND t.ts <  timestamptz '2026-09-11 12:00Z'
 GROUP BY 1 ORDER BY 1;


\echo '== 2. SELL CONTAMINATION of the held-to-settlement assumption =='
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
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0))  AS acq_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         sum(COALESCE(l.csell, 0)) AS sell_notional,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
    FROM leg l GROUP BY 1
), m AS (
  SELECT c.*,
         LEAST(COALESCE(c.qy, 0), COALESCE(c.qn, 0)) AS mq,
         abs(COALESCE(c.qy, 0) - COALESCE(c.qn, 0))  AS residual_qty,
         CASE WHEN COALESCE(c.qy, 0) > 0 AND COALESCE(c.qn, 0) > 0
              THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost
    FROM cond c
), k AS (
  SELECT m.*,
         COALESCE(m.mq * m.pair_cost, 0)         AS mcost,
         COALESCE(m.mq * (1.0 - m.pair_cost), 0) AS mpnl,
         CASE
           WHEN m.sell_qty = 0 THEN '1 NO SELL IN WINDOW'
           WHEN m.sell_qty <= m.residual_qty
             THEN '2 SELL FITS INSIDE THE UNMATCHED REMAINDER (not proof of either case)'
           ELSE '3 SELL EXCEEDS THE UNMATCHED REMAINDER (paired inventory provably sold)'
         END AS sell_class
    FROM m
), bridge AS (
  SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT k.sell_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(k.acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(k.acq_cost) / sum(sum(k.acq_cost)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       round(sum(k.mcost)::numeric, 0) AS matched_cost,
       round((100.0 * sum(k.mcost) / sum(sum(k.mcost)) OVER ())::numeric, 3)
         AS pct_matched_cost,
       round(sum(k.sell_qty)::numeric, 0) AS sell_shares,
       round(sum(k.sell_notional)::numeric, 0) AS sell_notional_usd,
       round(greatest(sum(k.sell_qty - k.residual_qty), 0)::numeric, 0)
         AS paired_shares_provably_sold,
       count(*) FILTER (WHERE mk.resolved AND mk.resolved_prices IS NOT NULL)
         AS conditions_settled,
       round((100.0 * count(*) FILTER (WHERE br1.condition_id IS NOT NULL)
              / count(*))::numeric, 2) AS pct_bridged
  FROM k
  LEFT JOIN br1     ON br1.condition_id = k.condition_id
  LEFT JOIN markets mk ON mk.condition_id = k.condition_id
 GROUP BY 1 ORDER BY 1;


\echo '== 3. WINDOW-EDGE TRUNCATION: is the in-window ledger the whole position? =='
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
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         bool_or(COALESCE(l.qsell, 0) > COALESCE(l.qbuy, 0)) AS leg_oversold,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
    FROM leg l GROUP BY 1
), edges AS (
  SELECT condition_id,
         count(*) FILTER (WHERE ts <  timestamptz '2026-08-05 00:00Z') AS n_before,
         count(*) FILTER (WHERE ts >= timestamptz '2026-09-11 12:00Z') AS n_after,
         round(sum(sh * px) FILTER (WHERE ts <  timestamptz '2026-08-05 00:00Z'
                                      AND side = 'BUY')::numeric, 0) AS acq_before,
         round(sum(sh * px) FILTER (WHERE ts >= timestamptz '2026-09-11 12:00Z'
                                      AND side = 'BUY')::numeric, 0) AS acq_after
    FROM canon GROUP BY 1
), k AS (
  SELECT c.*,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS mcost,
         COALESCE(e.n_before, 0) AS n_before,
         COALESCE(e.n_after, 0)  AS n_after,
         COALESCE(e.acq_before, 0) AS acq_before,
         COALESCE(e.acq_after, 0)  AS acq_after
    FROM cond c LEFT JOIN edges e ON e.condition_id = c.condition_id
)
SELECT CASE
         WHEN n_before = 0 AND n_after = 0 THEN '1 FULLY INSIDE THE WINDOW'
         WHEN n_before > 0 AND n_after = 0 THEN '2 ALSO HAS FILLS BEFORE THE WINDOW'
         WHEN n_before = 0 AND n_after > 0 THEN '3 ALSO HAS FILLS AFTER THE WINDOW'
         ELSE '4 FILLS ON BOTH SIDES OF THE WINDOW'
       END AS window_class,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost_in_window,
       round((100.0 * sum(acq_cost) / sum(sum(acq_cost)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       round(sum(mcost)::numeric, 0) AS matched_cost_in_window,
       round((100.0 * sum(mcost) / sum(sum(mcost)) OVER ())::numeric, 3)
         AS pct_matched_cost,
       sum(n_before) AS fills_before,
       sum(acq_before) AS buy_notional_before_usd,
       sum(n_after) AS fills_after,
       sum(acq_after) AS buy_notional_after_usd,
       count(*) FILTER (WHERE leg_oversold)
         AS conditions_with_a_leg_sold_beyond_its_in_window_buys
  FROM k GROUP BY 1 ORDER BY 1;


\echo '== 4. CLEAN COHORT vs FULL SETTLED COHORT, owner comparison list =='
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
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn,
         max(CASE WHEN l.outcome_index = 0
                  THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END) AS ny,
         max(CASE WHEN l.outcome_index = 1
                  THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END) AS nn
    FROM leg l GROUP BY 1
), edges AS (
  SELECT condition_id, count(*) AS n_out FROM canon
   WHERE ts <  timestamptz '2026-08-05 00:00Z'
      OR ts >= timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS (
  SELECT mt.condition_id, count(*) AS n_tokens,
         count(DISTINCT mt.outcome_index) AS n_idx,
         count(*) FILTER (WHERE mt.outcome_index IS NULL) AS n_null,
         bool_or(mt.outcome_index = 0) AS has0,
         bool_or(mt.outcome_index = 1) AS has1,
         max(mt.outcome_index) AS max_idx
    FROM market_tokens mt GROUP BY 1
), s AS (
  SELECT c.*,
         COALESCE(e.n_out, 0) AS n_out,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS mcost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (1.0 - (CASE WHEN c.qy > 0 AND c.qn > 0
                                 THEN c.cy / c.qy + c.cn / c.qn END)), 0) AS mpnl,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL THEN
           (   (mk.resolved_prices->>0)::float8 * COALESCE(c.ny, 0)
             + (mk.resolved_prices->>1)::float8 * COALESCE(c.nn, 0)
             - c.net_cost )
         END AS trading_pnl
    FROM cond c
    LEFT JOIN edges e ON e.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    LEFT JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
), bridge AS (
  SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge
), g AS (
  SELECT s.*, (br1.condition_id IS NOT NULL) AS bridged,
         (s.sell_qty = 0 AND s.n_out = 0 AND s.qy > 0 AND s.qn > 0) AS clean
    FROM s LEFT JOIN br1 ON br1.condition_id = s.condition_id
   WHERE s.trading_pnl IS NOT NULL
)
SELECT '1 FULL SETTLED COHORT (structurally eligible)' AS cohort,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(mcost)::numeric, 0) AS matched_cost,
       round(sum(acq_cost - mcost)::numeric, 0) AS residual_capital,
       round(sum(mpnl)::numeric, 0) AS matched_gross_pnl,
       round((100.0 * sum(mpnl) / NULLIF(sum(mcost), 0))::numeric, 3) AS matched_roi_pct,
       round(sum(trading_pnl - mpnl)::numeric, 0) AS non_matched_remainder_pnl,
       round((100.0 * sum(trading_pnl - mpnl)
              / NULLIF(sum(acq_cost - mcost), 0))::numeric, 3) AS remainder_roi_pct,
       round(sum(trading_pnl)::numeric, 0) AS trading_pnl,
       round((100.0 * sum(trading_pnl) / NULLIF(sum(acq_cost), 0))::numeric, 3)
         AS total_roi_pct,
       round((100.0 * count(*) FILTER (WHERE bridged) / count(*))::numeric, 2) AS pct_bridged
  FROM g
UNION ALL
SELECT '2 CLEAN COHORT (no sell, fully in window, both legs)',
       count(*),
       round(sum(acq_cost)::numeric, 0),
       round(sum(mcost)::numeric, 0),
       round(sum(acq_cost - mcost)::numeric, 0),
       round(sum(mpnl)::numeric, 0),
       round((100.0 * sum(mpnl) / NULLIF(sum(mcost), 0))::numeric, 3),
       round(sum(trading_pnl - mpnl)::numeric, 0),
       round((100.0 * sum(trading_pnl - mpnl)
              / NULLIF(sum(acq_cost - mcost), 0))::numeric, 3),
       round(sum(trading_pnl)::numeric, 0),
       round((100.0 * sum(trading_pnl) / NULLIF(sum(acq_cost), 0))::numeric, 3),
       round((100.0 * count(*) FILTER (WHERE bridged) / count(*))::numeric, 2)
  FROM g WHERE clean
UNION ALL
SELECT '3 EXCLUDED BY THE CLEAN FILTER',
       count(*),
       round(sum(acq_cost)::numeric, 0),
       round(sum(mcost)::numeric, 0),
       round(sum(acq_cost - mcost)::numeric, 0),
       round(sum(mpnl)::numeric, 0),
       round((100.0 * sum(mpnl) / NULLIF(sum(mcost), 0))::numeric, 3),
       round(sum(trading_pnl - mpnl)::numeric, 0),
       round((100.0 * sum(trading_pnl - mpnl)
              / NULLIF(sum(acq_cost - mcost), 0))::numeric, 3),
       round(sum(trading_pnl)::numeric, 0),
       round((100.0 * sum(trading_pnl) / NULLIF(sum(acq_cost), 0))::numeric, 3),
       round((100.0 * count(*) FILTER (WHERE bridged) / count(*))::numeric, 2)
  FROM g WHERE NOT clean
 ORDER BY 1;


\echo '== 5. THE SAME TWO COHORTS BY SPORT =='
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
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn,
         max(CASE WHEN l.outcome_index = 0
                  THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END) AS ny,
         max(CASE WHEN l.outcome_index = 1
                  THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END) AS nn
    FROM leg l GROUP BY 1
), edges AS (
  SELECT condition_id, count(*) AS n_out FROM canon
   WHERE ts <  timestamptz '2026-08-05 00:00Z'
      OR ts >= timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS (
  SELECT mt.condition_id, count(*) AS n_tokens,
         count(DISTINCT mt.outcome_index) AS n_idx,
         count(*) FILTER (WHERE mt.outcome_index IS NULL) AS n_null,
         bool_or(mt.outcome_index = 0) AS has0,
         bool_or(mt.outcome_index = 1) AS has1,
         max(mt.outcome_index) AS max_idx
    FROM market_tokens mt GROUP BY 1
), s AS (
  SELECT c.*,
         COALESCE(mk.sport, 'NO MARKETS ROW') AS sport,
         COALESCE(e.n_out, 0) AS n_out,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS mcost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (1.0 - (CASE WHEN c.qy > 0 AND c.qn > 0
                                 THEN c.cy / c.qy + c.cn / c.qn END)), 0) AS mpnl,
         CASE WHEN mk.resolved AND mk.resolved_prices IS NOT NULL THEN
           (   (mk.resolved_prices->>0)::float8 * COALESCE(c.ny, 0)
             + (mk.resolved_prices->>1)::float8 * COALESCE(c.nn, 0)
             - c.net_cost )
         END AS trading_pnl
    FROM cond c
    LEFT JOIN edges e ON e.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    LEFT JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
), g AS (
  SELECT s.*, (s.sell_qty = 0 AND s.n_out = 0 AND s.qy > 0 AND s.qn > 0) AS clean
    FROM s WHERE s.trading_pnl IS NOT NULL
)
SELECT sport,
       count(*) AS settled_conditions,
       count(*) FILTER (WHERE clean) AS clean_conditions,
       round((100.0 * count(*) FILTER (WHERE clean) / count(*))::numeric, 2) AS pct_clean,
       round(sum(acq_cost)::numeric, 0) AS acq_cost_settled,
       round(sum(acq_cost) FILTER (WHERE clean)::numeric, 0) AS acq_cost_clean,
       round((100.0 * sum(mpnl) / NULLIF(sum(mcost), 0))::numeric, 3)
         AS matched_roi_settled,
       round((100.0 * sum(mpnl) FILTER (WHERE clean)
              / NULLIF(sum(mcost) FILTER (WHERE clean), 0))::numeric, 3)
         AS matched_roi_clean,
       round((100.0 * sum(trading_pnl - mpnl)
              / NULLIF(sum(acq_cost - mcost), 0))::numeric, 3)
         AS remainder_roi_settled,
       round((100.0 * sum(trading_pnl - mpnl) FILTER (WHERE clean)
              / NULLIF(sum(acq_cost - mcost) FILTER (WHERE clean), 0))::numeric, 3)
         AS remainder_roi_clean
  FROM g GROUP BY 1 ORDER BY 5 DESC;


\echo '== 6. THE DISCRIMINATING TEST: bridged vs unbridged matched edge WITHIN sport =='
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
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
    FROM leg l GROUP BY 1
), tok AS (
  SELECT mt.condition_id, count(*) AS n_tokens,
         count(DISTINCT mt.outcome_index) AS n_idx,
         count(*) FILTER (WHERE mt.outcome_index IS NULL) AS n_null,
         bool_or(mt.outcome_index = 0) AS has0,
         bool_or(mt.outcome_index = 1) AS has1,
         max(mt.outcome_index) AS max_idx
    FROM market_tokens mt GROUP BY 1
), elig AS (
  SELECT c.condition_id, c.acq_cost,
         COALESCE(mk.sport, 'NO MARKETS ROW') AS sport,
         LEAST(c.qy, c.qn) AS mq,
         CASE WHEN c.qy > 0 AND c.qn > 0
              THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    LEFT JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
), bridge AS (
  SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge
), e AS (
  SELECT elig.*, (br1.condition_id IS NOT NULL) AS bridged,
         COALESCE(elig.mq * elig.pair_cost, 0)         AS mcost,
         COALESCE(elig.mq * (1.0 - elig.pair_cost), 0) AS mpnl
    FROM elig LEFT JOIN br1 ON br1.condition_id = elig.condition_id
)
SELECT sport,
       count(*) FILTER (WHERE bridged) AS cond_bridged,
       count(*) FILTER (WHERE NOT bridged) AS cond_unbridged,
       round(sum(mcost) FILTER (WHERE bridged)::numeric, 0) AS matched_cost_bridged,
       round(sum(mcost) FILTER (WHERE NOT bridged)::numeric, 0) AS matched_cost_unbridged,
       round((100.0 * sum(mpnl) FILTER (WHERE bridged)
              / NULLIF(sum(mcost) FILTER (WHERE bridged), 0))::numeric, 3)
         AS matched_roi_bridged,
       round((100.0 * sum(mpnl) FILTER (WHERE NOT bridged)
              / NULLIF(sum(mcost) FILTER (WHERE NOT bridged), 0))::numeric, 3)
         AS matched_roi_unbridged,
       round((100.0 * sum(mpnl) / NULLIF(sum(mcost), 0))::numeric, 3) AS matched_roi_all,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost)
               FILTER (WHERE bridged)::numeric, 4) AS p50_pair_cost_bridged,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost)
               FILTER (WHERE NOT bridged)::numeric, 4) AS p50_pair_cost_unbridged
  FROM e GROUP BY 1 ORDER BY sum(mcost) DESC;
