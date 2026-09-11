-- ============================================================================
-- B2. DIRECTIONAL RESIDUAL ECONOMICS BUILT FROM INVENTORY, NOT FROM SUBTRACTION
-- (2026-09-11, read-only.) Owner-specified.
--
-- Owner: "do not rely solely on subtraction to earn the directional label."
-- So this file constructs the residual component from share counts and
-- settlement payouts, allocates acquisition cost under an explicit rule, and
-- reconciles the result against the subtraction remainder.
--
--   M           = min(qY, qN)        on canonical BUY quantities
--   residual_Y  = qY - M
--   residual_N  = qN - M
--   DIRECT_DIRECTIONAL_RESIDUAL_PNL
--               = residual settlement value - attributable residual acq cost
--               = (pY*rY + pN*rN) - allocated_residual_cost
--
-- ---------------------------------------------------------------------------
-- THE ALGEBRA IS STATED BEFORE THE MEASUREMENT, BECAUSE IT CHANGES WHAT THE
-- RECONCILIATION CAN AND CANNOT PROVE. Three identities, derived symbolically
-- and checked by machine before this file was written.
--
--   (i)  ON THE CLEAN COHORT, UNDER AVERAGE-COST ALLOCATION, THE
--        RECONCILIATION IS AN ALGEBRAIC IDENTITY, NOT AN EMPIRICAL TEST:
--
--          (TRADING_PNL - MATCHED_GROSS_PNL) - DIRECT_DIRECTIONAL_avg == 0
--
--        given qX = M + rX, cX = qX*vX, no sells, and pY + pN = 1. It follows
--        that a near-zero reconciliation residual MUST NOT be reported as
--        independent confirmation of the number. It is the same number reached
--        twice. What it DOES establish is stated in (iii).
--
--   (ii) ON A COHORT WITH SELLS, THE SAME DIFFERENCE IS NOT ZERO, AND IT IS
--        NOT A MYSTERY TERM. It equals exactly
--
--          sell_proceeds - (pY*sold_Y + pN*sold_N)
--
--        i.e. WHAT HE RECEIVED FOR THE SHARES HE SOLD, MINUS WHAT THOSE SHARES
--        WOULD HAVE PAID AT SETTLEMENT. That is the economic content of the
--        SELL contamination, expressed in dollars. Statement 4 computes the
--        difference and that expression by two separate code paths and prints
--        both, so agreement is an arithmetic cross-check rather than an
--        assertion.
--
--   (iii) THE DERIVATION USES pY + pN = 1. If a condition's payout vector sums
--        to q instead, the reconciliation residual is exactly M*(q - 1). So a
--        NON-ZERO clean-cohort residual localizes to payout-vector defects,
--        scaled by matched quantity. THAT is the independent content of the
--        clean-cohort reconciliation: it is a per-condition, dollar-weighted
--        test of the binary payoff invariant on the cohort actually used, and
--        a test that the two code paths agree. It is not evidence that the
--        remainder is economically directional; identity (i) is why.
--
--        What DOES earn the directional label is the conjunction: the clean
--        filter removes every representable contaminant, identity (ii) shows
--        the only term the filter removes is the sold-share term, and the
--        residual reconciles at the per-condition level rather than only in
--        aggregate. Statement 5 checks that last point, because a total near
--        zero can hide large offsetting per-condition errors.
--
-- ---------------------------------------------------------------------------
-- THE ALLOCATION RULE, PRE-REGISTERED BEFORE ANY NUMBER IS SEEN.
--
-- PRIMARY: AVERAGE COST (POOL). Every share on a leg carries that leg's BUY
-- VWAP, so residual cost = rY*vY + rN*vN. It is primary for one stated reason
-- and not because of its result: it is THE RULE ALREADY IMPLIED BY THE
-- PUBLISHED matched_cost = M*(vY + vN) USED THROUGHOUT RUNS 58-63. Using
-- anything else as primary would silently restate every earlier figure.
--
-- SENSITIVITY 1: FIFO -- the earliest shares on each leg are the matched ones.
-- This is not an arbitrary third convention. It is the accounting image of the
-- chronological pairing already used in the BUY work, where
-- d_m(t) = max(min(cy_t, cn_t) - min(cy_{t-1}, cn_{t-1}), 0) telescopes to
-- min(Y, N) = M: a share becomes matched at the moment the opposite leg's
-- cumulative quantity covers it, which is exactly first-in-first-matched.
--
-- SENSITIVITY 2: LIFO -- the latest shares on each leg are the matched ones.
-- Included as the opposing convention. NOT as a bound: see below.
--
-- THE THREE RULES DO NOT FORM AN INTERVAL, AND THE PRIMARY IS NOT INSIDE ONE.
-- It is tempting to read FIFO and LIFO as brackets around average cost. THEY
-- ARE NOT. FIFO charges the residual the cost of the LAST rX shares of the leg
-- and LIFO the FIRST rX; when rX < qX/2 those two slices do not cover the leg,
-- so the whole-leg VWAP can sit outside both. Checked on 5,000 randomized
-- conditions before this file was written: the average-cost figure falls
-- OUTSIDE the FIFO-LIFO interval in 20.2% of them. The same check confirmed
-- the two expressions below are right -- FIFO residual cost equals the cost of
-- the leg's rX-share suffix, LIFO the rX-share prefix, and each rule's
-- portions sum to exactly rY + rN. So the three figures are three
-- conventions, reported side by side; the spread between them is a RANGE, and
-- it is never to be presented as an error bar around the primary.
--
-- ALL THREE ARE REPORTED WHATEVER THEY SHOW. The primary rule was fixed above
-- before the query ran, and it is not revised afterwards.
--
-- WHAT THE SENSITIVITY ACTUALLY MOVES. Under every rule the TOTAL is invariant:
-- matched_gross(rule) + direct_directional(rule) = M + residual_settlement
-- - acquisition_cost, with no rule in it. The rule moves ONLY THE SPLIT between
-- the matched and directional components. So the spread across rules is the
-- size of the BOOKKEEPING CONVENTION in this decomposition, and it must not be
-- read as uncertainty about RN1's total economics. Statement 1 reports how much
-- room the convention has at all: where a residual leg was bought at a single
-- price, all three rules coincide exactly.
--
-- ---------------------------------------------------------------------------
-- COHORT. CLEAN = settled, structurally eligible ({0,1} two-slot), BOTH legs
-- bought, ZERO SELL shares in window, NO canonical fills outside the window.
-- Named NO_SELL_FULLY_IN_WINDOW: the redemption half of the owner's
-- NO_SELL_NO_SPECIAL_DISPOSITION cannot be verified from a ledger whose side is
-- CHECK (side IN ('BUY','SELL')), so it is named rather than folded in.
--
-- Window: 2026-08-05 00:00Z <= ts < 2026-09-11 12:00Z, canonical RN1 fills.
--
-- Read-only: five SELECTs.
-- ============================================================================


