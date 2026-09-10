-- ============================================================================
-- FORENSIC BLOCK 1: sections 5 and 6 of the owner's brief (2026-09-10).
--
-- Answered from RN1's own fills and the settlement record ONLY. No order
-- book, no latency, no fill assumption, nothing about our execution. That is
-- deliberate: these two questions gate the architecture, and they are the
-- only parts of the brief answerable over full history without inventing
-- data we do not have.
--
-- ---------------------------------------------------------------------------
-- SECTION 6, RESTATED TO THE OWNER'S CORRECTION.
--
-- The proposed completion trigger was "RN1 bought the other token". That is
-- WRONG and this file is built to prove or disprove the corrected form.
-- Inventory is FUNGIBLE; there is no fact of the matter about which YES share
-- he "meant" to pair with which NO share. What is observable is
--
--     M_t  = min(Y_t, N_t)          Y, N = cumulative shares bought per leg
--     dM_t = max(M_t - M_(t-1), 0)  newly matched inventory, and nothing else
--
-- His 10,000 YES / 4,000 NO book, buying 2,000 NO, newly matches exactly
-- 2,000 -- not the clip, not the position. Every fill is then classified:
--
--   1 adds only unmatched inventory          dM = 0
--   2 increases matched inventory            dM = fill size (fully absorbed)
--   3 increases matched AND leaves residual  0 < dM < fill size
--   4 no economic inventory change           dM = 0 and no leg moved
--
-- and for classes 2 and 3 the distribution of dM / fill_size says whether his
-- opposite-leg fills complete the whole clip or only part of it. That ratio
-- IS our completion sizing; if it is usually well under 1.0, sizing a
-- completion off the incoming fill over-completes systematically.
--
-- ---------------------------------------------------------------------------
-- SECTION 5, AND AN ALGEBRAIC RESULT THAT NARROWS THE SEARCH.
--
-- Canonical (the forensic report's) decomposition, which reconciles by
-- construction so Total = Matched + Directional:
--
--     M          = min(Y, N)
--     pair_cost  = vwapY + vwapN
--     matched    = M x (1 - pair_cost)
--     directional= Total - matched
--
-- Alternate (mine): directional = |Y-N| x (payout_of_surplus - vwap_surplus).
--
-- Work the difference out with Y > N, residual on leg 0:
--
--     canonical  = (Y-N)(p0-v0) + M(p0 + p1 - 1)
--     alternate  = (Y-N)(p0-v0)
--     difference = M x (p0 + p1 - 1)
--
-- ON A TRUE TWO-OUTCOME MARKET p0 + p1 = 1, SO THE DIFFERENCE IS EXACTLY
-- ZERO AND BOTH DEFINITIONS MUST AGREE. So "different methodology" does not
-- explain a sign flip, and the real cause is elsewhere. The leading suspect
-- is MARKET CARDINALITY: filtering outcome_index IN (0,1) on a three-way
-- soccer market (home/draw/away) treats two of three outcomes as a
-- complementary pair, p0 + p1 != 1, min(Y,N) is not a matched pair at all,
-- and soccer is his largest book by market count. Statement 1 tests that
-- FIRST, and every later statement is split by cardinality so the artifact
-- cannot hide inside an aggregate.
--
-- WINDOWS ARE CUT BY MARKET, NEVER BY FILL: a market is in a window if its
-- LAST fill is, and then ALL of its fills are read. Cutting by fill would
-- truncate markets that opened earlier, and truncation bias grows as the
-- window shrinks -- manufacturing exactly the horizon pattern being tested.
--
-- Only SETTLED markets carry a result. Unsettled are counted, never defaulted.
--
-- Read-only: eight SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. CONTROL FIRST: how many outcomes does each of his markets have? =='
WITH f AS (
  SELECT t.condition_id, t.outcome_index, t.notional::float8 AS usd, t.sport, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IS NOT NULL
), c AS (
  SELECT condition_id, min(sport) AS sport, count(DISTINCT outcome_index) AS legs,
         max(outcome_index) AS max_idx, sum(usd) AS usd, count(*) AS fills
    FROM f GROUP BY condition_id
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS markets,
       count(*) FILTER (WHERE legs = 1) AS one_leg_traded,
       count(*) FILTER (WHERE legs = 2) AS two_legs_traded,
       count(*) FILTER (WHERE max_idx >= 2) AS HAS_A_THIRD_OUTCOME,
       round((100.0 * count(*) FILTER (WHERE max_idx >= 2) / NULLIF(count(*), 0))::numeric, 1)
         AS third_outcome_pct,
       round(sum(usd)::numeric, 0) AS usd,
       round(sum(usd) FILTER (WHERE max_idx >= 2)::numeric, 0) AS usd_three_way
  FROM c GROUP BY ROLLUP (sport) ORDER BY sum(usd) DESC NULLS LAST LIMIT 20;


\echo '== 1b. AND WHAT DO THE SETTLEMENTS SAY? does p0+p1 sum to 1? =='
-- The direct test of the algebra above. Where the two prices we treat as a
-- pair do not sum to 1.00, min(Y,N) was never a matched pair and every
-- "matched P&L" figure computed on it is wrong.
SELECT jsonb_array_length(m.resolved_prices) AS outcomes_in_settlement,
       count(*) AS markets,
       round(avg((m.resolved_prices ->> 0)::float8
                 + (m.resolved_prices ->> 1)::float8)::numeric, 4) AS avg_p0_plus_p1,
       count(*) FILTER (WHERE abs((m.resolved_prices ->> 0)::float8
                                  + (m.resolved_prices ->> 1)::float8 - 1.0) < 0.001) AS sums_to_one,
       round((100.0 * count(*) FILTER (WHERE abs((m.resolved_prices ->> 0)::float8
                                  + (m.resolved_prices ->> 1)::float8 - 1.0) < 0.001)
              / NULLIF(count(*), 0))::numeric, 1) AS pct_sums_to_one
  FROM markets m
 WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
   AND EXISTS (SELECT 1 FROM trades t JOIN whales w ON w.id = t.whale_id
                WHERE t.condition_id = m.condition_id AND lower(w.username) = 'rn1')
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;


\echo '== 2. SECTION 6 PRIMARY: every fill classified by INCREMENTAL MATCHED =='
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), r AS (
  SELECT f.*,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER wc AS y_now,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER wc AS n_now,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER wp AS y_prev,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER wp AS n_prev
    FROM f
  WINDOW wc AS (PARTITION BY condition_id ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
         wp AS (PARTITION BY condition_id ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
), d AS (
  SELECT r.*,
         GREATEST(LEAST(y_now, n_now) - LEAST(COALESCE(y_prev, 0), COALESCE(n_prev, 0)), 0) AS dm
    FROM r
)
SELECT CASE WHEN sh <= 0                     THEN '4 no economic change'
            WHEN dm <= 0                     THEN '1 adds only unmatched'
            WHEN dm >= sh - 0.000001         THEN '2 fully matched by this fill'
            ELSE                                  '3 partly matched, rest is new residual'
       END AS fill_class,
       count(*) AS fills,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_fills,
       round(sum(usd)::numeric, 0) AS usd,
       round((100.0 * sum(usd) / sum(sum(usd)) OVER ())::numeric, 2) AS pct_usd,
       round(sum(sh)::numeric, 0) AS shares,
       round(sum(dm)::numeric, 0) AS newly_matched_sh,
       round(avg(sh)::numeric, 0) AS avg_clip_sh,
       round(avg(CASE WHEN sh > 0 THEN dm / sh END)::numeric, 4) AS avg_dm_over_fill,
       round((percentile_cont(0.5) WITHIN GROUP (
                ORDER BY CASE WHEN sh > 0 THEN dm / sh END))::numeric, 4) AS med_dm_over_fill
  FROM d GROUP BY 1 ORDER BY 1;


\echo '== 3. FOR CLASSES 2 AND 3: how much of the incoming clip actually matches? =='
-- This ratio IS our completion sizing. If it clusters near 1.0 the incoming
-- fill is a fair proxy; if it is broadly spread, sizing a completion off the
-- fill over-completes and only dM is defensible.
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), r AS (
  SELECT f.*,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER wc AS y_now,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER wc AS n_now,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END) OVER wp AS y_prev,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END) OVER wp AS n_prev
    FROM f
  WINDOW wc AS (PARTITION BY condition_id ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
         wp AS (PARTITION BY condition_id ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
), d AS (
  SELECT r.*, GREATEST(LEAST(y_now, n_now)
              - LEAST(COALESCE(y_prev, 0), COALESCE(n_prev, 0)), 0) AS dm
    FROM r
)
SELECT COALESCE(sport, 'ALL') AS sport,
       CASE WHEN dm / sh < 0.10 THEN '1 under 10% of the clip'
            WHEN dm / sh < 0.50 THEN '2 10-50%'
            WHEN dm / sh < 0.90 THEN '3 50-90%'
            WHEN dm / sh < 0.999 THEN '4 90-100%'
            ELSE                      '5 the whole clip' END AS dm_over_fill,
       count(*) AS fills,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY sport))::numeric, 1) AS pct_of_sport,
       round(sum(usd)::numeric, 0) AS usd,
       round(sum(dm)::numeric, 0) AS newly_matched_sh,
       round(avg(sh)::numeric, 0) AS avg_clip
  FROM d WHERE dm > 0 AND sh > 0
 GROUP BY ROLLUP (sport), 2 ORDER BY sport NULLS FIRST, 2 LIMIT 60;


