-- ============================================================================
-- POPULATION CLOSURE, and the corrected NON_MATCHED_REMAINDER
-- (2026-09-11, read-only.) Owner-specified.
--
-- Run 62's residual_roi_pct is RETRACTED (research/RETRACTIONS.md section 2):
-- on a single-leg condition pair_cost is NULL by its own CASE guard, so both
-- trading_pnl - mq*(1-pair_cost) and acq_cost - mq*pair_cost are NULL and
-- sum() SKIPS THE ROW -- silently removing the purely directional conditions
-- from BOTH numerator and denominator, while total_roi_pct in the same row
-- kept them. Three metrics, three populations, in one displayed identity.
--
-- THE RULE THIS FILE ENFORCES, and it is the point of the file:
--   NO METRIC IN ONE DISPLAYED ACCOUNTING IDENTITY MAY USE A DIFFERENT
--   CONDITION POPULATION.
-- Every term below is therefore COALESCEd to its economically correct zero
-- rather than left NULL, and every term carries its own row count so that
-- equality of populations is VISIBLE IN THE OUTPUT rather than asserted in a
-- header.
--
-- THE DEFINITIONS, zero-safe throughout:
--
--   qy, qn            COALESCE(sum(size) FILTER (leg), 0)
--   M                 LEAST(qy, qn)                       -- 0 when a leg is absent
--   pair_cost         NULL unless qy > 0 AND qn > 0       -- genuinely undefined
--   MATCHED_GROSS_PNL COALESCE(M * (1 - pair_cost), 0)    -- 0 when M = 0
--   TOTAL_TRADING_PNL py*qy + pn*qn - acq_cost            -- never NULL if settled
--   NON_MATCHED_REMAINDER_PNL
--                     TOTAL_TRADING_PNL - MATCHED_GROSS_PNL
--
-- With MATCHED_GROSS_PNL COALESCEd to 0, the remainder is a total minus a
-- number rather than a total minus a NULL, so closure holds BY CONSTRUCTION at
-- the row level. Statement 1 still MEASURES it per condition rather than
-- relying on that, because construction arguments are exactly what the last two
-- defects survived behind.
--
-- SINGLE-LEG INVARIANTS, expected violations 0:
--     M = 0,  MATCHED_GROSS_PNL = 0,  NON_MATCHED_REMAINDER_PNL = TOTAL.
--
-- POPULATION: settled (resolved with a retained payout vector) AND
-- structurally eligible (exactly two outcome slots, index 0 and 1). Fixed once
-- and used unchanged by every statement here.
--
-- Read-only: three SELECTs.
-- ============================================================================


\echo '== 1. CLOSURE: TOTAL = MATCHED + REMAINDER, per condition and aggregate =='
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
), cond AS MATERIALIZED (
  SELECT condition_id,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS qy,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS qn,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS cy,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS cn
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2 AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0) AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), u AS MATERIALIZED (
  SELECT c.condition_id, c.qy, c.qn, c.cy + c.cn AS acq_cost,
         LEAST(c.qy, c.qn) AS m,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         (mk.resolved_prices->>0)::float8 * c.qy
           + (mk.resolved_prices->>1)::float8 * c.qn - (c.cy + c.cn) AS total_pnl
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
   WHERE mk.resolved AND mk.resolved_prices IS NOT NULL
), k AS MATERIALIZED (
  SELECT u.*,
         COALESCE(u.m * (1.0 - u.pair_cost), 0) AS matched_pnl,
         u.total_pnl - COALESCE(u.m * (1.0 - u.pair_cost), 0) AS remainder_pnl,
         (u.qy = 0 OR u.qn = 0) AS single_leg
    FROM u
)
SELECT count(*) AS conditions,
       count(*) FILTER (WHERE single_leg) AS single_leg_conditions,
       round(sum(total_pnl)::numeric, 2) AS total_trading_pnl,
       round(sum(matched_pnl)::numeric, 2) AS matched_gross_pnl,
       round(sum(remainder_pnl)::numeric, 2) AS non_matched_remainder_pnl,
       round(sum(total_pnl - matched_pnl - remainder_pnl)::numeric, 6)
         AS aggregate_closure_gap,
       round(max(abs(total_pnl - matched_pnl - remainder_pnl))::numeric, 9)
         AS max_abs_per_condition_closure_gap,
       count(*) FILTER (WHERE abs(total_pnl - matched_pnl - remainder_pnl) > 1e-6)
         AS closure_violations_expected_0,
       count(*) FILTER (WHERE single_leg AND abs(m) > 1e-9)
         AS single_leg_m_not_zero_expected_0,
       count(*) FILTER (WHERE single_leg AND abs(matched_pnl) > 1e-9)
         AS single_leg_matched_not_zero_expected_0,
       count(*) FILTER (WHERE single_leg AND abs(remainder_pnl - total_pnl) > 1e-6)
         AS single_leg_remainder_ne_total_expected_0
  FROM k;


