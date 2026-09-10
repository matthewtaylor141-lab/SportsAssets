-- ============================================================================
-- THREE CONTAMINATION CHECKS before sections 3 and 4 (2026-09-10, read-only).
--
-- ---------------------------------------------------------------------------
-- CHECK 1: IS (tx_hash, asset, side) A LEGITIMATE UNIQUE-FILL KEY?
--
-- Our table holds 946,198 RN1 rows against 880,157 distinct
-- (tx_hash, asset, side) groups -- 66,041 rows, 7.0%, share a key with
-- another row. D1 collapses those on the reasoning that the chain listener
-- and the poller report the SAME execution twice (Kostyuk read 92,145 against
-- the venue's 55,993). The chain path starts 2026-08-10 and the poller runs
-- concurrently, so the overlap window is exactly where a double count would
-- appear, and 2026-08 is our largest month.
--
-- THE SECTION 5 / 6 QUERIES READ RAW ROWS WITH NO DEDUPE. So if those 66,041
-- are one execution seen twice, every figure I reported is inflated; and if
-- they are genuinely distinct executions, D1 is destroying real fills in the
-- LIVE path. Both cannot be true and the answer is observable.
--
-- The discriminator is whether the collapsed rows carry the SAME economics.
-- Two reports of one fill must agree on price and size; two distinct fills in
-- one transaction generally will not. Statements 1 to 4 measure multiplicity,
-- then whether the members of a group differ in price, in size, in timestamp,
-- and in source -- and print notional before and after so the effect is a
-- dollar figure rather than a row count.
--
-- ---------------------------------------------------------------------------
-- CHECK 2: WINDOW COMPLETENESS.
--
-- Months 2026-02 through 2026-06 hold NO RN1 rows, 2026-01 holds 10 days and
-- 2026-07 holds 5. A 90-day window ending 2026-09-10 reaches back to
-- 2026-06-12 and crosses that hole, so the long horizons are not comparable
-- to the short ones and the decay I described may be an artifact of coverage.
-- Statement 5 prints requested start and end, days expected, days with any
-- data, missing days and a COMPLETE / PARTIAL verdict for every horizon
-- previously reported.
--
-- ---------------------------------------------------------------------------
-- CHECK 3: EVENT-LEVEL EXPOSURE, KEPT SEPARATE FROM THE MATCHED BOOK.
--
-- Every condition is a binary YES/NO proposition (proved: 32,582 settled
-- conditions, all two-slot, all summing to 1.0000), and Home / Draw / Away is
-- THREE binary conditions. So 48.2% of his dollars sit on events where he
-- holds more than one condition, and a per-condition reading cannot see a
-- hedge spread across them.
--
-- YES-Home plus YES-Draw is NOT the same as YES plus NO on one condition: it
-- is a partial hedge, because both lose when Away wins. So these are reported
-- SEPARATELY and are NEVER added to the binary matched book:
--
--   binary guaranteed matched   min(Y, N) within one condition -- the report's
--                               metric, unchanged
--   event partially hedged      several conditions of one event, minimum
--                               terminal payout could still be zero
--   event fully covered         cost strictly below the guaranteed return --
--                               reported only where provable
--   naked directional           one condition, one side
--
-- WHAT WE CANNOT DO, STATED PLAINLY: a true payoff-vector treatment needs the
-- event's complete outcome space, and we do not store which conditions of an
-- event partition it. We can see that he holds N conditions of one event and
-- what each settled at; we cannot prove those N exhaust the outcomes. So
-- statement 7 reports event-level cost against event-level realised return
-- and counts the same-side multi-condition holdings that are the partial-hedge
-- signature -- and the fully-covered class stays UNKNOWN rather than guessed.
--
-- Read-only: seven SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. DEDUPE: multiplicity of (tx_hash, asset, side) =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size::float8 AS sh, t.price::float8 AS px,
         t.notional::float8 AS usd, t.ts, t.source, t.id
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n,
         count(DISTINCT px) AS distinct_px,
         count(DISTINCT sh) AS distinct_sh,
         count(DISTINCT ts) AS distinct_ts,
         count(DISTINCT source) AS distinct_source,
         sum(usd) AS usd, max(usd) AS usd_max,
         max(px) - min(px) AS px_spread, max(sh) - min(sh) AS sh_spread,
         extract(epoch FROM (max(ts) - min(ts))) AS ts_spread_s
    FROM t GROUP BY 1, 2, 3
)
SELECT CASE WHEN n = 1 THEN '1 unique' WHEN n = 2 THEN '2 pair'
            WHEN n = 3 THEN '3 triple' WHEN n <= 5 THEN '4 four or five'
            ELSE '5 six or more' END AS multiplicity,
       count(*) AS groups,
       sum(n) AS rows_in_them,
       sum(n) - count(*) AS ROWS_A_COLLAPSE_WOULD_REMOVE,
       round(sum(usd)::numeric, 0) AS notional_before,
       round(sum(usd_max)::numeric, 0) AS notional_if_collapsed_to_max,
       count(*) FILTER (WHERE distinct_px > 1) AS groups_differing_in_PRICE,
       count(*) FILTER (WHERE distinct_sh > 1) AS groups_differing_in_SIZE,
       count(*) FILTER (WHERE distinct_ts > 1) AS groups_differing_in_TIME,
       count(*) FILTER (WHERE distinct_source > 1) AS groups_spanning_SOURCES
  FROM g GROUP BY 1 ORDER BY 1;


