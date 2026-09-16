-- ============================================================================
-- THE INDIVIDUAL FEE RESIDUAL, AND GROSS vs NET PAIR ECONOMICS
-- (2026-09-11, read-only.)
--
-- THE HYPOTHESIS UNDER TEST, stated so it can fail. Band averages matched
-- `chain = poll + 0.05 x p x (1-p)` to five decimals on two independent
-- price bands, and the absolute gap capped at exactly 0.0125 = 0.05 x 0.25.
-- That is suggestive, not proof: band means can agree while individual rows
-- scatter. So this measures the residual PER ROW,
--
--     eps = observed_chain_price - predicted_effective_price
--
-- with predicted = venue_price + s x 0.05 x p x (1-p), s = +1 on a BUY (the
-- taker pays more cash per share than the trade price) and -1 on a SELL (the
-- taker receives less). If the formula is exact subject to the 6-dp rounding
-- both paths apply, |eps| must sit at or below 5e-7 for essentially every
-- row. If it is merely an excellent approximation, the tail will say so.
--
-- MAKER / TAKER IS NOT OBSERVABLE FOR HIM. `trades.taker` is NULL on every
-- RN1 row (the population census read taker_unrecorded = every row), so the
-- split below is INFERRED, not read: a fill whose residual is smaller against
-- a ZERO fee than against the 5% fee is classed maker, and the reverse taker.
-- Statement 6 reports how many rows the venue actually labelled, so the
-- inference is never mistaken for a reading.
--
-- THE SCHEMA THE OWNER ASKED FOR, and why it matters. Economic price
-- information is NOT reduced to one canonical field. Two different things are
-- carried side by side:
--
--     venue_price          the venue's stated execution price (poll / Data API)
--     effective_cash_price the USDC per share that actually moved (chain)
--     fee_per_share        effective_cash_price - venue_price, signed by side
--     gross_notional       shares x venue_price
--     fee_amount           shares x fee_per_share
--     net_cash_flow        gross_notional + fee_amount on a BUY
--     *_source             which path each figure came from
--     fee_confidence       observed / modelled / unknown
--
-- THE QUESTION THAT MAKES THIS MATTER. A pair quoted under $1.00 is not the
-- same as a pair that COSTS under $1.00. Statement 7 therefore reports both
--
--     gross_pair_cost      YES venue_price + NO venue_price
--     effective_pair_cost  actual cash cost of YES + actual cash cost of NO
--     gross_matched_edge   1 - gross_pair_cost
--     net_matched_edge     1 - effective_pair_cost
--
-- If his ~2.2% matched return is gross and the fees eat it, the entire
-- matched-book thesis changes, and that is the number to know before any
-- architecture is chosen.
--
-- Read-only: seven SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE INDIVIDUAL RESIDUAL: is the formula exact, or approximate? =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), p AS (
  SELECT tx_hash, asset, side,
         count(*) FILTER (WHERE source = 'chain') AS n_chain,
         count(*) FILTER (WHERE source = 'poll')  AS n_poll,
         sum(sh) FILTER (WHERE source = 'chain') AS sh_chain,
         sum(sh) FILTER (WHERE source = 'poll')  AS sh_poll,
         max(px) FILTER (WHERE source = 'chain') AS px_chain,
         max(px) FILTER (WHERE source = 'poll')  AS px_poll
    FROM t GROUP BY 1, 2, 3
), r AS (
  SELECT side, px_poll, px_chain,
         CASE WHEN side = 'BUY' THEN 1.0 ELSE -1.0 END AS s,
         px_chain - (px_poll + (CASE WHEN side = 'BUY' THEN 1.0 ELSE -1.0 END)
                     * 0.05 * px_poll * (1.0 - px_poll)) AS eps_taker,
         px_chain - px_poll AS eps_maker
    FROM p
   WHERE px_chain IS NOT NULL AND px_poll > 0 AND px_poll < 1
     AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01
     -- LIKE FOR LIKE ONLY. A class C group has chain at one fill and poll at
     -- ~2.31, so comparing max(price) per source pits chain's single price
     -- against poll's HIGHEST -- not a measurement of the same execution.
     -- Those rows were landing in the 'maker' bucket as an artifact, so the
     -- residual test is restricted to groups where BOTH sources carry
     -- exactly one fill.
     AND n_chain = 1 AND n_poll = 1
), c AS (
  SELECT r.*,
         CASE WHEN abs(eps_maker) <= abs(eps_taker) THEN 'maker (inferred)'
              ELSE 'taker (inferred)' END AS fee_class,
         CASE WHEN abs(eps_maker) <= abs(eps_taker) THEN eps_maker ELSE eps_taker END AS eps
    FROM r
)
SELECT side, fee_class, count(*) AS rows,
       round(avg(abs(eps))::numeric, 9) AS mean_abs_eps,
       round((percentile_cont(0.50) WITHIN GROUP (ORDER BY abs(eps)))::numeric, 9) AS p50,
       round((percentile_cont(0.90) WITHIN GROUP (ORDER BY abs(eps)))::numeric, 9) AS p90,
       round((percentile_cont(0.95) WITHIN GROUP (ORDER BY abs(eps)))::numeric, 9) AS p95,
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY abs(eps)))::numeric, 9) AS p99,
       round(max(abs(eps))::numeric, 9) AS max_abs_eps,
       round((100.0 * count(*) FILTER (WHERE abs(eps) < 1e-6) / count(*))::numeric, 3) AS pct_within_1e6,
       round((100.0 * count(*) FILTER (WHERE abs(eps) < 1e-5) / count(*))::numeric, 3) AS pct_within_1e5,
       round((100.0 * count(*) FILTER (WHERE abs(eps) < 1e-4) / count(*))::numeric, 3) AS pct_within_1e4,
       round((100.0 * count(*) FILTER (WHERE abs(eps) < 1e-3) / count(*))::numeric, 3) AS pct_within_1e3
  FROM c GROUP BY ROLLUP (side, fee_class) ORDER BY 1 NULLS LAST, 2 NULLS LAST;