\echo '== 1. RESIDUAL SHAPE and how much room the allocation convention has =='
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
         count(DISTINCT px) FILTER (WHERE side = 'BUY') AS n_px
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0))  AS acq_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.n_px, 0) END) AS npx_y,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.n_px, 0) END) AS npx_n
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
), e AS (
  SELECT c.*,
         COALESCE(ed.n_out, 0) AS n_out,
         LEAST(c.qy, c.qn) AS mq,
         c.qy - LEAST(c.qy, c.qn) AS ry,
         c.qn - LEAST(c.qy, c.qn) AS rn_
    FROM cond c
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
     AND mk.resolved AND mk.resolved_prices IS NOT NULL
), k AS (
  SELECT e.*,
         (e.sell_qty = 0 AND e.n_out = 0 AND e.qy > 0 AND e.qn > 0) AS clean,
         CASE WHEN e.ry > 0 THEN e.npx_y WHEN e.rn_ > 0 THEN e.npx_n END
           AS npx_on_residual_leg
    FROM e
)
SELECT CASE
         WHEN qy = 0 OR qn = 0
           THEN '5 SINGLE LEG ONLY (M = 0, purely directional, no pair)'
         WHEN ry > 0 AND rn_ > 0
           THEN '4 BOTH LEGS RESIDUAL (arithmetically impossible: a self-check)'
         WHEN ry > 0 THEN '1 RESIDUAL ON OUTCOME 0 ONLY'
         WHEN rn_ > 0 THEN '2 RESIDUAL ON OUTCOME 1 ONLY'
         ELSE '3 PERFECTLY MATCHED (no residual inventory)'
       END AS residual_shape,
       count(*) AS conditions,
       count(*) FILTER (WHERE clean) AS clean_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(acq_cost) FILTER (WHERE clean)::numeric, 0) AS acq_cost_clean,
       round(sum(ry + rn_)::numeric, 0) AS residual_shares,
       count(*) FILTER (WHERE npx_on_residual_leg = 1)
         AS residual_leg_bought_at_one_price,
       count(*) FILTER (WHERE npx_on_residual_leg > 1)
         AS residual_leg_bought_at_many_prices,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY npx_on_residual_leg)::numeric, 1)
         AS p50_distinct_prices_on_residual_leg,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY npx_on_residual_leg)::numeric, 1)
         AS p90_distinct_prices_on_residual_leg
  FROM k GROUP BY 1 ORDER BY 1;