\echo '== 4. POOL OR SEQUENCE? do his two legs trade at the same time? =='
WITH f AS (
  SELECT t.condition_id, t.outcome_index, t.notional::float8 AS usd, t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), m AS (
  SELECT condition_id, min(sport) AS sport,
         min(ts) FILTER (WHERE outcome_index = 0) AS a0,
         max(ts) FILTER (WHERE outcome_index = 0) AS z0,
         min(ts) FILTER (WHERE outcome_index = 1) AS a1,
         max(ts) FILTER (WHERE outcome_index = 1) AS z1,
         count(*) AS fills, sum(usd) AS usd
    FROM f GROUP BY condition_id
), o AS (
  SELECT m.*,
         GREATEST(0, extract(epoch FROM (LEAST(z0, z1) - GREATEST(a0, a1)))) AS overlap_s,
         NULLIF(extract(epoch FROM (GREATEST(z0, z1) - LEAST(a0, a1))), 0) AS span_s
    FROM m WHERE a0 IS NOT NULL AND a1 IS NOT NULL
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS two_leg_markets,
       count(*) FILTER (WHERE overlap_s <= 0) AS sequential,
       count(*) FILTER (WHERE overlap_s > 0) AS interleaved,
       round((100.0 * count(*) FILTER (WHERE overlap_s > 0) / NULLIF(count(*), 0))::numeric, 1)
         AS interleaved_pct,
       round(avg(100.0 * overlap_s / span_s)::numeric, 1) AS avg_overlap_pct_of_span,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY 100.0 * overlap_s / span_s))::numeric, 1)
         AS med_overlap_pct,
       round(avg(fills)::numeric, 0) AS avg_fills, round(sum(usd)::numeric, 0) AS usd
  FROM o GROUP BY ROLLUP (sport) ORDER BY sum(usd) DESC NULLS LAST LIMIT 20;


