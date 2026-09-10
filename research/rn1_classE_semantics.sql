-- ============================================================================
-- CLASS E SEMANTICS, SPLIT BY TRANSACTION COMPLEXITY (2026-09-10, read-only).
--
-- WHAT THE CODE ALREADY ESTABLISHED, so the SQL only has to decide what the
-- code cannot:
--
--   chain (Path A)  price = round(usdc_units / size_units, 6) -- DERIVED from
--                   on-chain USDC and token transfer amounts. chain.py:498
--                   warns that in multi-leg shapes "which one priced this leg
--                   is not decidable here".
--   poll / backfill price = float(raw["price"]) -- the venue Data-API's OWN
--                   stated trade price, verbatim (history.py imports
--                   parse_data_api_trade from poller, so backfill shares the
--                   poll semantics exactly).
--   notional        DERIVED for every source: TradeEvent.notional = size x
--                   price. It is never observed, so "notional differs" is a
--                   restatement of "price differs" and carries no independent
--                   evidence.
--   dedupe_key      sha256 over (tx_hash, asset, side, size, price, ts) at 6
--                   dp. PRICE IS IN THE KEY, so the two paths collapse only
--                   when they agree on price to six decimals -- class E is the
--                   designed collapse failing its own precondition.
--   known already   s1_emitter.py:1650 carries "the venue might store the
--                   HALF_UP price" with a counter s1.abstain.price_variant.
--                   The price-variant duplicate was instrumented in one path
--                   and never generalised.
--
-- THE DISCRIMINATOR THIS FILE ADDS. If chain's price is unreliable because
-- USDC cannot be attributed to a leg, the disagreement must CONCENTRATE in
-- multi-leg transactions. If simple one-asset one-leg transactions show
-- materially the same disagreement, the attribution story is refuted and the
-- cause is something else -- fees, or two genuinely different economic
-- concepts. Statements 1 and 2 split every E group by transaction shape so
-- that test is decisive either way rather than accommodating.
--
-- THE CANONICAL PRICE RULE, WRITTEN DOWN RATHER THAN IMPLIED. Statement 9
-- assigns `price_source` with explicit provenance and NEVER prefers the
-- numerically more precise number:
--
--   single_source        only one path saw it; nothing to choose
--   agreed               all paths agree to 6 dp
--   ambiguous            paths disagree -- UNRESOLVED, excluded from CANONICAL
--                        and sensitivity-tested, never silently picked
--
-- `poll_authoritative` and `chain_authoritative` are deliberately NOT emitted
-- yet. They become available only if statements 1-5 prove which concept is the
-- leg-level execution price, and a rule that guessed first would be the same
-- error this whole exercise exists to avoid.
--
-- Read-only: nine SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. TRANSACTION COMPLEXITY x CLASS -- where does disagreement live? =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px, t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), txc AS (
  SELECT tx_hash, count(DISTINCT asset) AS n_assets,
         count(DISTINCT condition_id) AS n_conditions,
         count(DISTINCT (asset, side)) AS n_legs, count(*) AS n_rows
    FROM t GROUP BY 1
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n, sum(sh) AS shares,
         string_agg(round(px::numeric, 6)::text || '@' || round(sh::numeric, 4)::text,
                    '|' ORDER BY px, sh) AS ms
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n_sources, sum(n) AS rows_total,
         min(n) AS n_min, max(n) AS n_max,
         min(shares) AS sh_min, max(shares) AS sh_max,
         (array_agg(ms ORDER BY source))[1] AS ms1,
         (array_agg(ms ORDER BY source))[2] AS ms2
    FROM s GROUP BY 1, 2, 3
), c AS (
  SELECT g.*, txc.n_assets, txc.n_conditions, txc.n_legs,
         CASE WHEN txc.n_assets = 1 AND txc.n_legs = 1 AND txc.n_rows <= 2
                                                      THEN '1 single asset, single leg'
              WHEN txc.n_conditions = 1 AND txc.n_assets = 1
                                                      THEN '2 one condition, many fills'
              WHEN txc.n_conditions = 1 AND txc.n_assets > 1
                                                      THEN '3 multiple assets, one condition'
              WHEN txc.n_conditions > 1               THEN '4 multiple conditions'
              ELSE                                         '5 other complex' END AS tx_shape,
         CASE WHEN g.n_sources = 1 AND g.rows_total = 1 THEN 'S1 single row'
              WHEN g.n_sources = 1                     THEN 'S2 single-source sweep'
              WHEN ms1 = ms2                           THEN 'A exact duplicate'
              WHEN string_to_array(ms1, '|') <@ string_to_array(ms2, '|')
                OR string_to_array(ms2, '|') <@ string_to_array(ms1, '|')
                                                       THEN 'B subset'
              WHEN abs(sh_max - sh_min) < 0.01 AND n_min <> n_max
                                                       THEN 'C aggregated vs split'
              WHEN abs(sh_max - sh_min) >= 0.01        THEN 'D complementary'
              ELSE                                          'E irreconcilable' END AS class
    FROM g JOIN txc ON txc.tx_hash = g.tx_hash
)
SELECT tx_shape, class, count(*) AS groups,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY tx_shape))::numeric, 2) AS pct_of_shape,
       round(avg(n_assets)::numeric, 2) AS avg_assets, round(avg(n_legs)::numeric, 2) AS avg_legs
  FROM c GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 2. THE DISCRIMINATOR: E price disagreement by transaction shape =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.condition_id,
         t.size::float8 AS sh, t.price::float8 AS px
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), txc AS (
  SELECT tx_hash, count(DISTINCT asset) AS n_assets,
         count(DISTINCT condition_id) AS n_conditions,
         count(DISTINCT (asset, side)) AS n_legs, count(*) AS n_rows
    FROM t GROUP BY 1
), p AS (
  SELECT tx_hash, asset, side,
         count(DISTINCT source) AS n_sources, count(*) AS rows_total,
         sum(sh) FILTER (WHERE source = 'chain') AS sh_chain,
         sum(sh) FILTER (WHERE source = 'poll')  AS sh_poll,
         max(px) FILTER (WHERE source = 'chain') AS px_chain,
         max(px) FILTER (WHERE source = 'poll')  AS px_poll,
         max(px) FILTER (WHERE source = 's1')    AS px_s1,
         max(px) FILTER (WHERE source = 'backfill') AS px_bf
    FROM t GROUP BY 1, 2, 3
), e AS (
  SELECT p.*, txc.n_assets, txc.n_legs, txc.n_conditions,
         CASE WHEN txc.n_assets = 1 AND txc.n_legs = 1 AND txc.n_rows <= 2
                                                      THEN '1 single asset, single leg'
              WHEN txc.n_conditions = 1 AND txc.n_assets = 1
                                                      THEN '2 one condition, many fills'
              WHEN txc.n_conditions = 1 AND txc.n_assets > 1
                                                      THEN '3 multiple assets, one condition'
              WHEN txc.n_conditions > 1               THEN '4 multiple conditions'
              ELSE                                         '5 other complex' END AS tx_shape,
         abs(px_chain - px_poll) AS dpx,
         CASE WHEN px_poll > 0 THEN 10000.0 * abs(px_chain - px_poll) / px_poll END AS bps
    FROM p JOIN txc ON txc.tx_hash = p.tx_hash
   WHERE px_chain IS NOT NULL AND px_poll IS NOT NULL
     AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01
)
SELECT tx_shape, count(*) AS chain_poll_pairs,
       count(*) FILTER (WHERE dpx < 0.000001) AS prices_agree,
       count(*) FILTER (WHERE dpx >= 0.000001) AS PRICES_DISAGREE,
       round((100.0 * count(*) FILTER (WHERE dpx >= 0.000001) / NULLIF(count(*), 0))::numeric, 2)
         AS disagree_pct,
       count(*) FILTER (WHERE px_chain > px_poll) AS chain_higher,
       count(*) FILTER (WHERE px_poll > px_chain) AS poll_higher,
       round((percentile_cont(0.50) WITHIN GROUP (ORDER BY dpx))::numeric, 6) AS dpx_p50,
       round((percentile_cont(0.90) WITHIN GROUP (ORDER BY dpx))::numeric, 6) AS dpx_p90,
       round((percentile_cont(0.95) WITHIN GROUP (ORDER BY dpx))::numeric, 6) AS dpx_p95,
       round((percentile_cont(0.99) WITHIN GROUP (ORDER BY dpx))::numeric, 6) AS dpx_p99,
       round(max(dpx)::numeric, 6) AS dpx_max,
       round((percentile_cont(0.50) WITHIN GROUP (ORDER BY bps))::numeric, 1) AS bps_p50,
       round((percentile_cont(0.95) WITHIN GROUP (ORDER BY bps))::numeric, 1) AS bps_p95
  FROM e GROUP BY ROLLUP (tx_shape) ORDER BY 1 NULLS LAST;


