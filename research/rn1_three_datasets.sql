-- ============================================================================
-- RAW vs D1 vs CANONICAL, and the SAME-CLOCK 2x2 STATE-MODEL TEST
-- (2026-09-11, read-only.)
--
-- THREE DATASETS, built from one table, differing only in which rows survive.
--
--   RAW        every row as stored. Double-counts every envelope that both
--              feeds saw, because the venue price and the cash price differ
--              by the fee and so produce different dedupe keys.
--   D1         one row per (tx_hash, asset, side), cash feed first -- what the
--              live mirror reads today. Destroys every genuine multi-fill
--              inside one transaction.
--   CANONICAL  the venue feed's decomposition where the venue feed saw the
--              envelope, the cash feed's otherwise. Never the sum.
--
-- THE 2x2 THE OWNER ASKED FOR. Two axes, evaluated on the SAME fill stream in
-- the same time order, so the clock can never differ between cells:
--
--   AXIS 1  the dataset          CANONICAL or D1
--   AXIS 2  the state model      incremental matched inventory, or net position
--
-- The state models, stated so the divergence is legible:
--
--   NET POSITION (what production does today). The mirror reads his signed
--   net per market, qY - qN. When he BUYS the other token his net FALLS, and
--   the mirror reads that as him reducing -- so it SELLS. That is the
--   suspected structural bug.
--
--   INCREMENTAL MATCHED INVENTORY (the proposed replacement).
--       dM_t = max( min(Y_t, N_t) - min(Y_{t-1}, N_{t-1}), 0 )
--   When he BUYS the other token, dM RISES: he has COMPLETED a pair. Capital
--   comes back, the position is not reduced, and nothing should be sold.
--
-- THE DIVERGENCE CELL is the fill where dM > 0 AND net falls. Under the net
-- model the mirror sells; under the matched model it holds. Counting those
-- fills, and the dollars on them, is the direct measurement of what the
-- net-position reading has been costing -- separately for CANONICAL and D1,
-- so the damage done by D1 is never confused with the damage done by the
-- state model.
--
-- WHAT THIS FILE DOES NOT CLAIM. It measures HIS fill stream and what each
-- state model would have said about it. It does not simulate our fills, our
-- queue position or our latency. No theoretical fill appears anywhere.
--
-- Read-only: four SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE THREE DATASETS: how many rows each keeps, and what they weigh =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), d1 AS (
  SELECT DISTINCT ON (tx_hash, asset, side) id
    FROM base
   ORDER BY tx_hash, asset, side, CASE WHEN feed = 'cash' THEN 0 ELSE 1 END, ts, id
), s AS (
  SELECT 'RAW' AS ds, b.* FROM base b
  UNION ALL
  SELECT 'D1', b.* FROM base b JOIN d1 ON d1.id = b.id
  UNION ALL
  SELECT 'CANONICAL', b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
)
SELECT ds AS dataset, count(*) AS fills,
       count(DISTINCT (tx_hash, asset, side)) AS envelopes,
       round((count(*)::numeric / NULLIF(count(DISTINCT (tx_hash, asset, side)), 0)), 3)
         AS fills_per_envelope,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(sh * px)::numeric, 0) AS dollars,
       count(*) FILTER (WHERE side = 'SELL') AS sells
  FROM s GROUP BY 1 ORDER BY 2 DESC;