\echo '== 5. SECTION 5: CANONICAL vs ALTERNATE BY HORIZON, SPLIT BY CARDINALITY =='
WITH horizons(label, days) AS (
  VALUES ('1 48 hours', 2), ('2 7 days', 7), ('3 14 days', 14), ('4 21 days', 21),
         ('5 30 days', 30), ('6 90 days', 90), ('7 lifetime', 100000)
), f AS (
  SELECT t.condition_id, t.outcome_index, t.side, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IS NOT NULL
), mk AS (
  SELECT condition_id, max(ts) AS last_ts, max(outcome_index) AS max_idx,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS b0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS b1,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS x0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS x1,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS c0,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS c1,
         COALESCE(sum(usd) FILTER (WHERE side = 'SELL'), 0) AS proceeds
    FROM f GROUP BY condition_id
), e AS (
  SELECT h.label,
         CASE WHEN mk.max_idx >= 2 THEN 'B three-way+' ELSE 'A two-way' END AS card,
         (mk.b0 - mk.x0) AS y, (mk.b1 - mk.x1) AS n,
         (mk.c0 + mk.c1 - mk.proceeds) AS net_cost,
         COALESCE(CASE WHEN mk.b0 > 0 THEN mk.c0 / mk.b0 END, 0) AS v0,
         COALESCE(CASE WHEN mk.b1 > 0 THEN mk.c1 / mk.b1 END, 0) AS v1,
         (m.resolved_prices ->> 0)::float8 AS p0,
         (m.resolved_prices ->> 1)::float8 AS p1
    FROM mk CROSS JOIN horizons h
    JOIN markets m ON m.condition_id = mk.condition_id
   WHERE mk.last_ts >= now() - (h.days * interval '1 day')
     AND m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
), g AS (
  SELECT label, card, y, n, net_cost, p0, p1,
         LEAST(GREATEST(y, 0), GREATEST(n, 0)) AS matched,
         (y - n) AS resid, (v0 + v1) AS pair_cost,
         CASE WHEN y - n > 0 THEN v0 ELSE v1 END AS resid_vwap,
         CASE WHEN y - n > 0 THEN p0 ELSE p1 END AS resid_payout,
         (y * p0 + n * p1 - (net_cost)) AS total_pnl
    FROM e
)
SELECT label AS horizon, card, count(*) AS markets,
       round(sum(net_cost)::numeric, 0) AS total_cost,
       round(sum(total_pnl)::numeric, 0) AS total_pnl,
       round(sum(matched)::numeric, 0) AS matched_qty,
       round(sum(matched * pair_cost)::numeric, 0) AS matched_cost,
       round(sum(matched * (1.0 - pair_cost))::numeric, 0) AS matched_pnl,
       round((100.0 * sum(matched * (1.0 - pair_cost))
              / NULLIF(sum(matched * pair_cost), 0))::numeric, 2) AS matched_roi_pct,
       round(sum(abs(resid))::numeric, 0) AS unmatched_sh,
       round(sum(net_cost - matched * pair_cost)::numeric, 0) AS directional_cost,
       round(sum(total_pnl - matched * (1.0 - pair_cost))::numeric, 0) AS DIRECTIONAL_PNL_CANON,
       round((100.0 * sum(total_pnl - matched * (1.0 - pair_cost))
              / NULLIF(sum(net_cost - matched * pair_cost), 0))::numeric, 2) AS directional_roi_canon,
       round(sum(abs(resid) * (resid_payout - resid_vwap))::numeric, 0) AS directional_pnl_alt,
       round((100.0 * sum(matched * pair_cost) / NULLIF(sum(net_cost), 0))::numeric, 1) AS matched_pct_of_cost
  FROM g GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. CANONICAL DIRECTIONAL RESIDUAL BY MONTH -- is September negative? =='
