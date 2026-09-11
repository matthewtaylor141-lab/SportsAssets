-- ============================================================================
-- B (continued). THE COHORT COMPARISONS -- statements 4, 5, 6 of run 64,
-- re-issued after run 64 timed out on statement 4. (2026-09-11, read-only.)
--
-- WHY THIS IS A SEPARATE FILE. Run 64's statements 1-3 COMPLETED and their
-- results are recorded; re-running them costs ~95 s and returns nothing new.
-- Only statement 4 hit the 600 s statement timeout. Statements 5 and 6 never
-- ran and are carried here unchanged in substance.
--
-- WHAT RUN 64 ALREADY ESTABLISHED, and why it simplifies everything below:
--
--   STATEMENT 1. In the window RN1's canonical ledger contains EXACTLY ONE
--   side value: BUY. 408,094 rows, all BUY, zero SELL. Excluded from this
--   series: 18,068 rows with no condition_id (the same rows carry a null
--   outcome_index) and 422 with an outcome_index outside {0,1} -- together
--   $3,103,918 of $93,103,963 gross BUY notional (3.33%). 12 rows have a
--   stored `notional` disagreeing with size*price by more than a cent.
--
--   STATEMENT 2. SELL CONTAMINATION IS EXACTLY ZERO, not merely small.
--   26,248 of 26,248 conditions (100% of conditions, 100% of acquisition
--   cost, 100% of matched cost) are NO SELL IN WINDOW. Zero sell shares, zero
--   sell notional, zero paired shares provably sold.
--
--     This is consistent with the venue position model established earlier and
--     is not a surprise: on his venue a position is reduced by BUYING THE
--     COMPLEMENT, so a reduction books a BUY row on the other token, never a
--     SELL row. The 32,847 SELL events in run 59 were OUR required actions
--     under the PMUS one-signed-position model, not RN1 SELL fills. Those two
--     things have the same word and are not the same object.
--
--   STATEMENT 3. WINDOW-EDGE TRUNCATION IS IMMATERIAL. 26,171 of 26,248
--   conditions (99.707%) lie fully inside the window. 56 conditions (0.213%,
--   $165,573 = 0.221% of acquisition cost) also have fills BEFORE it; 21
--   (0.080%, $58,538 = 0.078%) also have fills AFTER it. None on both sides.
--   ZERO conditions have a leg whose in-window sells exceed its in-window buys
--   -- trivially so, since there are no sells at all.
--
-- CONSEQUENCE FOR THE CLEAN COHORT. With no sells anywhere, the clean filter
-- reduces to FULLY IN WINDOW plus BOTH LEGS BOUGHT, and it retains 99.7% of
-- acquisition cost. The `clean` flag is still computed from the sell test as
-- well as the window test, so the definition does not silently change between
-- files -- the sell term is simply never the binding one.
--
-- THE STATEMENT-4 TIMEOUT, AND WHAT IS DONE ABOUT IT. Statement 4 joined cond,
-- edges, tok, markets and the bridge union in one shot and did not return in
-- 600 s, while structurally similar statements in run 63 returned in seconds.
-- Rather than theorize about which join collapsed, every heavy CTE below is
-- declared AS MATERIALIZED. That forces one-pass tuplestore evaluation and
-- structurally prevents per-outer-row re-execution REGARDLESS of the planner's
-- estimates -- the same fix that resolved the execution bridge's
-- non-termination, where a CASE-over-aggregate join condition had no
-- statistics and selectivities multiplied down to the rows=1 floor.
--
-- The three-way UNION ALL over the same CTE is also replaced by a single pass
-- with GROUPING SETS, so the cohort split and the total come from one scan.
--
-- Read-only: three SELECTs.
-- ============================================================================


\echo '== 4. CLEAN COHORT vs FULL SETTLED COHORT, owner comparison list =='
WITH base AS MATERIALIZED (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), leg AS MATERIALIZED (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END), 0) AS cn,
         COALESCE(max(CASE WHEN l.outcome_index = 0
                           THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END), 0) AS ny,
         COALESCE(max(CASE WHEN l.outcome_index = 1
                           THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END), 0) AS nn
    FROM leg l GROUP BY 1
), edges AS MATERIALIZED (
  SELECT condition_id, count(*) AS n_out FROM canon
   WHERE ts <  timestamptz '2026-08-05 00:00Z'
      OR ts >= timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id
    FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2
      AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0)
      AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) u WHERE condition_id IS NOT NULL
), g AS MATERIALIZED (
  SELECT c.condition_id, c.acq_cost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS mcost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (1.0 - (CASE WHEN c.qy > 0 AND c.qn > 0
                                 THEN c.cy / c.qy + c.cn / c.qn END)), 0) AS mpnl,
         (mk.resolved_prices->>0)::float8 * c.ny
           + (mk.resolved_prices->>1)::float8 * c.nn - c.net_cost AS trading_pnl,
         (br1.condition_id IS NOT NULL) AS bridged,
         (c.sell_qty = 0 AND COALESCE(ed.n_out, 0) = 0 AND c.qy > 0 AND c.qn > 0) AS clean
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
    LEFT JOIN br1     ON br1.condition_id = c.condition_id
   WHERE mk.resolved AND mk.resolved_prices IS NOT NULL
)
SELECT CASE WHEN grouping(clean) = 1 THEN '3 ALL SETTLED ELIGIBLE'
            WHEN clean THEN '1 CLEAN (no sell, fully in window, both legs)'
            ELSE '2 EXCLUDED BY THE CLEAN FILTER' END AS cohort,
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
  FROM g GROUP BY GROUPING SETS ((clean), ()) ORDER BY 1;


