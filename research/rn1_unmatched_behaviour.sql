-- ============================================================================
-- WHAT DOES RN1 DO WHEN THE PAIR IS NOT OBTAINED?  (2026-09-10, read-only.)
--
-- THE QUESTION. Our two-sided shadow says 23.5% of quotes get both legs and
-- 61% get exactly one. A one-legged quote leaves directional inventory, which
-- is the thing that has been losing the money. RN1 runs the same book and must
-- hit the same problem far more often than he completes -- so the question is
-- not whether he has unmatched legs, it is WHAT HE DOES WITH THEM.
--
-- There are only three things anyone can do holding a leg whose partner never
-- came:
--
--   (A) COMPLETE  -- buy the missing leg anyway, paying up if you must. The
--                    pair still clears if the two prices sum under 1.00, so
--                    this is profitable exactly while he has room to pay.
--   (B) UNWIND    -- sell back what he holds. Costs the spread; caps the loss.
--   (C) CARRY     -- hold the naked leg to settlement. Free to do, and it is
--                    a directional bet whether he meant one or not.
--
-- His fills tell us which, and at what price, WITHOUT any interpretation: a
-- BUY of the leg he is short of is (A); a SELL of the leg he is long of is
-- (B); a market that ends with a residual he never traded out of is (C).
--
-- METHOD. `trades` carries `outcome_index` per fill, so a market's two legs
-- are index 0 and index 1 and no token mapping is needed. Running per-leg
-- positions come from window functions over (condition_id ORDER BY ts, id) --
-- ONE pass, no correlated subquery (the band pilot timed out on those and had
-- to be rewritten; this is written that way from the start).
--
-- IMBALANCE is n0 - n1 in shares. |imbalance| shrinking is a step toward a
-- matched book; growing is a step away. The 0.5-share deadband keeps rounding
-- dust from being read as an action.
--
-- WHAT WOULD CHANGE OUR BUILD.
--   * If (A) dominates and his completing pair cost stays under 1.00, the rule
--     to build is "keep quoting the second leg, and pay up to the basis
--     ceiling" -- which is exactly the owner's standing-order rule, and it
--     would mean his edge survives one-legged fills rather than being killed
--     by them.
--   * If (B) dominates, he is stopping out, and we should measure what that
--     costs him before copying it.
--   * If (C) dominates, the naked leg is not an accident he manages, it is a
--     directional book he runs beside the pairs -- and then a market-making
--     copy of him is only half of him.
--
-- Read-only: five SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. HOW TWO-SIDED IS HE, REALLY? every market of his, 14 days =='
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.side,
         t.size::float8 AS sh, t.price::float8 AS px, t.notional::float8 AS usd,
         t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.ts >= now() - interval '14 days'
     AND t.outcome_index IN (0, 1)
     AND t.condition_id IS NOT NULL
), m AS (
  SELECT condition_id, min(sport) AS sport, count(*) AS fills, sum(usd) AS gross_usd,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0)
           - COALESCE(sum(sh) FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS n0,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0)
           - COALESCE(sum(sh) FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS n1,
         count(*) FILTER (WHERE outcome_index = 0 AND side = 'BUY') AS buys0,
         count(*) FILTER (WHERE outcome_index = 1 AND side = 'BUY') AS buys1,
         count(*) FILTER (WHERE side = 'SELL') AS sells,
         extract(epoch FROM (max(ts) - min(ts))) AS life_s
    FROM f GROUP BY condition_id
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS markets,
       count(*) FILTER (WHERE buys0 > 0 AND buys1 > 0) AS bought_both_legs,
       round((100.0 * count(*) FILTER (WHERE buys0 > 0 AND buys1 > 0)
              / NULLIF(count(*), 0))::numeric, 1) AS two_sided_pct,
       round(sum(gross_usd)::numeric, 0) AS gross_usd,
       round(sum(gross_usd) FILTER (WHERE buys0 > 0 AND buys1 > 0)::numeric, 0) AS gross_two_sided,
       round(sum(LEAST(GREATEST(n0, 0), GREATEST(n1, 0)))::numeric, 0) AS matched_sh,
       round(sum(abs(n0 - n1))::numeric, 0) AS residual_sh,
       round((100.0 * sum(LEAST(GREATEST(n0, 0), GREATEST(n1, 0)))
              / NULLIF(sum(LEAST(GREATEST(n0, 0), GREATEST(n1, 0))) + sum(abs(n0 - n1)), 0))::numeric, 1)
         AS matched_share_pct,
       count(*) FILTER (WHERE sells > 0) AS markets_he_sold_in,
       round(avg(life_s)::numeric, 0) AS avg_life_s
  FROM m
 GROUP BY ROLLUP (sport)
 ORDER BY sum(gross_usd) DESC NULLS LAST
 LIMIT 30;


\echo '== 2. THE ANSWER: every fill classified by what it did to the gap =='
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.side,
         t.size::float8 AS sh, t.price::float8 AS px, t.notional::float8 AS usd,
         t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.ts >= now() - interval '14 days'
     AND t.outcome_index IN (0, 1)
     AND t.condition_id IS NOT NULL
), r AS (
  SELECT f.*,
         sum(CASE WHEN outcome_index = 0 THEN (CASE WHEN side = 'BUY' THEN sh ELSE -sh END) ELSE 0 END)
           OVER w AS n0,
         sum(CASE WHEN outcome_index = 1 THEN (CASE WHEN side = 'BUY' THEN sh ELSE -sh END) ELSE 0 END)
           OVER w AS n1
    FROM f
  WINDOW w AS (PARTITION BY condition_id ORDER BY ts, id
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), d AS (
  SELECT r.*, (n0 - n1) AS imb,
         lag(n0 - n1) OVER (PARTITION BY condition_id ORDER BY ts, id) AS prev_imb,
         extract(epoch FROM (ts - lag(ts) OVER (PARTITION BY condition_id ORDER BY ts, id))) AS gap_s
    FROM r
)
SELECT CASE
         WHEN prev_imb IS NULL                       THEN '0 the opening fill'
         WHEN abs(imb) > abs(prev_imb) + 0.5         THEN '1 WIDENS the gap'
         WHEN abs(imb) < abs(prev_imb) - 0.5
              AND side = 'BUY'                       THEN '2 CLOSES it by BUYING the missing leg'
         WHEN abs(imb) < abs(prev_imb) - 0.5
              AND side = 'SELL'                      THEN '3 CLOSES it by SELLING what he holds'
         WHEN side = 'SELL'                          THEN '4 a SELL that leaves the gap alone'
         ELSE                                             '5 a BUY that leaves the gap alone'
       END AS what_the_fill_did,
       count(*) AS fills,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 1) AS pct_of_fills,
       round(sum(usd)::numeric, 0) AS usd,
       round((100.0 * sum(usd) / sum(sum(usd)) OVER ())::numeric, 1) AS pct_of_usd,
       round(avg(px)::numeric, 4) AS avg_px,
       round(avg(sh)::numeric, 0) AS avg_shares,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_s))::numeric, 0) AS median_s_since_prev,
       round(avg(abs(prev_imb))::numeric, 0) AS avg_gap_before,
       round(avg(abs(imb))::numeric, 0) AS avg_gap_after
  FROM d
 GROUP BY 1 ORDER BY 1;