\echo '== 2. THE THREE ALLOCATION RULES on the CLEAN cohort =='
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
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0))  AS acq_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
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
), cl AS (
  SELECT c.condition_id, c.acq_cost, c.qy, c.qn, c.cy, c.cn,
         LEAST(c.qy, c.qn) AS mq,
         c.qy - LEAST(c.qy, c.qn) AS ry,
         c.qn - LEAST(c.qy, c.qn) AS rn_,
         (mk.resolved_prices->>0)::float8 AS py,
         (mk.resolved_prices->>1)::float8 AS pn
    FROM cond c
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
     AND mk.resolved AND mk.resolved_prices IS NOT NULL
     AND c.sell_qty = 0 AND COALESCE(ed.n_out, 0) = 0
     AND c.qy > 0 AND c.qn > 0
), buys AS (
  SELECT i.condition_id, i.outcome_index, i.sh, i.px,
         sum(i.sh) OVER (PARTITION BY i.condition_id, i.outcome_index
                         ORDER BY i.ts, i.id
                         ROWS UNBOUNDED PRECEDING) AS cum
    FROM inwin i
    JOIN cl ON cl.condition_id = i.condition_id
   WHERE i.side = 'BUY'
), alloc AS (
  SELECT b.condition_id,
         sum(b.px * greatest(0.0, least(b.sh, b.cum - cl.mq)))
           AS resid_cost_fifo,
         sum(b.px * greatest(0.0, least(b.sh,
               (CASE WHEN b.outcome_index = 0 THEN cl.ry ELSE cl.rn_ END)
               - (b.cum - b.sh))))
           AS resid_cost_lifo,
         sum(greatest(0.0, least(b.sh, b.cum - cl.mq))) AS resid_shares_fifo,
         sum(greatest(0.0, least(b.sh,
               (CASE WHEN b.outcome_index = 0 THEN cl.ry ELSE cl.rn_ END)
               - (b.cum - b.sh)))) AS resid_shares_lifo
    FROM buys b JOIN cl ON cl.condition_id = b.condition_id
   GROUP BY 1
), f AS (
  SELECT cl.*,
         a.resid_cost_fifo, a.resid_cost_lifo, a.resid_shares_fifo_check,
         cl.py * cl.ry + cl.pn * cl.rn_ AS resid_settlement,
         CASE WHEN cl.qy > 0 THEN cl.cy / cl.qy END AS vy,
         CASE WHEN cl.qn > 0 THEN cl.cn / cl.qn END AS vn
    FROM cl JOIN alloc a ON a.condition_id = cl.condition_id
), g AS (
  SELECT f.*,
         (f.ry * f.vy + f.rn_ * f.vn) AS resid_cost_avg,
         f.mq * (f.vy + f.vn)         AS matched_cost_avg
    FROM f
)
SELECT '1 AVERAGE COST (POOL) -- PRIMARY, matches published matched_cost' AS allocation_rule,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(matched_cost_avg)::numeric, 0) AS matched_cost,
       round(sum(mq - matched_cost_avg)::numeric, 0) AS matched_gross_pnl,
       round((100.0 * sum(mq - matched_cost_avg)
              / NULLIF(sum(matched_cost_avg), 0))::numeric, 3) AS matched_roi_pct,
       round(sum(resid_cost_avg)::numeric, 0) AS residual_capital,
       round(sum(resid_settlement)::numeric, 0) AS residual_settlement_value,
       round(sum(resid_settlement - resid_cost_avg)::numeric, 0)
         AS direct_directional_residual_pnl,
       round((100.0 * sum(resid_settlement - resid_cost_avg)
              / NULLIF(sum(resid_cost_avg), 0))::numeric, 3) AS directional_roi_pct,
       round(sum(mq - matched_cost_avg + resid_settlement - resid_cost_avg)::numeric, 0)
         AS total_pnl_invariant_across_rules
  FROM g
