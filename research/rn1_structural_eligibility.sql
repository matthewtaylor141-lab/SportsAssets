-- ============================================================================
-- A. STRUCTURAL ELIGIBILITY FOR MATCHED ECONOMICS, WITHOUT SETTLEMENT
-- (2026-09-11, read-only.)
--
-- Run 62 left MATCHED_MECHANISM_GENERALIZABLE at INDETERMINATE for one reason:
-- the payoff invariant was validated on RETAINED SETTLED payout vectors
-- (101,734 of 101,757 sum to 1, 99.9774%) and the owner correctly refused to
-- let that be extended to the unresolved population by inference. This file
-- establishes eligibility STRUCTURALLY instead, using no settlement at all.
--
-- THE STRUCTURAL CLAIM AND ITS SOURCES, stated before the measurement.
--
--   1 market_tokens (migration 001_init.sql) is keyed
--         token_id PRIMARY KEY, condition_id REFERENCES markets, outcome,
--         outcome_index
--     with token_id documented as the "CTF ERC-1155 tokenId". A binary
--     condition in the Gnosis Conditional Token Framework has exactly two
--     outcome slots, and a complete set of one of each redeems for exactly
--     one unit of collateral. That is the contract semantics the $1 pair
--     rests on, not an empirical regularity.
--
--   2 markets.resolved_prices is documented in the same migration as
--         "payout per outcome index at resolution, e.g. [1, 0]"
--     i.e. a payout VECTOR over the outcome slots.
--
--   3 The engine treats the two tokens as complementary throughout, pricing
--     the other token at 1 - p: analytics/mirror.py:275, :330-332, :905.
--     That is a code-level assertion of complementarity, independent of any
--     particular resolution.
--
-- SO THE TEST IS: does each RN1 canonical condition actually PRESENT as a
-- two-slot binary condition in retained data? Anything that does not is NOT
-- assumed eligible; it is classified and reported.
--
--   STRUCTURALLY_ELIGIBLE    exactly 2 token rows, outcome_index set = {0,1}
--   STRUCTURALLY_INELIGIBLE  token rows exist but are not a clean {0,1} pair
--                            (>2 slots, duplicate index, missing index, or a
--                            single slot)
--   STRUCTURE_UNKNOWN        no market_tokens rows retained for the condition
--
-- STRUCTURE_UNKNOWN is NOT folded into eligible. A missing catalogue row is an
-- absence of evidence about structure, not evidence of binary structure.
--
-- Read-only: four SELECTs.
-- ============================================================================


\echo '== 1. TOKEN-SLOT SHAPE of every RN1 canonical condition =='
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
), conds AS (SELECT DISTINCT condition_id FROM inwin
), tok AS (
  SELECT mt.condition_id,
         count(*) AS n_tokens,
         count(DISTINCT mt.outcome_index) AS n_distinct_idx,
         count(*) FILTER (WHERE mt.outcome_index IS NULL) AS n_null_idx,
         bool_or(mt.outcome_index = 0) AS has0,
         bool_or(mt.outcome_index = 1) AS has1,
         max(mt.outcome_index) AS max_idx
    FROM market_tokens mt GROUP BY 1
)
SELECT COALESCE(t.n_tokens, 0) AS token_rows,
       COALESCE(t.n_distinct_idx, 0) AS distinct_outcome_index,
       COALESCE(t.n_null_idx, 0) AS null_outcome_index,
       COALESCE(t.max_idx, -1) AS max_outcome_index,
       count(*) AS conditions
  FROM conds c LEFT JOIN tok t ON t.condition_id = c.condition_id
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC;


\echo '== 2. ELIGIBILITY CLASS with coverage by conditions, acq cost, matched cost =='
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
), tok AS (
  SELECT mt.condition_id, count(*) AS n_tokens,
         count(DISTINCT mt.outcome_index) AS n_idx,
         count(*) FILTER (WHERE mt.outcome_index IS NULL) AS n_null,
         bool_or(mt.outcome_index = 0) AS has0,
         bool_or(mt.outcome_index = 1) AS has1,
         max(mt.outcome_index) AS max_idx
    FROM market_tokens mt GROUP BY 1
), cls AS (
  SELECT c.*,
         CASE
           WHEN t.condition_id IS NULL THEN '3 STRUCTURE_UNKNOWN (no catalogue rows)'
           WHEN t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
                AND t.has0 AND t.has1 AND t.max_idx = 1
             THEN '1 STRUCTURALLY_ELIGIBLE (exactly two slots, index 0 and 1)'
           ELSE '2 STRUCTURALLY_INELIGIBLE (not a clean two-slot pair)'
         END AS eligibility
    FROM cond c LEFT JOIN tok t ON t.condition_id = c.condition_id
)
SELECT eligibility,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct_conditions,
       round(sum(acq_cost)::numeric, 0) AS acquisition_cost,
       round((100.0 * sum(acq_cost) / sum(sum(acq_cost)) OVER ())::numeric, 3)
         AS pct_acq_cost,
       round(sum(mq * pair_cost)::numeric, 0) AS matched_cost,
       round((100.0 * sum(mq * pair_cost) / sum(sum(mq * pair_cost)) OVER ())::numeric, 3)
         AS pct_matched_cost
  FROM cls GROUP BY 1 ORDER BY 1;