\echo '== 2. WHAT THE RETRACTED CALCULATION OMITTED =='
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
), cond AS MATERIALIZED (
  SELECT condition_id,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS qy,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS qn,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS cy,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS cn
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2 AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0) AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) v WHERE condition_id IS NOT NULL
), k AS MATERIALIZED (
  SELECT c.condition_id, c.qy, c.qn, c.cy + c.cn AS acq_cost,
         (br1.condition_id IS NOT NULL) AS bridged,
         LEAST(c.qy, c.qn) AS m,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         (mk.resolved_prices->>0)::float8 * c.qy
           + (mk.resolved_prices->>1)::float8 * c.qn - (c.cy + c.cn) AS total_pnl
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
    LEFT JOIN br1 ON br1.condition_id = c.condition_id
   WHERE mk.resolved AND mk.resolved_prices IS NOT NULL
)
SELECT CASE WHEN bridged THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS settled_eligible_conditions,
       count(*) FILTER (WHERE pair_cost IS NULL) AS conditions_omitted_by_the_bug,
       round((100.0 * count(*) FILTER (WHERE pair_cost IS NULL) / count(*))::numeric, 2)
         AS pct_conditions_omitted,
       round(sum(acq_cost) FILTER (WHERE pair_cost IS NULL)::numeric, 0)
         AS acq_cost_omitted_usd,
       round((100.0 * sum(acq_cost) FILTER (WHERE pair_cost IS NULL)
              / NULLIF(sum(acq_cost), 0))::numeric, 2) AS pct_acq_cost_omitted,
       round(sum(total_pnl) FILTER (WHERE pair_cost IS NULL)::numeric, 0)
         AS pnl_omitted_usd,
       round(sum(abs(total_pnl)) FILTER (WHERE pair_cost IS NULL)::numeric, 0)
         AS abs_pnl_omitted_usd,
       round((100.0 * sum(abs(total_pnl)) FILTER (WHERE pair_cost IS NULL)
              / NULLIF(sum(abs(total_pnl)), 0))::numeric, 2) AS pct_abs_pnl_omitted
  FROM k GROUP BY 1 ORDER BY 1;


\echo '== 3. BRIDGED vs UNBRIDGED, every term on ONE population, each with its n =='
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
), cond AS MATERIALIZED (
  SELECT condition_id,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS qy,
         COALESCE(sum(sh)      FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS qn,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS cy,
         COALESCE(sum(sh * px) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS cn
    FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
   GROUP BY 1
), tok AS MATERIALIZED (
  SELECT mt.condition_id FROM market_tokens mt GROUP BY mt.condition_id
   HAVING count(*) = 2 AND count(DISTINCT mt.outcome_index) = 2
      AND count(*) FILTER (WHERE mt.outcome_index IS NULL) = 0
      AND bool_or(mt.outcome_index = 0) AND bool_or(mt.outcome_index = 1)
      AND max(mt.outcome_index) = 1
), br1 AS MATERIALIZED (
  SELECT DISTINCT condition_id FROM (
    SELECT condition_id FROM mirror_books        WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
    UNION ALL SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
  ) v WHERE condition_id IS NOT NULL
), k AS MATERIALIZED (
  SELECT c.condition_id, c.cy + c.cn AS acq_cost,
         (br1.condition_id IS NOT NULL) AS bridged,
         LEAST(c.qy, c.qn) AS m,
         CASE WHEN c.qy > 0 AND c.qn > 0 THEN c.cy / c.qy + c.cn / c.qn END AS pair_cost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (CASE WHEN c.qy > 0 AND c.qn > 0
                          THEN c.cy / c.qy + c.cn / c.qn END), 0) AS matched_cost,
         COALESCE(LEAST(c.qy, c.qn)
                  * (1.0 - (CASE WHEN c.qy > 0 AND c.qn > 0
                                 THEN c.cy / c.qy + c.cn / c.qn END)), 0) AS matched_pnl,
         (mk.resolved_prices->>0)::float8 * c.qy
           + (mk.resolved_prices->>1)::float8 * c.qn - (c.cy + c.cn) AS total_pnl
    FROM cond c
    JOIN tok t ON t.condition_id = c.condition_id
    JOIN markets mk ON mk.condition_id = c.condition_id
    LEFT JOIN br1 ON br1.condition_id = c.condition_id
   WHERE mk.resolved AND mk.resolved_prices IS NOT NULL
)
SELECT CASE WHEN bridged THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS n_conditions,
       count(acq_cost) AS n_acq_cost,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       count(matched_cost) AS n_matched_cost,
       round(sum(matched_cost)::numeric, 0) AS matched_cost,
       count(matched_pnl) AS n_matched_pnl,
       round(sum(matched_pnl)::numeric, 0) AS matched_gross_pnl,
       round((100.0 * sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 3)
         AS matched_gross_roi_pct,
       count(*) AS n_non_matched_denominator,
       round(sum(acq_cost - matched_cost)::numeric, 0) AS non_matched_denominator,
       count(*) AS n_remainder,
       round(sum(total_pnl - matched_pnl)::numeric, 0) AS non_matched_remainder_pnl,
       round((100.0 * sum(total_pnl - matched_pnl)
              / NULLIF(sum(acq_cost - matched_cost), 0))::numeric, 3)
         AS non_matched_remainder_roi_pct,
       count(total_pnl) AS n_total_pnl,
       round(sum(total_pnl)::numeric, 0) AS total_trading_pnl,
       round((100.0 * sum(total_pnl) / NULLIF(sum(acq_cost), 0))::numeric, 3)
         AS total_roi_pct
  FROM k GROUP BY 1
UNION ALL
SELECT '3 ALL SETTLED ELIGIBLE', count(*), count(acq_cost),
       round(sum(acq_cost)::numeric, 0), count(matched_cost),
       round(sum(matched_cost)::numeric, 0), count(matched_pnl),
       round(sum(matched_pnl)::numeric, 0),
       round((100.0 * sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 3),
       count(*), round(sum(acq_cost - matched_cost)::numeric, 0), count(*),
       round(sum(total_pnl - matched_pnl)::numeric, 0),
       round((100.0 * sum(total_pnl - matched_pnl)
              / NULLIF(sum(acq_cost - matched_cost), 0))::numeric, 3),
       count(total_pnl), round(sum(total_pnl)::numeric, 0),
       round((100.0 * sum(total_pnl) / NULLIF(sum(acq_cost), 0))::numeric, 3)
  FROM k
 ORDER BY 1;