UNION ALL
SELECT '2 FIFO (earliest shares matched; image of the dM pairing)',
       count(*),
       round(sum(acq_cost)::numeric, 0),
       round(sum(acq_cost - resid_cost_fifo)::numeric, 0),
       round(sum(mq - (acq_cost - resid_cost_fifo))::numeric, 0),
       round((100.0 * sum(mq - (acq_cost - resid_cost_fifo))
              / NULLIF(sum(acq_cost - resid_cost_fifo), 0))::numeric, 3),
       round(sum(resid_cost_fifo)::numeric, 0),
       round(sum(resid_settlement)::numeric, 0),
       round(sum(resid_settlement - resid_cost_fifo)::numeric, 0),
       round((100.0 * sum(resid_settlement - resid_cost_fifo)
              / NULLIF(sum(resid_cost_fifo), 0))::numeric, 3),
       round(sum(mq - (acq_cost - resid_cost_fifo)
                 + resid_settlement - resid_cost_fifo)::numeric, 0)
  FROM g
UNION ALL
SELECT '3 LIFO (latest shares matched; the opposing bound)',
       count(*),
       round(sum(acq_cost)::numeric, 0),
       round(sum(acq_cost - resid_cost_lifo)::numeric, 0),
       round(sum(mq - (acq_cost - resid_cost_lifo))::numeric, 0),
       round((100.0 * sum(mq - (acq_cost - resid_cost_lifo))
              / NULLIF(sum(acq_cost - resid_cost_lifo), 0))::numeric, 3),
       round(sum(resid_cost_lifo)::numeric, 0),
       round(sum(resid_settlement)::numeric, 0),
       round(sum(resid_settlement - resid_cost_lifo)::numeric, 0),
       round((100.0 * sum(resid_settlement - resid_cost_lifo)
              / NULLIF(sum(resid_cost_lifo), 0))::numeric, 3),
       round(sum(mq - (acq_cost - resid_cost_lifo)
                 + resid_settlement - resid_cost_lifo)::numeric, 0)
  FROM g
 ORDER BY 1;