\echo '== 3. MATCHED MECHANISM on STRUCTURALLY_ELIGIBLE only, settlement-free =='
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
), tok AS (
  SELECT mt.condition_id, count(*) AS n_tokens,
         count(DISTINCT mt.outcome_index) AS n_idx,
         count(*) FILTER (WHERE mt.outcome_index IS NULL) AS n_null,
         bool_or(mt.outcome_index = 0) AS has0,
         bool_or(mt.outcome_index = 1) AS has1,
         max(mt.outcome_index) AS max_idx
    FROM market_tokens mt GROUP BY 1
), elig AS (
  SELECT c.* FROM cond c JOIN tok t ON t.condition_id = c.condition_id
   WHERE t.n_tokens = 2 AND t.n_idx = 2 AND t.n_null = 0
     AND t.has0 AND t.has1 AND t.max_idx = 1
), bridge AS (
  SELECT condition_id FROM mirror_books      WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_shadow WHERE us_market_slug IS NOT NULL
  UNION SELECT condition_id FROM mirror_candidate_refusals WHERE us_slug IS NOT NULL
), br1 AS (SELECT DISTINCT condition_id FROM bridge)
SELECT CASE WHEN br1.condition_id IS NOT NULL THEN '1 BRIDGED' ELSE '2 UNBRIDGED' END AS grp,
       count(*) AS conditions,
       round(sum(e.acq_cost)::numeric, 0) AS acquisition_cost,
       round(sum(e.mq * e.pair_cost)::numeric, 0) AS matched_cost,
       round(sum(e.mq)::numeric, 0) AS matched_qty,
       round((100.0 * sum(e.mq * e.pair_cost) / NULLIF(sum(e.acq_cost), 0))::numeric, 2)
         AS matched_pct_of_acq_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY e.pair_cost)::numeric, 4)
         AS p50_pair_cost,
       round(sum(e.mq * (1.0 - e.pair_cost))::numeric, 0) AS matched_gross_pnl_usd,
       round((100.0 * sum(e.mq * (1.0 - e.pair_cost))
              / NULLIF(sum(e.mq * e.pair_cost), 0))::numeric, 3)
         AS matched_gross_roi_pct
  FROM elig e LEFT JOIN br1 ON br1.condition_id = e.condition_id
 GROUP BY 1
UNION ALL
SELECT '3 ALL ELIGIBLE',
       count(*),
       round(sum(e.acq_cost)::numeric, 0),
       round(sum(e.mq * e.pair_cost)::numeric, 0),
       round(sum(e.mq)::numeric, 0),
       round((100.0 * sum(e.mq * e.pair_cost) / NULLIF(sum(e.acq_cost), 0))::numeric, 2),
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY e.pair_cost)::numeric, 4),
       round(sum(e.mq * (1.0 - e.pair_cost))::numeric, 0),
       round((100.0 * sum(e.mq * (1.0 - e.pair_cost))
              / NULLIF(sum(e.mq * e.pair_cost), 0))::numeric, 3)
  FROM elig e
 ORDER BY 1;


\echo '== 4. CORROBORATION: retained payout vectors on RN1 conditions only =='
-- Run 62 statement 1 tested ALL retained settled markets. This narrows it to
-- RN1's own conditions so the invariant is checked on the population in scope.
WITH base AS (
  SELECT t.tx_hash, t.asset, t.ts, t.condition_id,
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
), conds AS (
  SELECT DISTINCT condition_id FROM canon
   WHERE ts >= timestamptz '2026-08-05 00:00Z'
     AND ts <  timestamptz '2026-09-11 12:00Z'
)
SELECT CASE
         WHEN mk.resolved_prices IS NULL THEN '4 NO PAYOUT VECTOR RETAINED'
         WHEN jsonb_typeof(mk.resolved_prices) <> 'array'
           OR jsonb_array_length(mk.resolved_prices) <> 2
           THEN '3 PAYOUT VECTOR NOT A 2-ELEMENT ARRAY'
         WHEN abs(((mk.resolved_prices->>0)::float8
                 + (mk.resolved_prices->>1)::float8) - 1.0) < 1e-9
           THEN '1 SUMS TO 1'
         ELSE '2 DOES NOT SUM TO 1'
       END AS payout_check,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct
  FROM conds c LEFT JOIN markets mk ON mk.condition_id = c.condition_id
 GROUP BY 1 ORDER BY 1;