\echo '== 2. DEDUPE: are the collapsed rows economically IDENTICAL? =='
-- If two reports of one fill, price and size agree exactly. If two distinct
-- executions inside one transaction, they generally will not.
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.size::float8 AS sh, t.price::float8 AS px,
         t.notional::float8 AS usd, t.ts, t.source
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n,
         count(DISTINCT px) AS dpx, count(DISTINCT sh) AS dsh,
         count(DISTINCT source) AS dsrc,
         sum(usd) AS usd, max(usd) AS usd_max,
         max(px) - min(px) AS px_gap, max(sh) - min(sh) AS sh_gap,
         extract(epoch FROM (max(ts) - min(ts))) AS ts_gap
    FROM t GROUP BY 1, 2, 3 HAVING count(*) > 1
)
SELECT CASE WHEN dpx = 1 AND dsh = 1 THEN '1 identical price AND size'
            WHEN dpx = 1 AND dsh > 1 THEN '2 same price, DIFFERENT size'
            WHEN dpx > 1 AND dsh = 1 THEN '3 DIFFERENT price, same size'
            ELSE                          '4 DIFFERENT price and size' END AS shape,
       count(*) AS groups, sum(n) AS rows,
       sum(n) - count(*) AS rows_a_collapse_removes,
       round(sum(usd)::numeric, 0) AS notional_before,
       round(sum(usd_max)::numeric, 0) AS notional_after_collapse,
       round(sum(usd - usd_max)::numeric, 0) AS NOTIONAL_A_COLLAPSE_REMOVES,
       count(*) FILTER (WHERE dsrc > 1) AS spanning_two_sources,
       round(avg(px_gap)::numeric, 6) AS avg_price_gap,
       round(avg(sh_gap)::numeric, 2) AS avg_size_gap,
       round(avg(ts_gap)::numeric, 1) AS avg_seconds_apart
  FROM g GROUP BY 1 ORDER BY 1;


\echo '== 3. DEDUPE: which SOURCE PAIRS collapse together? =='
WITH t AS (
  SELECT t.tx_hash, t.asset, t.side, t.source, t.notional::float8 AS usd, t.ts
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), g AS (
  SELECT tx_hash, asset, side, count(*) AS n,
         string_agg(DISTINCT source, '+' ORDER BY source) AS srcs,
         sum(usd) AS usd, max(usd) AS usd_max,
         min(ts)::date AS d
    FROM t GROUP BY 1, 2, 3 HAVING count(*) > 1
)
SELECT srcs AS source_combination, count(*) AS groups, sum(n) AS rows,
       round(sum(usd)::numeric, 0) AS notional_before,
       round(sum(usd - usd_max)::numeric, 0) AS notional_removed,
       min(d) AS first_day, max(d) AS last_day
  FROM g GROUP BY 1 ORDER BY 2 DESC LIMIT 15;