\echo '== 2. THE IMPLIED FEE COEFFICIENT, solved per row =='
-- k = (chain - poll) / (p x (1-p)). If the wedge is a 5% fee, k clusters hard
-- at 0.05 for takers and at 0 for makers, and nowhere else.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), p AS (
  SELECT tx_hash, asset, side,
         sum(sh) FILTER (WHERE source = 'chain') AS sh_chain,
         sum(sh) FILTER (WHERE source = 'poll')  AS sh_poll,
         max(px) FILTER (WHERE source = 'chain') AS px_chain,
         max(px) FILTER (WHERE source = 'poll')  AS px_poll
    FROM t GROUP BY 1, 2, 3
), k AS (
  SELECT side, px_poll,
         (px_chain - px_poll) / NULLIF(px_poll * (1.0 - px_poll), 0)
           * (CASE WHEN side = 'BUY' THEN 1.0 ELSE -1.0 END) AS kk
    FROM p
   WHERE px_chain IS NOT NULL AND px_poll > 0 AND px_poll < 1
     AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01
)
SELECT CASE WHEN kk < 0.001          THEN '1 k ~ 0 (no fee: maker)'
            WHEN kk < 0.045          THEN '2 k 0.001-0.045'
            WHEN kk <= 0.055         THEN '3 k 0.045-0.055 (FIVE PERCENT)'
            WHEN kk <= 0.065         THEN '4 k 0.055-0.065'
            ELSE                          '5 k above 0.065' END AS k_band,
       count(*) AS rows,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 3) AS pct,
       round(avg(kk)::numeric, 8) AS avg_k,
       round(stddev_samp(kk)::numeric, 8) AS sd_k,
       round(min(kk)::numeric, 8) AS min_k, round(max(kk)::numeric, 8) AS max_k,
       round(avg(px_poll)::numeric, 4) AS avg_px
  FROM k GROUP BY 1 ORDER BY 1;


\echo '== 3. DOES IT HOLD ACROSS THE PRICE RANGE? residual by price bucket =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), p AS (
  SELECT tx_hash, asset, side,
         count(*) FILTER (WHERE source = 'chain') AS n_chain,
         count(*) FILTER (WHERE source = 'poll')  AS n_poll,
         sum(sh) FILTER (WHERE source = 'chain') AS sh_chain,
         sum(sh) FILTER (WHERE source = 'poll')  AS sh_poll,
         max(px) FILTER (WHERE source = 'chain') AS px_chain,
         max(px) FILTER (WHERE source = 'poll')  AS px_poll
    FROM t GROUP BY 1, 2, 3
), r AS (
  SELECT px_poll, px_chain, side,
         px_chain - (px_poll + (CASE WHEN side = 'BUY' THEN 1.0 ELSE -1.0 END)
                     * 0.05 * px_poll * (1.0 - px_poll)) AS eps_taker,
         px_chain - px_poll AS eps_maker
    FROM p
   WHERE px_chain IS NOT NULL AND px_poll > 0 AND px_poll < 1
     AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01
     -- LIKE FOR LIKE ONLY (same restriction as statement 1): a class C group
     -- has chain at one fill and poll at ~2.31, so max(price) per source
     -- would pit chain's single price against poll's HIGHEST. Restricted to
     -- groups where BOTH sources carry exactly one fill.
     AND n_chain = 1 AND n_poll = 1
)
SELECT width_bucket(px_poll, 0, 1, 10) AS price_decile,
       round(min(px_poll)::numeric, 3) AS px_from, round(max(px_poll)::numeric, 3) AS px_to,
       count(*) AS rows,
       count(*) FILTER (WHERE abs(eps_maker) <= abs(eps_taker)) AS maker_inferred,
       count(*) FILTER (WHERE abs(eps_taker) < abs(eps_maker)) AS taker_inferred,
       round(avg(abs(LEAST(abs(eps_taker), abs(eps_maker))))::numeric, 9) AS mean_abs_eps,
       round(max(abs(LEAST(abs(eps_taker), abs(eps_maker))))::numeric, 9) AS max_abs_eps,
       round(avg(abs(px_chain - px_poll))::numeric, 6) AS avg_raw_gap,
       round((0.05 * avg(px_poll) * (1 - avg(px_poll)))::numeric, 6) AS predicted_fee_at_mean_px
  FROM r GROUP BY 1 ORDER BY 1;