\echo '== 3. RECONCILIATION on the CLEAN cohort: direct vs subtraction =='
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
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0))  AS acq_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
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
), cl AS (
  SELECT c.condition_id, c.acq_cost, c.qy, c.qn, c.cy, c.cn,
         LEAST(c.qy, c.qn) AS mq,
         c.qy - LEAST(c.qy, c.qn) AS ry,
         c.qn - LEAST(c.qy, c.qn) AS rn_,
         (mk.resolved_prices->>0)::float8 AS py,
         (mk.resolved_prices->>1)::float8 AS pn
    FROM cond c
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
     AND mk.resolved AND mk.resolved_prices IS NOT NULL
     AND c.sell_qty = 0 AND COALESCE(ed.n_out, 0) = 0
     AND c.qy > 0 AND c.qn > 0
), buys AS (
  SELECT i.condition_id, i.outcome_index, i.sh, i.px,
         sum(i.sh) OVER (PARTITION BY i.condition_id, i.outcome_index
                         ORDER BY i.ts, i.id
                         ROWS UNBOUNDED PRECEDING) AS cum
    FROM inwin i
    JOIN cl ON cl.condition_id = i.condition_id
   WHERE i.side = 'BUY'
), alloc AS (
  SELECT b.condition_id,
         sum(b.px * greatest(0.0, least(b.sh, b.cum - cl.mq))) AS resid_cost_fifo,
         sum(b.px * greatest(0.0, least(b.sh,
               (CASE WHEN b.outcome_index = 0 THEN cl.ry ELSE cl.rn_ END)
               - (b.cum - b.sh)))) AS resid_cost_lifo
    FROM buys b JOIN cl ON cl.condition_id = b.condition_id
   GROUP BY 1
), g AS (
  SELECT cl.*, a.resid_cost_fifo, a.resid_cost_lifo,
         cl.py * cl.ry + cl.pn * cl.rn_ AS resid_settlement,
         cl.ry * (cl.cy / cl.qy) + cl.rn_ * (cl.cn / cl.qn) AS resid_cost_avg,
         cl.mq * (cl.cy / cl.qy + cl.cn / cl.qn) AS matched_cost_avg,
         cl.py * cl.qy + cl.pn * cl.qn - cl.acq_cost AS trading_pnl
    FROM cl JOIN alloc a ON a.condition_id = cl.condition_id
)
SELECT count(*) AS clean_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(trading_pnl - (mq - matched_cost_avg))::numeric, 2)
         AS subtraction_remainder_usd,
       round(sum(resid_settlement - resid_cost_avg)::numeric, 2)
         AS direct_directional_avg_usd,
       round(sum(trading_pnl - (mq - matched_cost_avg)
                 - (resid_settlement - resid_cost_avg))::numeric, 2)
         AS reconciliation_residual_usd,
       round((100.0 * sum(trading_pnl - (mq - matched_cost_avg)
                          - (resid_settlement - resid_cost_avg))
              / NULLIF(sum(acq_cost), 0))::numeric, 9)
         AS reconciliation_residual_pct_of_acq_cost,
       round(sum(mq * (py + pn - 1.0))::numeric, 2)
         AS predicted_residual_from_payout_vectors_usd,
       count(*) FILTER (WHERE abs(py + pn - 1.0) > 1e-9)
         AS conditions_whose_payout_vector_does_not_sum_to_1,
       round(sum(resid_settlement - resid_cost_fifo
                 - (resid_settlement - resid_cost_avg))::numeric, 0)
         AS fifo_minus_avg_split_shift_usd,
       round(sum(resid_settlement - resid_cost_lifo
                 - (resid_settlement - resid_cost_avg))::numeric, 0)
         AS lifo_minus_avg_split_shift_usd
  FROM g;


\echo '== 4. THE SAME RECONCILIATION on the FULL settled cohort: the gap is the sold-share term =='
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
         sum(COALESCE(l.cbuy, 0))  AS acq_cost,
         sum(COALESCE(l.csell, 0)) AS sell_proceeds,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qsell, 0) END) AS sy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qsell, 0) END) AS sn
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
), g AS (
  SELECT c.condition_id, c.acq_cost, c.sell_proceeds, c.sell_qty,
         c.qy, c.qn, c.cy, c.cn, c.sy, c.sn,
         COALESCE(ed.n_out, 0) AS n_out,
         LEAST(c.qy, c.qn) AS mq,
         c.qy - LEAST(c.qy, c.qn) AS ry,
         c.qn - LEAST(c.qy, c.qn) AS rn_,
         (mk.resolved_prices->>0)::float8 AS py,
         (mk.resolved_prices->>1)::float8 AS pn
    FROM cond c
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
     AND mk.resolved AND mk.resolved_prices IS NOT NULL
     AND c.qy > 0 AND c.qn > 0
), h AS (
  SELECT g.*,
         (g.sell_qty = 0 AND g.n_out = 0) AS clean,
         g.py * g.ry + g.pn * g.rn_ AS resid_settlement,
         g.ry * (g.cy / g.qy) + g.rn_ * (g.cn / g.qn) AS resid_cost_avg,
         g.mq * (g.cy / g.qy + g.cn / g.qn) AS matched_cost_avg,
         g.py * (g.qy - g.sy) + g.pn * (g.qn - g.sn)
           - (g.acq_cost - g.sell_proceeds) AS trading_pnl
    FROM g
)
SELECT CASE WHEN clean THEN '1 CLEAN (no sell, fully in window)'
            ELSE '2 CONTAMINATED (sells and/or fills outside the window)' END AS cohort,
       count(*) AS conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(trading_pnl - (mq - matched_cost_avg))::numeric, 2)
         AS subtraction_remainder_usd,
       round(sum(resid_settlement - resid_cost_avg)::numeric, 2)
         AS direct_directional_avg_usd,
       round(sum(trading_pnl - (mq - matched_cost_avg)
                 - (resid_settlement - resid_cost_avg))::numeric, 2)
         AS gap_by_subtraction_usd,
       round(sum(sell_proceeds - (py * sy + pn * sn))::numeric, 2)
         AS gap_by_direct_sold_share_formula_usd,
       round(sum(trading_pnl - (mq - matched_cost_avg)
                 - (resid_settlement - resid_cost_avg)
                 - (sell_proceeds - (py * sy + pn * sn)))::numeric, 2)
         AS disagreement_between_the_two_paths_usd,
       round((100.0 * sum(trading_pnl - (mq - matched_cost_avg)
                          - (resid_settlement - resid_cost_avg))
              / NULLIF(sum(acq_cost), 0))::numeric, 6)
         AS gap_pct_of_acq_cost
  FROM h GROUP BY 1 ORDER BY 1;