\echo '== 3. WHEN HE COMPLETES A PAIR, WHAT DOES THE PAIR COST HIM? =='
-- The completing BUY's price plus the running vwap of the leg he already held.
-- Under 1.00 the pair is profitable however long it took; over 1.00 he has
-- bought a guaranteed loss, and how often that happens is the whole question
-- of whether "keep quoting the second leg" is safe to copy.
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.side,
         t.size::float8 AS sh, t.price::float8 AS px, t.notional::float8 AS usd,
         t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.ts >= now() - interval '14 days'
     AND t.outcome_index IN (0, 1)
     AND t.condition_id IS NOT NULL
), r AS (
  SELECT f.*,
         sum(CASE WHEN outcome_index = 0 THEN (CASE WHEN side = 'BUY' THEN sh ELSE -sh END) ELSE 0 END)
           OVER w AS n0,
         sum(CASE WHEN outcome_index = 1 THEN (CASE WHEN side = 'BUY' THEN sh ELSE -sh END) ELSE 0 END)
           OVER w AS n1,
         -- running BUY cost and BUY shares per leg, EXCLUDING this row, so the
         -- vwap compared against is the book he held when he chose to complete
         sum(CASE WHEN outcome_index = 0 AND side = 'BUY' THEN usd ELSE 0 END) OVER wp AS c0,
         sum(CASE WHEN outcome_index = 0 AND side = 'BUY' THEN sh  ELSE 0 END) OVER wp AS q0,
         sum(CASE WHEN outcome_index = 1 AND side = 'BUY' THEN usd ELSE 0 END) OVER wp AS c1,
         sum(CASE WHEN outcome_index = 1 AND side = 'BUY' THEN sh  ELSE 0 END) OVER wp AS q1
    FROM f
  WINDOW w  AS (PARTITION BY condition_id ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),
         wp AS (PARTITION BY condition_id ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
), d AS (
  SELECT r.*, (n0 - n1) AS imb,
         lag(n0 - n1) OVER (PARTITION BY condition_id ORDER BY ts, id) AS prev_imb,
         CASE WHEN outcome_index = 1 AND q0 > 0 THEN c0 / q0
              WHEN outcome_index = 0 AND q1 > 0 THEN c1 / q1 END AS held_vwap
    FROM r
), completing AS (
  SELECT sport, px, sh, usd, held_vwap, (px + held_vwap) AS pair_cost,
         abs(prev_imb) AS gap_before
    FROM d
   WHERE prev_imb IS NOT NULL AND side = 'BUY'
     AND abs(imb) < abs(prev_imb) - 0.5
     AND held_vwap IS NOT NULL
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS completing_buys,
       round(sum(usd)::numeric, 0) AS usd,
       round(avg(held_vwap)::numeric, 4) AS avg_leg_he_held,
       round(avg(px)::numeric, 4) AS avg_px_he_paid,
       round(avg(pair_cost)::numeric, 4) AS avg_pair_cost,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost))::numeric, 4) AS median_pair_cost,
       count(*) FILTER (WHERE pair_cost < 1.0) AS pair_under_1,
       round((100.0 * count(*) FILTER (WHERE pair_cost < 1.0) / NULLIF(count(*), 0))::numeric, 1)
         AS pct_under_1,
       round(sum(sh * (1.0 - pair_cost))::numeric, 0) AS profit_if_all_held_to_settlement,
       round(avg(gap_before)::numeric, 0) AS avg_gap_he_was_closing
  FROM completing
 GROUP BY ROLLUP (sport)
 ORDER BY count(*) DESC
 LIMIT 25;