\echo '== 4. DEDUPE: does it materially change matched / residual? BOTH ways =='
-- Section 5 recomputed on RAW rows and on DEDUPED rows side by side. If the
-- two agree the audit is closed; if they do not, every earlier figure is
-- restated. 30-day window, settled markets, two-way conditions.
WITH raw AS (
  SELECT t.condition_id, t.outcome_index, t.side, t.size::float8 AS sh,
         t.notional::float8 AS usd, t.ts, t.tx_hash, t.asset,
         row_number() OVER (PARTITION BY t.tx_hash, t.asset, t.side
                            ORDER BY CASE t.source WHEN 'chain' THEN 0 WHEN 'poll' THEN 1
                                                   WHEN 'backfill' THEN 2 ELSE 3 END, t.id) AS rn
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.ts >= now() - interval '30 days'
), agg AS (
  SELECT mode_label, condition_id,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS b0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS b1,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 0 AND side = 'BUY'), 0) AS c0,
         COALESCE(sum(usd) FILTER (WHERE outcome_index = 1 AND side = 'BUY'), 0) AS c1,
         COALESCE(sum(usd) FILTER (WHERE side = 'SELL'), 0) AS proceeds,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 0 AND side = 'SELL'), 0) AS x0,
         COALESCE(sum(sh)  FILTER (WHERE outcome_index = 1 AND side = 'SELL'), 0) AS x1,
         count(*) AS fills
    FROM (SELECT 'A raw' AS mode_label, * FROM raw
          UNION ALL
          SELECT 'B deduped' AS mode_label, * FROM raw WHERE rn = 1) u
   GROUP BY 1, 2
), g AS (
  SELECT agg.mode_label, agg.fills,
         (agg.b0 - agg.x0) AS y, (agg.b1 - agg.x1) AS n,
         (agg.c0 + agg.c1 - agg.proceeds) AS net_cost,
         LEAST(GREATEST(agg.b0 - agg.x0, 0), GREATEST(agg.b1 - agg.x1, 0)) AS matched,
         (COALESCE(CASE WHEN agg.b0 > 0 THEN agg.c0 / agg.b0 END, 0)
          + COALESCE(CASE WHEN agg.b1 > 0 THEN agg.c1 / agg.b1 END, 0)) AS pair_cost,
         ((agg.b0 - agg.x0) * (m.resolved_prices ->> 0)::float8
          + (agg.b1 - agg.x1) * (m.resolved_prices ->> 1)::float8
          - (agg.c0 + agg.c1 - agg.proceeds)) AS total_pnl
    FROM agg JOIN markets m ON m.condition_id = agg.condition_id
   WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
)
SELECT mode_label, count(*) AS markets, sum(fills) AS fills,
       round(sum(net_cost)::numeric, 0) AS total_cost,
       round(sum(y + n)::numeric, 0) AS shares_held,
       round(sum(matched)::numeric, 0) AS matched_qty,
       round(avg(pair_cost) FILTER (WHERE matched > 0)::numeric, 5) AS avg_pair_cost,
       round(sum(total_pnl)::numeric, 0) AS total_pnl,
       round(sum(matched * (1.0 - pair_cost))::numeric, 0) AS matched_pnl,
       round(sum(total_pnl - matched * (1.0 - pair_cost))::numeric, 0) AS directional_pnl,
       round((100.0 * sum(total_pnl - matched * (1.0 - pair_cost))
              / NULLIF(sum(net_cost - matched * pair_cost), 0))::numeric, 2) AS directional_roi,
       round((100.0 * sum(matched * (1.0 - pair_cost))
              / NULLIF(sum(matched * pair_cost), 0))::numeric, 2) AS matched_roi
  FROM g GROUP BY 1 ORDER BY 1;


\echo '== 5. WINDOW COMPLETENESS for every horizon previously reported =='
WITH horizons(label, days) AS (
  VALUES ('1 48 hours', 2), ('2 7 days', 7), ('3 14 days', 14), ('4 21 days', 21),
         ('5 30 days', 30), ('6 90 days', 90), ('7 available history', 800)
), f AS (
  SELECT t.ts::date AS d, t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1'
), h AS (
  SELECT label, days,
         (now() - (days * interval '1 day'))::date AS requested_start,
         now()::date AS requested_end,
         LEAST(days, (now()::date - (SELECT min(d) FROM f)))::int AS days_expected
    FROM horizons
)
SELECT h.label AS horizon, h.requested_start, h.requested_end, h.days_expected,
       count(DISTINCT f.d) AS days_with_any_data,
       h.days_expected - count(DISTINCT f.d) AS missing_days,
       count(f.*) AS fills, round(sum(f.usd)::numeric, 0) AS cost,
       round((100.0 * count(DISTINCT f.d) / NULLIF(h.days_expected, 0))::numeric, 1) AS coverage_pct,
       CASE WHEN count(DISTINCT f.d) >= h.days_expected - 1 THEN 'COMPLETE'
            ELSE 'PARTIAL' END AS coverage_status
  FROM h LEFT JOIN f ON f.d >= h.requested_start
 GROUP BY h.label, h.requested_start, h.requested_end, h.days_expected
 ORDER BY 1;