\echo '== 3. PRECISION PROFILE: how many decimals does each source carry? =='
SELECT source,
       count(*) AS rows,
       count(*) FILTER (WHERE abs(price * 100   - round(price * 100))   < 1e-9) AS exactly_2dp,
       count(*) FILTER (WHERE abs(price * 1000  - round(price * 1000))  < 1e-9) AS within_3dp,
       count(*) FILTER (WHERE abs(price * 10000 - round(price * 10000)) < 1e-9) AS within_4dp,
       round((100.0 * count(*) FILTER (WHERE abs(price * 100 - round(price * 100)) < 1e-9)
              / NULLIF(count(*), 0))::numeric, 2) AS pct_2dp,
       round(min(price)::numeric, 6) AS px_min, round(max(price)::numeric, 6) AS px_max
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
 GROUP BY 1 ORDER BY 2 DESC;


\echo '== 4. IS POLL A ROUNDING OF CHAIN? prove or reject =='
-- If E is display rounding, poll must equal chain rounded to some fixed
-- precision. The worked example (chain 0.522495, poll 0.51) says no --
-- round(0.522495, 2) is 0.52 -- and this measures how general that is.
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
)
SELECT count(*) AS chain_poll_pairs,
       count(*) FILTER (WHERE abs(px_chain - px_poll) < 1e-9) AS identical,
       count(*) FILTER (WHERE abs(round(px_chain::numeric, 2) - px_poll::numeric) < 1e-9)
         AS poll_is_chain_rounded_2dp,
       count(*) FILTER (WHERE abs(round(px_chain::numeric, 3) - px_poll::numeric) < 1e-9)
         AS poll_is_chain_rounded_3dp,
       count(*) FILTER (WHERE abs(floor(px_chain * 100)::numeric / 100 - px_poll::numeric) < 1e-9)
         AS poll_is_chain_floored_2dp,
       count(*) FILTER (WHERE abs(px_chain - px_poll) >= 0.005) AS gap_over_half_a_cent,
       count(*) FILTER (WHERE abs(px_chain - px_poll) >= 0.01) AS GAP_OVER_A_CENT,
       round(avg(px_chain - px_poll)::numeric, 6) AS mean_signed_gap,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY px_chain - px_poll))::numeric, 6)
         AS median_signed_gap
  FROM p
 WHERE px_chain IS NOT NULL AND px_poll IS NOT NULL
   AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01;