\echo '== 5. THE SAME TWO COHORTS BY SPORT =='
WITH base AS MATERIALIZED (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), leg AS MATERIALIZED (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY')  AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY')  AS cbuy,
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell,
         sum(sh * px) FILTER (WHERE side = 'SELL') AS csell
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         sum(COALESCE(l.cbuy, 0) - COALESCE(l.csell, 0)) AS net_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END), 0) AS cn,
         COALESCE(max(CASE WHEN l.outcome_index = 0
                           THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END), 0) AS ny,
         COALESCE(max(CASE WHEN l.outcome_index = 1
                           THEN COALESCE(l.qbuy, 0) - COALESCE(l.qsell, 0) END), 0) AS nn
    FROM leg l GROUP BY 1
), edges AS MATERIALIZED (
  SELECT condition_id, count(*) AS n_out FROM canon
   WHERE ts <  timestamptz '2026-08-05 00:00Z'
      OR ts >= timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id
    FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2
      AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0)
      AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), g AS MATERIALIZED (
  SELECT c.condition_id, c.acq_cost,
         COALESCE(mk.sport, 'NO MARKETS ROW') AS sport,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS mcost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (1.0 - (CASE WHEN c.qy > 0 AND c.qn > 0
                                 THEN c.cy / c.qy + c.cn / c.qn END)), 0) AS mpnl,
         (mk.resolved_prices->>0)::float8 * c.ny
           + (mk.resolved_prices->>1)::float8 * c.nn - c.net_cost AS trading_pnl,
         (c.sell_qty = 0 AND COALESCE(ed.n_out, 0) = 0 AND c.qy > 0 AND c.qn > 0) AS clean
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
   WHERE mk.resolved AND mk.resolved_prices IS NOT NULL
)
SELECT sport,
       count(*) AS settled_conditions,
       count(*) FILTER (WHERE clean) AS clean_conditions,
       round((100.0 * count(*) FILTER (WHERE clean) / count(*))::numeric, 2) AS pct_clean,
       round(sum(acq_cost)::numeric, 0) AS acq_cost_settled,
       round(sum(acq_cost) FILTER (WHERE clean)::numeric, 0) AS acq_cost_clean,
       round((100.0 * sum(mpnl) / NULLIF(sum(mcost), 0))::numeric, 3) AS matched_roi_settled,
       round((100.0 * sum(mpnl) FILTER (WHERE clean)
              / NULLIF(sum(mcost) FILTER (WHERE clean), 0))::numeric, 3) AS matched_roi_clean,
       round((100.0 * sum(trading_pnl - mpnl)
              / NULLIF(sum(acq_cost - mcost), 0))::numeric, 3) AS remainder_roi_settled,
       round((100.0 * sum(trading_pnl - mpnl) FILTER (WHERE clean)
              / NULLIF(sum(acq_cost - mcost) FILTER (WHERE clean), 0))::numeric, 3)
         AS remainder_roi_clean
  FROM g GROUP BY 1 ORDER BY 5 DESC;


\echo '== 6. THE DISCRIMINATING TEST: bridged vs unbridged matched edge WITHIN sport =='
WITH base AS MATERIALIZED (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px, t.side,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), canon AS MATERIALIZED (
  SELECT z.* FROM (
    SELECT b.*, bool_or(b.feed = 'venue')
                  OVER (PARTITION BY b.tx_hash, b.asset) AS any_venue
      FROM base b) z
   WHERE NOT (z.feed = 'cash' AND z.any_venue)
), leg AS MATERIALIZED (
  SELECT condition_id, outcome_index,
         sum(sh)      FILTER (WHERE side = 'BUY') AS qbuy,
         sum(sh * px) FILTER (WHERE side = 'BUY') AS cbuy
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1, 2
), cond AS MATERIALIZED (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0)) AS acq_cost,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END), 0) AS qy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END), 0) AS qn,
         COALESCE(max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END), 0) AS cy,
         COALESCE(max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END), 0) AS cn
    FROM leg l GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id
    FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2
      AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0)
      AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) u WHERE condition_id IS NOT NULL
), e AS MATERIALIZED (
  SELECT c.condition_id, c.acq_cost,
         COALESCE(mk.sport, 'NO MARKETS ROW') AS sport,
         (br1.condition_id IS NOT NULL) AS bridged,
         CASE WHEN c.qy > 0 AND c.qn > 0
              THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS mcost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (1.0 - (CASE WHEN c.qy > 0 AND c.qn > 0
                                 THEN c.cy / c.qy + c.cn / c.qn END)), 0) AS mpnl
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    LEFT JOIN markets mk ON mk.condition_id = c.condition_id
    LEFT JOIN br1     ON br1.condition_id = c.condition_id
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