\echo '== 5. PER-CONDITION reconciliation residual: does a small total hide offsets? =='
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
         sum(sh)      FILTER (WHERE side = 'SELL') AS qsell
    FROM inwin GROUP BY 1, 2
), cond AS (
  SELECT l.condition_id,
         sum(COALESCE(l.cbuy, 0))  AS acq_cost,
         sum(COALESCE(l.qsell, 0)) AS sell_qty,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.qbuy, 0) END) AS qy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.qbuy, 0) END) AS qn,
         max(CASE WHEN l.outcome_index = 0 THEN COALESCE(l.cbuy, 0) END) AS cy,
         max(CASE WHEN l.outcome_index = 1 THEN COALESCE(l.cbuy, 0) END) AS cn
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
), g AS (
  SELECT c.condition_id, c.acq_cost,
         LEAST(c.qy, c.qn) AS mq,
         c.qy - LEAST(c.qy, c.qn) AS ry,
         c.qn - LEAST(c.qy, c.qn) AS rn_,
         c.cy / c.qy AS vy, c.cn / c.qn AS vn,
         c.qy, c.qn,
         (mk.resolved_prices->>0)::float8 AS py,
         (mk.resolved_prices->>1)::float8 AS pn
    FROM cond c
    LEFT JOIN edges ed ON ed.condition_id = c.condition_id
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
     AND mk.resolved AND mk.resolved_prices IS NOT NULL
     AND c.sell_qty = 0 AND COALESCE(ed.n_out, 0) = 0
     AND c.qy > 0 AND c.qn > 0
), r AS (
  SELECT g.*,
         (g.py * g.qy + g.pn * g.qn - g.acq_cost)
           - (g.mq - g.mq * (g.vy + g.vn))
           - ((g.py * g.ry + g.pn * g.rn_) - (g.ry * g.vy + g.rn_ * g.vn))
           AS recon
    FROM g
)
SELECT count(*) AS clean_conditions,
       round(sum(recon)::numeric, 4) AS sum_recon_usd,
       round(sum(abs(recon))::numeric, 4) AS sum_abs_recon_usd,
       round(max(abs(recon))::numeric, 6) AS max_abs_recon_usd,
       count(*) FILTER (WHERE abs(recon) > 0.01) AS conditions_over_1_cent,
       count(*) FILTER (WHERE abs(recon) > 1.00) AS conditions_over_1_dollar,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY abs(recon))::numeric, 9) AS p50_abs,
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY abs(recon))::numeric, 9) AS p99_abs,
       round((100.0 * sum(abs(recon)) / NULLIF(sum(acq_cost), 0))::numeric, 9)
         AS sum_abs_recon_pct_of_acq_cost
  FROM r;