\echo '== 5. THE FEE HYPOTHESIS: is chain/poll a CONSTANT ratio? =='
-- A fee wedge is multiplicative and near-constant. Mis-attributed USDC is
-- not. This bands the ratio so a spike at one value would name a fee and a
-- broad scatter would refuse it.
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
), r AS (
  SELECT px_chain / NULLIF(px_poll, 0) AS ratio, px_poll, px_chain
    FROM p
   WHERE px_chain IS NOT NULL AND px_poll > 0
     AND abs(COALESCE(sh_chain, 0) - COALESCE(sh_poll, 0)) < 0.01
)
SELECT CASE WHEN ratio < 0.98        THEN '1 chain 2%+ below poll'
            WHEN ratio < 0.999       THEN '2 chain slightly below'
            WHEN ratio <= 1.001      THEN '3 within 0.1%'
            WHEN ratio <= 1.02       THEN '4 chain up to 2% above'
            WHEN ratio <= 1.06       THEN '5 chain 2-6% above'
            ELSE                          '6 chain 6%+ above' END AS ratio_band,
       count(*) AS pairs,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct,
       round(avg(ratio)::numeric, 6) AS avg_ratio,
       round(stddev_samp(ratio)::numeric, 6) AS sd_ratio,
       round(avg(px_poll)::numeric, 4) AS avg_poll_px
  FROM r GROUP BY 1 ORDER BY 1;