\echo '== 4. IS THE RESIDUAL JUST 6-DP ROUNDING? =='
-- chain stores round(usdc/size, 6) and poll stores the API value. If the
-- formula is exact, the residual must live inside one rounding step.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), p AS (
  SELECT tx_hash, asset, side,
         count(*) FILTER (WHERE source = 'chain') AS n_chain,
         count(*) FILTER (WHERE source = 'poll')  AS n_poll,
         sum(sh) FILTER (WHERE source = 'chain') AS sh_chain,
         sum(sh) FILTER (WHERE source = 'poll')  AS sh_poll,
         max(px) FILTER (WHERE source = 'chain') AS px_chain,
         max(px) FILTER (WHERE source = 'poll')  AS px_poll
    FROM t GROUP BY 1, 2, 3
), r AS (
  SELECT LEAST(abs(px_chain - (px_poll + (CASE WHEN side = 'BUY' THEN 1.0 ELSE -1.0 END)
                               * 0.05 * px_poll * (1.0 - px_poll))),
               abs(px_chain - px_poll)) AS abs_eps
    FROM p
   WHERE px_chain IS NOT NULL AND px_poll > 0 AND px_poll < 1
     AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01
     -- LIKE FOR LIKE ONLY (same restriction as statement 1): a class C group
     -- has chain at one fill and poll at ~2.31, so max(price) per source
     -- would pit chain's single price against poll's HIGHEST. Restricted to
     -- groups where BOTH sources carry exactly one fill.
     AND n_chain = 1 AND n_poll = 1
)
SELECT count(*) AS rows,
       count(*) FILTER (WHERE abs_eps <= 5e-7)  AS within_half_a_6dp_step,
       count(*) FILTER (WHERE abs_eps <= 5e-6)  AS within_5e6,
       count(*) FILTER (WHERE abs_eps <= 5e-5)  AS within_5e5,
       count(*) FILTER (WHERE abs_eps >  1e-3)  AS BEYOND_A_TENTH_OF_A_CENT,
       round((100.0 * count(*) FILTER (WHERE abs_eps <= 5e-7) / count(*))::numeric, 3)
         AS pct_EXACT_within_rounding,
       round(max(abs_eps)::numeric, 9) AS worst_residual
  FROM r;


\echo '== 5. CLASS D RE-TESTED ON FEE-NORMALISED PRICES =='
-- The earlier overlap test compared raw price@size strings, and chain and
-- poll differ systematically by the fee -- so a chain+poll pair could never
-- show an identical fill and "fully disjoint" was an artifact of my own test.
-- This normalises every chain price back to its venue equivalent first.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), n AS (
  SELECT tx_hash, asset, side, source, sh,
         -- invert the wedge: solve p from c = p + 0.05 p (1-p) on a BUY.
         -- 0.05p^2 - 1.05p + c = 0  ->  p = (1.05 - sqrt(1.1025 - 0.2c)) / 0.1
         CASE WHEN source = 'chain' AND side = 'BUY' AND 1.1025 - 0.2 * px >= 0
              THEN round(((1.05 - sqrt(1.1025 - 0.2 * px)) / 0.1)::numeric, 4)
              ELSE round(px::numeric, 4) END AS px_venue
    FROM t
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n, sum(sh) AS shares,
         string_agg(px_venue::text || '@' || round(sh::numeric, 2)::text,
                    '|' ORDER BY px_venue, sh) AS ms
    FROM n GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         string_agg(DISTINCT source, '+' ORDER BY source) AS srcs,
         max(shares) - min(shares) AS sh_gap, max(shares) AS shares_richest,
         sum(shares) AS shares_union,
         (array_agg(ms ORDER BY source))[1] AS ms1,
         (array_agg(ms ORDER BY source))[2] AS ms2
    FROM s GROUP BY 1, 2, 3 HAVING count(*) > 1
)
SELECT srcs, count(*) AS groups,
       count(*) FILTER (WHERE string_to_array(ms1, '|') && string_to_array(ms2, '|'))
         AS share_a_fill_AFTER_normalising,
       count(*) FILTER (WHERE NOT (string_to_array(ms1, '|') && string_to_array(ms2, '|')))
         AS still_disjoint,
       count(*) FILTER (WHERE abs(sh_gap) < 0.01) AS shares_conserve,
       round(sum(shares_richest)::numeric, 0) AS shares_richest,
       round(sum(shares_union)::numeric, 0) AS shares_if_unioned
  FROM g GROUP BY 1 ORDER BY 2 DESC LIMIT 15;