\echo '== 2. SECTION 5 RECOMPUTED ON EACH DATASET: does the edge move? =='
-- Canonical residual definition (the forensic report's), on settled two-way
-- conditions only. Unsettled are excluded, never defaulted.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.ts >= now() - interval '21 days'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), d1 AS (
  SELECT DISTINCT ON (tx_hash, asset, side) id
    FROM base
   ORDER BY tx_hash, asset, side, CASE WHEN feed = 'cash' THEN 0 ELSE 1 END, ts, id
), s AS (
  SELECT 'RAW' AS ds, b.* FROM base b
  UNION ALL
  SELECT 'D1', b.* FROM base b JOIN d1 ON d1.id = b.id
  UNION ALL
  SELECT 'CANONICAL', b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), mk AS (
  SELECT ds, condition_id,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS b0,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS b1,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS x0,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS x1,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS c0,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS c1,
         COALESCE(sum(sh * px) FILTER (WHERE side = 'SELL'), 0) AS proceeds
    FROM s GROUP BY 1, 2
), e AS (
  SELECT mk.ds, (mk.b0 - mk.x0) AS y, (mk.b1 - mk.x1) AS n,
         (mk.c0 + mk.c1 - mk.proceeds) AS net_cost,
         COALESCE(CASE WHEN mk.b0 > 0 THEN mk.c0 / mk.b0 END, 0) AS v0,
         COALESCE(CASE WHEN mk.b1 > 0 THEN mk.c1 / mk.b1 END, 0) AS v1,
         (m.resolved_prices ->> 0)::float8 AS p0,
         (m.resolved_prices ->> 1)::float8 AS p1
    FROM mk JOIN markets m ON m.condition_id = mk.condition_id
   WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
     AND jsonb_array_length(m.resolved_prices) = 2
), g AS (
  SELECT ds, net_cost,
         LEAST(GREATEST(y, 0), GREATEST(n, 0)) AS matched,
         (y - n) AS resid, (v0 + v1) AS pair_cost,
         (y * p0 + n * p1 - net_cost) AS total_pnl
    FROM e
)
SELECT ds AS dataset, count(*) AS markets,
       round(sum(net_cost)::numeric, 0) AS total_cost,
       round(sum(total_pnl)::numeric, 0) AS TOTAL_PNL,
       round(sum(matched)::numeric, 0) AS matched_qty,
       round(sum(matched * (1.0 - pair_cost))::numeric, 0) AS matched_pnl,
       round((100.0 * sum(matched * (1.0 - pair_cost))
              / NULLIF(sum(matched * pair_cost), 0))::numeric, 2) AS matched_roi_pct,
       round(sum(abs(resid))::numeric, 0) AS unmatched_sh,
       round(sum(total_pnl - matched * (1.0 - pair_cost))::numeric, 0) AS directional_pnl,
       round((100.0 * sum(total_pnl - matched * (1.0 - pair_cost))
              / NULLIF(sum(net_cost - matched * pair_cost), 0))::numeric, 2)
         AS directional_roi_pct
  FROM g GROUP BY 1 ORDER BY 4 DESC;