\echo '== 6. TIMING DECOMPOSITION: exchange time to each detection stage =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.ts, t.detected_at,
         t.price::float8 AS px, t.size::float8 AS sh
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.detected_at IS NOT NULL
     AND t.ts >= now() - interval '30 days'
), g AS (
  SELECT tx_hash, asset, side, min(ts) AS exchange_ts,
         min(detected_at) AS first_any_detection_at,
         max(detected_at) AS first_full_transaction_detection_at,
         count(*) AS rows_total, count(DISTINCT source) AS n_sources
    FROM t GROUP BY 1, 2, 3
)
SELECT CASE WHEN rows_total = 1 THEN '1 one observation'
            WHEN n_sources = 1  THEN '2 one source, several rows'
            ELSE                     '3 several sources' END AS shape,
       count(*) AS groups,
       round((percentile_cont(0.50) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_any_detection_at - exchange_ts))))::numeric, 1) AS first_any_p50,
       round((percentile_cont(0.90) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_any_detection_at - exchange_ts))))::numeric, 1) AS first_any_p90,
       round((percentile_cont(0.99) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_any_detection_at - exchange_ts))))::numeric, 1) AS first_any_p99,
       round((percentile_cont(0.50) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_full_transaction_detection_at - exchange_ts))))::numeric, 1) AS full_p50,
       round((percentile_cont(0.90) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_full_transaction_detection_at - exchange_ts))))::numeric, 1) AS full_p90,
       round((percentile_cont(0.99) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_full_transaction_detection_at - exchange_ts))))::numeric, 1) AS full_p99,
       round((percentile_cont(0.50) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_full_transaction_detection_at
                                      - first_any_detection_at))))::numeric, 1) AS LOOKAHEAD_WINDOW_p50,
       round((percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM (first_full_transaction_detection_at
                                      - first_any_detection_at))))::numeric, 1) AS lookahead_p95
  FROM g GROUP BY 1 ORDER BY 1;


\echo '== 7. CLASS C: which source is finer, and did it arrive first or later? =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.ts, t.detected_at,
         t.size::float8 AS sh, t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n, sum(sh) AS shares, sum(usd) AS usd,
         min(detected_at) AS det
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n_sources,
         max(n) AS n_fine, min(n) AS n_coarse,
         max(shares) - min(shares) AS sh_gap, max(usd) - min(usd) AS usd_gap,
         (array_agg(source ORDER BY n DESC, source))[1] AS finer_source,
         (array_agg(source ORDER BY n ASC, source))[1] AS coarser_source,
         (array_agg(det ORDER BY n DESC, source))[1] AS finer_det,
         (array_agg(det ORDER BY n ASC, source))[1] AS coarser_det
    FROM s GROUP BY 1, 2, 3
   HAVING count(*) > 1 AND max(n) <> min(n)
      AND abs(max(shares) - min(shares)) < 0.01 AND abs(max(usd) - min(usd)) < 0.01
)
SELECT finer_source, coarser_source, count(*) AS groups,
       round(avg(n_fine)::numeric, 2) AS avg_fills_fine,
       round(avg(n_coarse)::numeric, 2) AS avg_fills_coarse,
       count(*) FILTER (WHERE finer_det <= coarser_det) AS FINER_ARRIVED_FIRST,
       count(*) FILTER (WHERE finer_det > coarser_det) AS finer_arrived_later,
       round((percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM (finer_det - coarser_det))))::numeric, 1) AS median_delay_to_finer_s,
       round((percentile_cont(0.9) WITHIN GROUP (
         ORDER BY extract(epoch FROM (finer_det - coarser_det))))::numeric, 1) AS p90_delay_s
  FROM g GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;