\echo '== 4. WHEN HE SELLS OUT INSTEAD, WHAT DID IT COST HIM? =='
-- The unwinding SELL's price against the running vwap of the SAME leg. A sale
-- below his own basis is a realised loss he chose to take; how big, and how
-- fast, is the shape of his stop -- if he has one at all.
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.side,
         t.size::float8 AS sh, t.price::float8 AS px, t.notional::float8 AS usd,
         t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.ts >= now() - interval '14 days'
     AND t.outcome_index IN (0, 1)
     AND t.condition_id IS NOT NULL
), r AS (
  SELECT f.*,
         sum(CASE WHEN side = 'BUY' AND outcome_index = f.outcome_index THEN usd ELSE 0 END)
           OVER wp AS same_cost,
         sum(CASE WHEN side = 'BUY' AND outcome_index = f.outcome_index THEN sh ELSE 0 END)
           OVER wp AS same_qty,
         min(ts) OVER (PARTITION BY condition_id, outcome_index) AS leg_first_ts
    FROM f
  WINDOW wp AS (PARTITION BY condition_id, outcome_index ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
), s AS (
  SELECT sport, px, sh, usd, ts, leg_first_ts,
         CASE WHEN same_qty > 0 THEN same_cost / same_qty END AS basis,
         extract(epoch FROM (ts - leg_first_ts)) AS held_s
    FROM r
   WHERE side = 'SELL'
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS sells,
       round(sum(usd)::numeric, 0) AS usd,
       round(avg(basis)::numeric, 4) AS avg_basis,
       round(avg(px)::numeric, 4) AS avg_sell_px,
       round(avg(px - basis)::numeric, 4) AS avg_px_vs_basis,
       count(*) FILTER (WHERE px < basis) AS sold_below_basis,
       round((100.0 * count(*) FILTER (WHERE px < basis) / NULLIF(count(*) FILTER (WHERE basis IS NOT NULL), 0))::numeric, 1)
         AS pct_below_basis,
       round(sum(sh * (px - basis))::numeric, 0) AS realised_on_sells_usd,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY px - basis))::numeric, 4) AS median_px_vs_basis,
       round((percentile_cont(0.1) WITHIN GROUP (ORDER BY px - basis))::numeric, 4) AS p10_px_vs_basis,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY held_s))::numeric, 0) AS median_held_s
  FROM s
 WHERE basis IS NOT NULL
 GROUP BY ROLLUP (sport)
 ORDER BY count(*) DESC
 LIMIT 25;