\echo '== 6. EVENT-LEVEL: how many conditions, and same-side or opposed? =='
-- The partial-hedge signature is holding several conditions of one event.
-- Whether that reduces risk depends on the outcome space, which we do not
-- store -- so this counts the shape and stops there.
WITH f AS (
  SELECT t.event_slug, t.condition_id, t.outcome_index, t.sport,
         t.size::float8 AS sh, t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.event_slug IS NOT NULL AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.ts >= now() - interval '30 days'
), c AS (
  SELECT event_slug, condition_id, min(sport) AS sport,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS y,
         COALESCE(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS n,
         sum(usd) AS usd
    FROM f GROUP BY 1, 2
), e AS (
  SELECT event_slug, min(sport) AS sport, count(*) AS conditions,
         sum(usd) AS usd,
         sum(LEAST(y, n)) AS binary_matched_sh,
         sum(GREATEST(y - n, 0)) AS surplus_yes_sh,
         sum(GREATEST(n - y, 0)) AS surplus_no_sh,
         count(*) FILTER (WHERE y > 0 AND n > 0) AS conds_with_both_sides,
         count(*) FILTER (WHERE y > 0 AND n = 0) AS conds_yes_only,
         count(*) FILTER (WHERE n > 0 AND y = 0) AS conds_no_only
    FROM c GROUP BY 1
)
SELECT COALESCE(sport, 'ALL') AS sport,
       CASE WHEN conditions = 1 THEN '1 single condition (clean)'
            WHEN conds_yes_only >= 2 THEN '2 multi-condition, TWO+ YES-only legs'
            WHEN conditions > 1 THEN '3 multi-condition, mixed'
            ELSE '4 other' END AS event_shape,
       count(*) AS events,
       round(sum(usd)::numeric, 0) AS usd,
       round((100.0 * sum(usd) / sum(sum(usd)) OVER (PARTITION BY sport))::numeric, 1) AS pct_usd,
       round(sum(binary_matched_sh)::numeric, 0) AS binary_matched_sh,
       round(sum(surplus_yes_sh + surplus_no_sh)::numeric, 0) AS unmatched_sh,
       round(avg(conditions)::numeric, 2) AS avg_conditions
  FROM e GROUP BY ROLLUP (sport), 2 ORDER BY sport NULLS FIRST, 2 LIMIT 45;


\echo '== 7. EVENT-LEVEL REALISED: does per-condition attribution mislead? =='
-- Event-level cost against event-level realised return, beside the sum of the
-- per-condition binary matched P&L. Where the two diverge, per-condition
-- attribution is describing something other than his real exposure.
WITH f AS (
  SELECT t.event_slug, t.condition_id, t.outcome_index, t.sport,
         t.size::float8 AS sh, t.notional::float8 AS usd
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.event_slug IS NOT NULL AND t.condition_id IS NOT NULL
     AND t.outcome_index IN (0, 1) AND t.ts >= now() - interval '30 days'
), c AS (
  SELECT f.event_slug, f.condition_id, min(f.sport) AS sport,
         COALESCE(sum(f.sh)  FILTER (WHERE f.outcome_index = 0), 0) AS y,
         COALESCE(sum(f.sh)  FILTER (WHERE f.outcome_index = 1), 0) AS n,
         COALESCE(sum(f.usd) FILTER (WHERE f.outcome_index = 0), 0) AS cy,
         COALESCE(sum(f.usd) FILTER (WHERE f.outcome_index = 1), 0) AS cn,
         (m.resolved_prices ->> 0)::float8 AS p0,
         (m.resolved_prices ->> 1)::float8 AS p1
    FROM f JOIN markets m ON m.condition_id = f.condition_id
   WHERE m.resolved AND jsonb_typeof(m.resolved_prices) = 'array'
   GROUP BY f.event_slug, f.condition_id, m.resolved_prices
), e AS (
  SELECT event_slug, min(sport) AS sport, count(*) AS conditions,
         sum(cy + cn) AS cost,
         sum(y * p0 + n * p1) AS realised,
         sum(LEAST(y, n) * (1.0 - (COALESCE(CASE WHEN y > 0 THEN cy / y END, 0)
                                   + COALESCE(CASE WHEN n > 0 THEN cn / n END, 0)))) AS binary_matched_pnl
    FROM c GROUP BY 1
)
SELECT COALESCE(sport, 'ALL') AS sport,
       CASE WHEN conditions = 1 THEN 'A single condition' ELSE 'B multi-condition' END AS shape,
       count(*) AS events,
       round(sum(cost)::numeric, 0) AS event_cost,
       round(sum(realised - cost)::numeric, 0) AS event_pnl,
       round((100.0 * sum(realised - cost) / NULLIF(sum(cost), 0))::numeric, 2) AS event_roi_pct,
       round(sum(binary_matched_pnl)::numeric, 0) AS binary_matched_pnl,
       round(sum(realised - cost - binary_matched_pnl)::numeric, 0) AS residual_after_binary,
       count(*) FILTER (WHERE realised >= cost) AS events_that_returned_cost
  FROM e GROUP BY ROLLUP (sport), 2 ORDER BY sport NULLS FIRST, 2 LIMIT 30;