\echo '== 8. CLASS D: do the partial sources OVERLAP, or are they disjoint? =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px,
         t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n, sum(sh) AS shares, sum(usd) AS usd,
         string_agg(round(px::numeric, 6)::text || '@' || round(sh::numeric, 4)::text,
                    '|' ORDER BY px, sh) AS ms
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side,
         string_agg(DISTINCT source, '+' ORDER BY source) AS srcs,
         max(shares) - min(shares) AS sh_gap,
         sum(shares) AS union_shares_if_disjoint, max(shares) AS shares_richest,
         sum(usd) AS union_usd_if_disjoint, max(usd) AS usd_richest,
         (array_agg(ms ORDER BY source))[1] AS ms1,
         (array_agg(ms ORDER BY source))[2] AS ms2
    FROM s GROUP BY 1, 2, 3 HAVING count(*) > 1
)
SELECT srcs, count(*) AS groups,
       count(*) FILTER (WHERE string_to_array(ms1, '|') && string_to_array(ms2, '|'))
         AS SHARE_AN_IDENTICAL_FILL,
       count(*) FILTER (WHERE NOT (string_to_array(ms1, '|') && string_to_array(ms2, '|')))
         AS fully_disjoint,
       round(avg(sh_gap)::numeric, 2) AS avg_share_gap,
       round(sum(shares_richest)::numeric, 0) AS shares_if_richest_wins,
       round(sum(union_shares_if_disjoint)::numeric, 0) AS shares_if_unioned,
       round(sum(usd_richest)::numeric, 0) AS usd_if_richest_wins,
       round(sum(union_usd_if_disjoint)::numeric, 0) AS usd_if_unioned
  FROM g WHERE abs(sh_gap) >= 0.01
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;


\echo '== 9. THE CANONICAL PRICE RULE, with provenance and no silent choice =='
-- price_source is assigned by PROVENANCE, never by which number has more
-- decimals. `ambiguous` is a terminal state here: those groups are excluded
-- from CANONICAL and sensitivity-tested in sections 3 and 4 rather than
-- resolved by preference. poll_authoritative / chain_authoritative are NOT
-- emitted until statements 1-5 prove which concept is the leg-level
-- execution price.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.size::float8 AS sh, t.price::float8 AS px,
         t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), s AS (
  SELECT tx_hash, asset, side, source, count(*) AS n, sum(sh) AS shares, sum(usd) AS usd,
         string_agg(round(px::numeric, 6)::text || '@' || round(sh::numeric, 4)::text,
                    '|' ORDER BY px, sh) AS ms
    FROM t GROUP BY 1, 2, 3, 4
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n_sources, sum(n) AS rows_total,
         max(usd) AS usd_richest, sum(usd) AS usd_all,
         (array_agg(ms ORDER BY source))[1] AS ms1,
         (array_agg(ms ORDER BY source))[2] AS ms2
    FROM s GROUP BY 1, 2, 3
)
SELECT CASE WHEN n_sources = 1  THEN 'single_source'
            WHEN ms1 = ms2      THEN 'agreed'
            ELSE                     'ambiguous' END AS price_source,
       CASE WHEN n_sources = 1  THEN 'only one path observed this leg'
            WHEN ms1 = ms2      THEN 'all paths agree to 6 dp'
            ELSE                     'paths disagree; held out of CANONICAL' END AS provenance,
       count(*) AS groups, sum(rows_total) AS observation_rows,
       round(sum(usd_richest)::numeric, 0) AS notional_richest_source,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_groups,
       round((100.0 * sum(usd_richest) / sum(sum(usd_richest)) OVER ())::numeric, 2) AS pct_notional
  FROM g GROUP BY 1, 2 ORDER BY 1;