WITH f AS (
  SELECT t.condition_id, t.outcome_index, t.side, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IS NOT NULL
), mk AS (
  SELECT condition_id, max(ts) AS last_ts, max(outcome_index) AS max_idx,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS b0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS b1,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS x0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS x1,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS c0,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS c1,
         COALESCE(sum(usd) FILTER (WHERE side = 'SELL'), 0) AS proceeds
    FROM f GROUP BY condition_id
), g AS (
  SELECT to_char(date_trunc('month', mk.last_ts), 'YYYY-MM') AS month,
         CASE WHEN mk.max_idx >= 2 THEN 'B three-way+' ELSE 'A two-way' END AS card,
         (mk.b0 - mk.x0) AS y, (mk.b1 - mk.x1) AS n,
         (mk.c0 + mk.c1 - mk.proceeds) AS net_cost,
         LEAST(GREATEST(mk.b0 - mk.x0, 0), GREATEST(mk.b1 - mk.x1, 0)) AS matched,
         (COALESCE(CASE WHEN mk.b0 > 0 THEN mk.c0 / mk.b0 END, 0)
          + COALESCE(CASE WHEN mk.b1 > 0 THEN mk.c1 / mk.b1 END, 0)) AS pair_cost,
         ((mk.b0 - mk.x0) * (m.resolved_prices ->> 0)::float8
          + (mk.b1 - mk.x1) * (m.resolved_prices ->> 1)::float8
          - (mk.c0 + mk.c1 - mk.proceeds)) AS total_pnl
    FROM mk JOIN markets m ON m.condition_id = mk.condition_id
   WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
)
SELECT month, card, count(*) AS markets,
       round(sum(net_cost)::numeric, 0) AS total_cost,
       round(sum(total_pnl)::numeric, 0) AS total_pnl,
       round(sum(matched * (1.0 - pair_cost))::numeric, 0) AS matched_pnl,
       round(sum(total_pnl - matched * (1.0 - pair_cost))::numeric, 0) AS directional_pnl,
       round((100.0 * sum(total_pnl - matched * (1.0 - pair_cost))
              / NULLIF(sum(net_cost - matched * pair_cost), 0))::numeric, 2) AS directional_roi_pct,
       round((100.0 * sum(total_pnl) / NULLIF(sum(net_cost), 0))::numeric, 2) AS total_roi_pct
  FROM g GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 7. THE DIRECTIONAL RESIDUAL BY SPORT, 30 DAYS -- what hides in the total? =='