\echo '== 6. IS maker/taker EVER LABELLED BY THE VENUE on his rows? =='
SELECT source, count(*) AS rows,
       count(*) FILTER (WHERE taker IS TRUE) AS taker_true,
       count(*) FILTER (WHERE taker IS FALSE) AS taker_false,
       count(*) FILTER (WHERE taker IS NULL) AS taker_UNRECORDED,
       round((100.0 * count(*) FILTER (WHERE taker IS NULL) / count(*))::numeric, 2) AS pct_null
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
 GROUP BY 1 ORDER BY 2 DESC;


\echo '== 7. GROSS vs NET PAIR ECONOMICS -- does the matched edge survive fees? =='
-- The question the whole mirror turns on. A pair QUOTED under $1.00 is not a
-- pair that COSTS under $1.00. Where chain observed the cash price it is used
-- (fee_source = observed); otherwise the fee is MODELLED at 0.05 p (1-p) and
-- labelled as such, never silently blended.
WITH t AS (
  SELECT t.condition_id, t.outcome_index, t.side, t.tx_hash, t.asset, t.source,
         t.size::float8 AS sh, t.price::float8 AS px, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.outcome_index IN (0, 1) AND t.condition_id IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), leg AS (
  SELECT condition_id, outcome_index, tx_hash, asset,
         max(sh) AS sh,
         max(px) FILTER (WHERE source = 'poll')  AS px_venue,
         max(px) FILTER (WHERE source = 'chain') AS px_cash,
         max(px) AS px_any
    FROM t GROUP BY 1, 2, 3, 4
), e AS (
  SELECT condition_id, outcome_index, sh,
         COALESCE(px_venue, px_any) AS venue_price,
         CASE WHEN px_cash IS NOT NULL THEN px_cash
              ELSE COALESCE(px_venue, px_any)
                   + 0.05 * COALESCE(px_venue, px_any) * (1 - COALESCE(px_venue, px_any))
         END AS effective_cash_price,
         CASE WHEN px_cash IS NOT NULL THEN 'observed' ELSE 'modelled' END AS fee_source
    FROM leg
), m AS (
  SELECT condition_id,
         sum(sh) FILTER (WHERE outcome_index = 0) AS y,
         sum(sh) FILTER (WHERE outcome_index = 1) AS n,
         sum(sh * venue_price) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS v0,
         sum(sh * venue_price) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS v1,
         sum(sh * effective_cash_price) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS c0,
         sum(sh * effective_cash_price) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS c1,
         count(*) FILTER (WHERE fee_source = 'observed') AS legs_observed,
         count(*) AS legs
    FROM e GROUP BY 1
)
SELECT CASE WHEN legs_observed = 0 THEN 'B fee modelled throughout'
            WHEN legs_observed = legs THEN 'A fee observed on every leg'
            ELSE 'C mixed observed and modelled' END AS fee_basis,
       count(*) AS markets,
       round(sum(LEAST(y, n))::numeric, 0) AS matched_sh,
       round(avg(v0 + v1)::numeric, 5) AS GROSS_PAIR_COST,
       round(avg(c0 + c1)::numeric, 5) AS EFFECTIVE_PAIR_COST,
       round(avg(1.0 - (v0 + v1))::numeric, 5) AS gross_matched_edge,
       round(avg(1.0 - (c0 + c1))::numeric, 5) AS NET_MATCHED_EDGE,
       count(*) FILTER (WHERE v0 + v1 < 1.0) AS gross_under_one,
       count(*) FILTER (WHERE c0 + c1 < 1.0) AS NET_UNDER_ONE,
       round((100.0 * count(*) FILTER (WHERE c0 + c1 < 1.0)
              / NULLIF(count(*) FILTER (WHERE v0 + v1 < 1.0), 0))::numeric, 1)
         AS pct_of_gross_winners_surviving,
       round(sum(LEAST(y, n) * (1.0 - (v0 + v1)))::numeric, 0) AS gross_matched_pnl,
       round(sum(LEAST(y, n) * (1.0 - (c0 + c1)))::numeric, 0) AS NET_MATCHED_PNL
  FROM m WHERE y > 0 AND n > 0
 GROUP BY 1 ORDER BY 1;