\echo '== 3. SECTION 6 RECOMPUTED: what each fill does to matched inventory =='
-- dM = max( min(Y,N) - min(Y,N)_prev, 0 ), fungible, in fill order.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.ts >= now() - interval '21 days'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), d1 AS (
  SELECT DISTINCT ON (tx_hash, asset, side) id
    FROM base
   ORDER BY tx_hash, asset, side, CASE WHEN feed = 'cash' THEN 0 ELSE 1 END, ts, id
), s AS (
  SELECT 'RAW' AS ds, b.* FROM base b
  UNION ALL
  SELECT 'D1', b.* FROM base b JOIN d1 ON d1.id = b.id
  UNION ALL
  SELECT 'CANONICAL', b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), r AS (
  SELECT ds, condition_id, id, ts, side, sh, px,
         sum(CASE WHEN outcome_index = 0
                  THEN CASE WHEN side = 'BUY' THEN sh ELSE -sh END ELSE 0 END)
           OVER (PARTITION BY ds, condition_id ORDER BY ts, id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS y,
         sum(CASE WHEN outcome_index = 1
                  THEN CASE WHEN side = 'BUY' THEN sh ELSE -sh END ELSE 0 END)
           OVER (PARTITION BY ds, condition_id ORDER BY ts, id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n
    FROM s
), ev AS (
  SELECT ds, sh, px, side,
         LEAST(GREATEST(y, 0), GREATEST(n, 0)) AS m,
         COALESCE(LEAST(GREATEST(lag(y) OVER w, 0), GREATEST(lag(n) OVER w, 0)), 0) AS m_prev
    FROM r WINDOW w AS (PARTITION BY ds, condition_id ORDER BY ts, id)
), d AS (
  SELECT ds, sh, px, side, GREATEST(m - m_prev, 0) AS dm FROM ev
)
SELECT ds AS dataset, count(*) AS fills,
       count(*) FILTER (WHERE dm <= 0.000001) AS adds_only_unmatched,
       count(*) FILTER (WHERE dm >= sh - 0.000001 AND dm > 0.000001) AS FULLY_matched_by_this_fill,
       count(*) FILTER (WHERE dm > 0.000001 AND dm < sh - 0.000001) AS PARTLY_matched,
       round((100.0 * count(*) FILTER (WHERE dm > 0.000001 AND dm < sh - 0.000001)
              / NULLIF(count(*), 0))::numeric, 2) AS pct_partly,
       round(avg(dm / NULLIF(sh, 0)) FILTER (WHERE dm > 0.000001 AND dm < sh - 0.000001)::numeric, 4)
         AS mean_dM_over_size_on_partials,
       round(avg(sh) FILTER (WHERE dm > 0.000001 AND dm < sh - 0.000001)::numeric, 0)
         AS avg_clip_on_partials,
       round(avg(sh)::numeric, 0) AS avg_clip_all,
       round(sum(dm)::numeric, 0) AS total_dM_shares,
       round(sum(dm * px)::numeric, 0) AS total_dM_dollars
  FROM d GROUP BY 1 ORDER BY 2 DESC;


\echo '== 4. THE SAME-CLOCK 2x2: dataset x state model, on one fill stream =='
-- Every cell reads the SAME rows in the SAME order. Only the question asked
-- of each fill changes. The divergence cell -- dM rises AND net falls -- is
-- where the net-position model sells something the matched model holds.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.ts >= now() - interval '21 days'
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), d1 AS (
  SELECT DISTINCT ON (tx_hash, asset, side) id
    FROM base
   ORDER BY tx_hash, asset, side, CASE WHEN feed = 'cash' THEN 0 ELSE 1 END, ts, id
), s AS (
  SELECT 'D1' AS ds, b.* FROM base b JOIN d1 ON d1.id = b.id
  UNION ALL
  SELECT 'CANONICAL', b.* FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
), r AS (
  SELECT ds, condition_id, id, ts, side, sh, px,
         sum(CASE WHEN outcome_index = 0
                  THEN CASE WHEN side = 'BUY' THEN sh ELSE -sh END ELSE 0 END)
           OVER (PARTITION BY ds, condition_id ORDER BY ts, id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS y,
         sum(CASE WHEN outcome_index = 1
                  THEN CASE WHEN side = 'BUY' THEN sh ELSE -sh END ELSE 0 END)
           OVER (PARTITION BY ds, condition_id ORDER BY ts, id
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n
    FROM s
), ev AS (
  SELECT ds, sh, px, side, y, n,
         LEAST(GREATEST(y, 0), GREATEST(n, 0)) AS m,
         COALESCE(LEAST(GREATEST(lag(y) OVER w, 0), GREATEST(lag(n) OVER w, 0)), 0) AS m_prev,
         abs(y - n) AS absnet,
         COALESCE(abs(lag(y) OVER w - lag(n) OVER w), 0) AS absnet_prev
    FROM r WINDOW w AS (PARTITION BY ds, condition_id ORDER BY ts, id)
), c AS (
  SELECT ds, sh, px, side,
         GREATEST(m - m_prev, 0) > 0.000001 AS completes_a_pair,
         absnet < absnet_prev - 0.000001    AS net_position_falls,
         GREATEST(m - m_prev, 0) AS dm, (absnet_prev - absnet) AS net_drop
    FROM ev
)
SELECT ds AS dataset,
       CASE WHEN completes_a_pair AND net_position_falls
              THEN '1 DIVERGENCE: net model SELLS, matched model HOLDS'
            WHEN completes_a_pair AND NOT net_position_falls
              THEN '2 both hold (pair completed, net did not fall)'
            WHEN NOT completes_a_pair AND net_position_falls
              THEN '3 both reduce (a genuine reduction)'
            ELSE '4 both hold (pure add to an unmatched leg)'
       END AS cell,
       count(*) AS fills,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY ds))::numeric, 2) AS pct_of_fills,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(sh * px)::numeric, 0) AS dollars,
       round(sum(net_drop)::numeric, 0) AS shares_the_net_model_would_SELL,
       round(sum(net_drop * px)::numeric, 0) AS dollars_the_net_model_would_SELL
  FROM c GROUP BY 1, 2 ORDER BY 1, 2;