WITH f AS (
  SELECT t.condition_id, t.outcome_index, t.side, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts, t.sport, t.market_slug
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IS NOT NULL
), mk AS (
  SELECT condition_id, min(sport) AS sport, min(market_slug) AS slug,
         max(ts) AS last_ts, max(outcome_index) AS max_idx,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS b0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS b1,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS x0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS x1,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS c0,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS c1,
         COALESCE(sum(usd) FILTER (WHERE side = 'SELL'), 0) AS proceeds
    FROM f GROUP BY condition_id
), g AS (
  SELECT COALESCE(mk.sport, 'unclassified') AS sport,
         CASE WHEN mk.slug ~ '-(o|u)\d' OR mk.slug ~ 'total' THEN '2 total'
              WHEN mk.slug ~ '(pos|neg)-?\d' OR mk.slug ~ 'spread|handicap' THEN '3 spread'
              WHEN mk.max_idx >= 2 THEN '4 three-way'
              ELSE '1 moneyline' END AS mkt_type,
         (mk.b0 - mk.x0) AS y, (mk.b1 - mk.x1) AS n,
         (mk.c0 + mk.c1 - mk.proceeds) AS net_cost,
         LEAST(GREATEST(mk.b0 - mk.x0, 0), GREATEST(mk.b1 - mk.x1, 0)) AS matched,
         (COALESCE(CASE WHEN mk.b0 > 0 THEN mk.c0 / mk.b0 END, 0)
          + COALESCE(CASE WHEN mk.b1 > 0 THEN mk.c1 / mk.b1 END, 0)) AS pair_cost,
         ((mk.b0 - mk.x0) * (m.resolved_prices ->> 0)::float8
          + (mk.b1 - mk.x1) * (m.resolved_prices ->> 1)::float8
          - (mk.c0 + mk.c1 - mk.proceeds)) AS total_pnl
    FROM mk JOIN markets m ON m.condition_id = mk.condition_id
   WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
     AND mk.last_ts >= now() - interval '30 days'
)
SELECT sport, mkt_type, count(*) AS markets,
       round(sum(net_cost)::numeric, 0) AS total_cost,
       round(sum(total_pnl)::numeric, 0) AS total_pnl,
       round(sum(matched * (1.0 - pair_cost))::numeric, 0) AS matched_pnl,
       round(sum(total_pnl - matched * (1.0 - pair_cost))::numeric, 0) AS directional_pnl,
       round((100.0 * sum(total_pnl - matched * (1.0 - pair_cost))
              / NULLIF(sum(net_cost - matched * pair_cost), 0))::numeric, 2) AS directional_roi_pct,
       round(sum(abs(y - n))::numeric, 0) AS unmatched_sh
  FROM g GROUP BY ROLLUP (sport, mkt_type)
 ORDER BY sum(net_cost) DESC NULLS LAST LIMIT 60;