\echo '== 5. THE MARKETS HE NEVER MATCHED: what happened to the naked leg? =='
-- Per market, the residual he ended with, its basis, and -- where the market
-- has settled -- what it actually paid. This is the only line that says
-- whether carrying an unmatched leg is a cost or a second business.
WITH f AS (
  SELECT t.id, t.condition_id, t.outcome_index, t.side,
         t.size::float8 AS sh, t.price::float8 AS px, t.notional::float8 AS usd,
         t.ts, t.sport
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
     AND t.ts >= now() - interval '21 days'
     AND t.outcome_index IN (0, 1)
     AND t.condition_id IS NOT NULL
), m AS (
  SELECT condition_id, min(sport) AS sport,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0)
           - COALESCE(sum(sh) FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS n0,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0)
           - COALESCE(sum(sh) FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS n1,
         CASE WHEN COALESCE(sum(sh) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) > 0
              THEN sum(usd) FILTER (WHERE outcome_index = 0 AND side = 'BUY')
                   / sum(sh) FILTER (WHERE outcome_index = 0 AND side = 'BUY') END AS vwap0,
         CASE WHEN COALESCE(sum(sh) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) > 0
              THEN sum(usd) FILTER (WHERE outcome_index = 1 AND side = 'BUY')
                   / sum(sh) FILTER (WHERE outcome_index = 1 AND side = 'BUY') END AS vwap1,
         sum(usd) AS gross_usd, max(ts) AS last_ts
    FROM f GROUP BY condition_id
), j AS (
  SELECT m.*,
         (m.n0 - m.n1) AS resid,
         CASE WHEN m.n0 - m.n1 > 0 THEN 0 ELSE 1 END AS resid_leg,
         CASE WHEN m.n0 - m.n1 > 0 THEN m.vwap0 ELSE m.vwap1 END AS resid_vwap,
         mk.resolved,
         CASE WHEN mk.resolved AND jsonb_typeof(mk.resolved_prices) = 'array'
              THEN (mk.resolved_prices ->> (CASE WHEN m.n0 - m.n1 > 0 THEN 0 ELSE 1 END))::float8
         END AS resid_payout
    FROM m LEFT JOIN markets mk ON mk.condition_id = m.condition_id
)
SELECT COALESCE(sport, 'ALL') AS sport,
       count(*) AS markets,
       count(*) FILTER (WHERE abs(resid) < 1) AS ended_matched,
       round((100.0 * count(*) FILTER (WHERE abs(resid) < 1) / NULLIF(count(*), 0))::numeric, 1)
         AS ended_matched_pct,
       round(sum(abs(resid))::numeric, 0) AS naked_shares,
       round(avg(resid_vwap)::numeric, 4) AS avg_naked_basis,
       count(*) FILTER (WHERE resid_payout IS NOT NULL) AS settled,
       round(avg(resid_payout)::numeric, 4) AS naked_hit_rate,
       round(sum(abs(resid) * (resid_payout - resid_vwap))
             FILTER (WHERE resid_payout IS NOT NULL AND resid_vwap IS NOT NULL)::numeric, 0)
         AS naked_leg_pnl_usd,
       round((100.0 * sum(abs(resid) * (resid_payout - resid_vwap))
                     FILTER (WHERE resid_payout IS NOT NULL AND resid_vwap IS NOT NULL)
              / NULLIF(sum(abs(resid) * resid_vwap)
                       FILTER (WHERE resid_payout IS NOT NULL AND resid_vwap IS NOT NULL), 0))::numeric, 2)
         AS naked_leg_roi_pct
  FROM j
 WHERE abs(resid) >= 1
 GROUP BY ROLLUP (sport)
 ORDER BY count(*) DESC
 LIMIT 25;