\echo '== 8. THE A-MINUS-B RECONCILIATION, in dollars, by cardinality =='
-- The algebra says canonical - alternate = matched x (p0 + p1 - 1), which is
-- ZERO on a true two-way market. This prints the two figures and their gap
-- beside that predicted term, so the reconciliation is arithmetic rather than
-- assertion. A non-zero gap on the two-way rows would mean the derivation is
-- wrong and I need to say so.
WITH f AS (
  SELECT t.condition_id, t.outcome_index, t.side, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IS NOT NULL AND t.ts >= now() - interval '90 days'
), mk AS (
  SELECT condition_id, max(outcome_index) AS max_idx,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS b0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS b1,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS x0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS x1,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 0 AND side = 'BUY'),  0) AS c0,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 1 AND side = 'BUY'),  0) AS c1,
         COALESCE(sum(usd) FILTER (WHERE side = 'SELL'), 0) AS proceeds
    FROM f GROUP BY condition_id
), g AS (
  SELECT CASE WHEN mk.max_idx >= 2 THEN 'B three-way+' ELSE 'A two-way' END AS card,
         (mk.b0 - mk.x0) AS y, (mk.b1 - mk.x1) AS n,
         (mk.c0 + mk.c1 - mk.proceeds) AS net_cost,
         LEAST(GREATEST(mk.b0 - mk.x0, 0), GREATEST(mk.b1 - mk.x1, 0)) AS matched,
         (COALESCE(CASE WHEN mk.b0 > 0 THEN mk.c0 / mk.b0 END, 0)
          + COALESCE(CASE WHEN mk.b1 > 0 THEN mk.c1 / mk.b1 END, 0)) AS pair_cost,
         CASE WHEN (mk.b0 - mk.x0) - (mk.b1 - mk.x1) > 0
              THEN COALESCE(CASE WHEN mk.b0 > 0 THEN mk.c0 / mk.b0 END, 0)
              ELSE COALESCE(CASE WHEN mk.b1 > 0 THEN mk.c1 / mk.b1 END, 0) END AS resid_vwap,
         CASE WHEN (mk.b0 - mk.x0) - (mk.b1 - mk.x1) > 0
              THEN (m.resolved_prices ->> 0)::float8
              ELSE (m.resolved_prices ->> 1)::float8 END AS resid_payout,
         (m.resolved_prices ->> 0)::float8 AS p0,
         (m.resolved_prices ->> 1)::float8 AS p1
    FROM mk JOIN markets m ON m.condition_id = mk.condition_id
   WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
)
SELECT card, count(*) AS markets,
       round(avg(p0 + p1)::numeric, 4) AS avg_p0_plus_p1,
       round(sum(y * p0 + n * p1 - net_cost)::numeric, 0) AS total_pnl,
       round(sum(matched * (1.0 - pair_cost))::numeric, 0) AS matched_pnl,
       round(sum(y * p0 + n * p1 - net_cost - matched * (1.0 - pair_cost))::numeric, 0) AS canonical_dir,
       round(sum(abs(y - n) * (resid_payout - resid_vwap))::numeric, 0) AS alternate_dir,
       round(sum(y * p0 + n * p1 - net_cost - matched * (1.0 - pair_cost)
                 - abs(y - n) * (resid_payout - resid_vwap))::numeric, 0) AS gap_observed,
       round(sum(matched * (p0 + p1 - 1.0))::numeric, 0) AS gap_predicted_by_algebra
  FROM g GROUP BY 1 ORDER BY 1;
