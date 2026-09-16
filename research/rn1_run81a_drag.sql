-- RUN 81A -- FIRST_RETAINED_OBSERVATION_DRAG
--
-- PRICE DETERIORATION OBSERVED AT THE FIRST RETAINED EXACT BOOK OBSERVATION.
--
-- Read-only. Nine SELECTs. No writes, no money path. No settlement, no
-- resolved_prices, no ai_trades, no TRUEEDGE. Run 81B does not follow.
-- mirror_live=false.
--
-- NOTHING HERE IS A LATENCY COST. No figure in this file is produced by, named
-- after, or divided by an elapsed time. Estimator B is not in this file at all.
--
-- AUDIT_CUTOFF_TS = 2026-09-12T00:00:00Z, applied per table on that table's
-- designated field: trades on ts AND detected_at, copy_probes on probe_at.
--
-- ================= CORRECTION 1, APPLIED THROUGHOUT =================
-- MISSING DEPTH DOES NOT BOUND ANYTHING. My previous design called a full-q
-- figure built on exhausted depth an UPPER BOUND. That was wrong and is
-- withdrawn. If requested q exceeds the retained top-8 ask depth, full-q
-- executable VWAP is NOT IDENTIFIABLE FROM RETAINED DEPTH: the retained rows do
-- not say whether the remaining quantity was executable at all, at what prices,
-- or what the full-q VWAP or dollar drag would have been. An ascending ask book
-- makes the observed prefix a statement about the cost of the part we can see,
-- conditional on continuing to buy -- it identifies neither the rest nor the
-- whole.
--
-- So every sizing scenario reports two disjoint sets, and never one number
-- across both:
--     FULLY_DEPTH_SUPPORTED   depth_shares >= requested shares
--     DEPTH_EXHAUSTED         depth_shares <  requested shares
-- DEPTH_EXHAUSTED rows are never extrapolated from the last observed price,
-- never imputed a VWAP, and never folded into an aggregate as if known. They
-- are printed with their own count, source notional, requested shares, observed
-- shares and coverage fraction (statement 7).
--
-- Every aggregate built only on supported events is labelled
-- DEPTH_SUPPORTED_SUBSET and prints its retention against the parent cohort.
--
-- Unknown FEES may make a REMAINING_MARGIN an upper bound. That is an 81B
-- concept, it is a different mechanism, and the two are not conflated here.
--
-- ================= CORRECTION 2, APPLIED THROUGHOUT =================
-- q IS IN SHARES, DEFINED ONCE, PRE-REGISTERED:
--     Q_A_SHARES = 0.10 * RN1_FILL_SHARES     (trades.size)
--     Q_B_SHARES = RN1_FILL_SHARES
--     Q_C_SHARES = 1000 / RN1_SOURCE_PRICE    (~$1,000 of SOURCE-price exposure)
-- These are SENSITIVITY SCENARIOS. None is "the BETTOR size" and none is named
-- that anywhere in this file.
--
-- NOTE A DELIBERATE DIFFERENCE FROM RUN 80. Run 80's depth census tested
-- exhaustion in DOLLARS (depth_usd < 0.10*notional, < 1000). This file tests it
-- in SHARES against the pre-registered share quantities above. At the source
-- price the two agree; above it they do not, because 1000/p_h shares cost more
-- than $1,000 once the ask sits above p_h. The share definition is the one the
-- equations use, so it is the one used, and run 80's dollar figures are NOT
-- carried forward as if they were these.
--
-- ================= TWO DIFFERENT PRIMARY POPULATIONS =================
-- TOP_OF_BOOK_MOVE needs no depth. It is therefore reported on EVERY eligible
-- U2 row with a valid best ask (statement 3). The depth requirement must never
-- silently change the population for the top-of-book statistic, so the
-- selection from U2 -> valid best ask -> fully depth supported at q is printed
-- explicitly, per lane, for every q (statement 2).
--
-- That separation is the point: it distinguishes
--     "the market had already moved"                  (top of book, all rows)
-- from
--     "our proposed size would have walked the book"  (depth component)
--
-- p_h IS trades.price -- RN1's own recorded fill price on the trade row.
-- copy_probes.his_price is a SECOND recording of the same quantity and is used
-- only as a cross-check (statement 1), never as the primary.
--
-- NEGATIVE DRAG STAYS NEGATIVE. Nothing is floored at zero anywhere in this
-- file. A row where the book had moved in our favour is evidence and is carried
-- with its sign.

\echo '== RUN 81A -- FIRST_RETAINED_OBSERVATION_DRAG =='
\echo '== PRICE DETERIORATION AT THE FIRST RETAINED EXACT BOOK OBSERVATION =='
\echo '== No settlement. No latency. No ai_trades. mirror_live=false. =='
\echo ''
\echo '== 0. THE PINNED CUTOFF AND ITS TWO STANDING CONTROLS =='
-- U0 is the drift-free control run 80 established (962,509 at the cutoff, re-read
-- identical). U2 is run 80's probe-backed population (214,708). If either has
-- moved, say so here rather than discovering it in an aggregate later.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), u0 AS MATERIALIZED (
  SELECT id FROM trades
   WHERE whale_id IN (SELECT id FROM rn1)
     AND ts <= timestamptz '2026-09-12 00:00:00+00'
     AND detected_at <= timestamptz '2026-09-12 00:00:00+00'
), u2 AS MATERIALIZED (
  SELECT t.id FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
)
SELECT timestamptz '2026-09-12 00:00:00+00'                    AS audit_cutoff_ts,
       (SELECT count(*) FROM u0)                               AS u0_now,
       962509                                                  AS u0_run80_control,
       CASE WHEN (SELECT count(*) FROM u0) = 962509
            THEN 'PINNED' ELSE 'DRIFTED -- read the findings note' END
                                                               AS u0_verdict,
       (SELECT count(*) FROM u2)                               AS u2_now,
       214708                                                  AS u2_run80_control,
       CASE WHEN (SELECT count(*) FROM u2) = 214708
            THEN 'PINNED' ELSE 'DRIFTED -- read the findings note' END
                                                               AS u2_verdict;

\echo ''
\echo '== 1. SEMANTIC GATE -- what the retained fields actually mean =='
-- STAGE 4 OF THE EVIDENCE CHAIN, run BEFORE any arithmetic rests on it. Four
-- things are asserted rather than assumed:
--   (a) the SIDE the probe models. The probe snapshots an ASK book and stores
--       best_ask; walking it prices a BUY. A SELL fill in the population would
--       be priced against the wrong side of the book.
--   (b) the DEPTH ELEMENT ORDER. Every level is read as [price, shares]. If
--       that is backwards every VWAP in this file is wrong, so it is tested:
--       element 0 must look like a price (0..1] and element 1 must be positive.
--   (c) best_ask must equal the MINIMUM level price. This is the strongest
--       single check available -- it confirms the element order AND that the
--       array is the ask side AND that best_ask describes the same snapshot.
--   (d) p_h agreement between trades.price and copy_probes.his_price.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), b AS MATERIALIZED (
  SELECT t.id, t.side AS trade_side, c.side AS probe_side,
         t.price AS p_h, c.his_price, c.best_ask, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), lv AS (
  SELECT b.id,
         min((e.value ->> 0)::float8)                          AS min_px,
         max((e.value ->> 0)::float8)                          AS max_px,
         min((e.value ->> 1)::float8)                          AS min_sh,
         count(*)                                              AS levels
    FROM b, jsonb_array_elements(b.depth) AS e
   GROUP BY b.id
)
SELECT count(*)                                                AS u2_events,
       count(*) FILTER (WHERE b.trade_side = 'BUY')            AS trade_side_buy,
       count(*) FILTER (WHERE b.trade_side = 'SELL')           AS trade_side_sell,
       count(*) FILTER (WHERE upper(COALESCE(b.probe_side, '')) = 'BUY')
                                                               AS probe_side_buy,
       count(*) FILTER (WHERE upper(COALESCE(b.probe_side, '')) <> 'BUY')
                                                               AS probe_side_other,
       count(*) FILTER (WHERE l.min_px > 0 AND l.max_px <= 1)  AS element0_price_shaped,
       count(*) FILTER (WHERE NOT (l.min_px > 0 AND l.max_px <= 1))
                                                               AS element0_NOT_price_shaped,
       count(*) FILTER (WHERE l.min_sh > 0)                    AS element1_positive,
       count(*) FILTER (WHERE b.best_ask IS NOT NULL
                          AND abs(b.best_ask::float8 - l.min_px) <= 0.000001)
                                                               AS best_ask_equals_min_level,
       count(*) FILTER (WHERE b.best_ask IS NOT NULL
                          AND abs(b.best_ask::float8 - l.min_px) > 0.000001)
                                                               AS best_ask_DISAGREES,
       count(*) FILTER (WHERE abs(b.p_h::float8
                                  - COALESCE(b.his_price::float8, -9)) <= 0.000001)
                                                               AS p_h_agrees_with_probe,
       count(*) FILTER (WHERE abs(b.p_h::float8
                                  - COALESCE(b.his_price::float8, -9)) > 0.000001)
                                                               AS p_h_DISAGREES
  FROM b JOIN lv l ON l.id = b.id;

\echo ''
\echo '== 2. THE SELECTION LADDER -- U2 to valid ask to depth supported, per lane =='
-- The population for each statistic, named at each step, with its cost. The
-- top-of-book population and the per-q populations are DIFFERENT and this is
-- where that is made explicit rather than implied.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), b AS MATERIALIZED (
  SELECT t.id, t.source, t.notional, t.size, t.price AS p_h,
         c.best_ask, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), v AS MATERIALIZED (
  SELECT b.id, b.source, b.notional, b.size, b.p_h, b.best_ask,
         (b.best_ask IS NOT NULL AND b.best_ask > 0 AND b.best_ask <= 1)
                                                               AS ask_valid,
         (b.p_h > 0 AND b.p_h < 1 AND b.size > 0)              AS q_definable,
         (SELECT COALESCE(sum((e.value ->> 1)::float8), 0)
            FROM jsonb_array_elements(b.depth) AS e)           AS depth_shares
    FROM b
), s AS (
  SELECT CASE WHEN source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
              WHEN source = 'chain'    THEN 'A_CHAIN'
              WHEN source = 'poll'     THEN 'A_POLL'
              WHEN source = 's1'       THEN 'A_S1'
              ELSE 'HALT_UNKNOWN_LANE_' || source END          AS stratum,
         id, notional, size, p_h, ask_valid, q_definable, depth_shares
    FROM v
), agg AS (
  SELECT stratum,
         count(*)                                              AS u2_events,
         count(*) FILTER (WHERE ask_valid)                     AS with_valid_ask,
         count(*) FILTER (WHERE ask_valid AND q_definable)     AS q_definable_events,
         count(*) FILTER (WHERE ask_valid AND q_definable
                            AND depth_shares >= 0.10 * size)   AS supported_q_a,
         count(*) FILTER (WHERE ask_valid AND q_definable
                            AND depth_shares >= size)          AS supported_q_b,
         count(*) FILTER (WHERE ask_valid AND q_definable
                            AND depth_shares >= 1000.0 / p_h)  AS supported_q_c
    FROM s GROUP BY stratum
)
SELECT stratum, u2_events, with_valid_ask, q_definable_events,
       supported_q_a, supported_q_b, supported_q_c,
       round(100.0 * supported_q_a / NULLIF(q_definable_events, 0), 2) AS pct_sup_q_a,
       round(100.0 * supported_q_b / NULLIF(q_definable_events, 0), 2) AS pct_sup_q_b,
       round(100.0 * supported_q_c / NULLIF(q_definable_events, 0), 2) AS pct_sup_q_c
  FROM agg
UNION ALL
SELECT 'A_ONLINE_COMBINED', sum(u2_events), sum(with_valid_ask),
       sum(q_definable_events), sum(supported_q_a), sum(supported_q_b),
       sum(supported_q_c),
       round(100.0 * sum(supported_q_a) / NULLIF(sum(q_definable_events), 0), 2),
       round(100.0 * sum(supported_q_b) / NULLIF(sum(q_definable_events), 0), 2),
       round(100.0 * sum(supported_q_c) / NULLIF(sum(q_definable_events), 0), 2)
  FROM agg WHERE stratum IN ('A_CHAIN', 'A_POLL', 'A_S1')
 ORDER BY 1;

\echo ''
\echo '== 3. TOP_OF_BOOK_MOVE -- ALL rows with a valid ask, no depth requirement =='
-- TOP_OF_BOOK_MOVE_PER_SHARE = best_ask(d) - p_h, in cents per share.
-- This is "the market had already moved". It needs no depth and no size, so the
-- depth requirement is NOT allowed to trim this population.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.condition_id, t.notional,
         (c.best_ask::float8 - t.price::float8) * 100.0        AS tob_cps
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND c.best_ask IS NOT NULL AND c.best_ask > 0 AND c.best_ask <= 1
), s AS (
  SELECT CASE WHEN source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
              WHEN source = 'chain'    THEN 'A_CHAIN'
              WHEN source = 'poll'     THEN 'A_POLL'
              WHEN source = 's1'       THEN 'A_S1'
              ELSE 'HALT_UNKNOWN_LANE_' || source END          AS stratum,
         id, condition_id, notional, tob_cps
    FROM v
), agg AS (
  SELECT stratum,
         count(*)                                              AS events,
         count(DISTINCT condition_id)                          AS conditions,
         round(sum(notional)::numeric, 2)                      AS source_notional,
         round(avg(tob_cps)::numeric, 4)                       AS mean_cps,
         round(percentile_cont(0.50) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4)
                                                               AS median_cps,
         round(percentile_cont(0.10) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4)
                                                               AS p10_cps,
         round(percentile_cont(0.25) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4)
                                                               AS p25_cps,
         round(percentile_cont(0.75) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4)
                                                               AS p75_cps,
         round(percentile_cont(0.90) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4)
                                                               AS p90_cps,
         round(100.0 * count(*) FILTER (WHERE tob_cps > 0)
               / NULLIF(count(*), 0), 3)                       AS pct_positive,
         round(100.0 * count(*) FILTER (WHERE tob_cps = 0)
               / NULLIF(count(*), 0), 3)                       AS pct_zero,
         round(100.0 * count(*) FILTER (WHERE tob_cps < 0)
               / NULLIF(count(*), 0), 3)                       AS pct_negative
    FROM s GROUP BY stratum
)
SELECT * FROM agg
UNION ALL
SELECT 'A_ONLINE_COMBINED', count(*), count(DISTINCT condition_id),
       round(sum(notional)::numeric, 2),
       round(avg(tob_cps)::numeric, 4),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       round(percentile_cont(0.25) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       round(100.0 * count(*) FILTER (WHERE tob_cps > 0) / NULLIF(count(*), 0), 3),
       round(100.0 * count(*) FILTER (WHERE tob_cps = 0) / NULLIF(count(*), 0), 3),
       round(100.0 * count(*) FILTER (WHERE tob_cps < 0) / NULLIF(count(*), 0), 3)
  FROM s WHERE stratum IN ('A_CHAIN', 'A_POLL', 'A_S1')
 ORDER BY 1;

\echo ''
\echo '== 4. DEPTH SUPPORT CENSUS, per scenario and lane =='
-- The quantities Correction 1 requires beside every sizing scenario:
-- requested events, supported, exhausted, retention, requested shares,
-- depth-supported shares, and the coverage fraction of requested quantity.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.notional, t.size, t.price AS p_h, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND c.best_ask IS NOT NULL AND c.best_ask > 0 AND c.best_ask <= 1
     AND t.price > 0 AND t.price < 1 AND t.size > 0
), d AS MATERIALIZED (
  SELECT v.id, v.source, v.notional, v.size, v.p_h,
         (SELECT COALESCE(sum((e.value ->> 1)::float8), 0)
            FROM jsonb_array_elements(v.depth) AS e)           AS depth_shares
    FROM v
), sc AS (
  SELECT d.id,
         CASE WHEN d.source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
              WHEN d.source = 'chain'    THEN 'A_CHAIN'
              WHEN d.source = 'poll'     THEN 'A_POLL'
              WHEN d.source = 's1'       THEN 'A_S1'
              ELSE 'HALT_UNKNOWN_LANE_' || d.source END        AS stratum,
         d.depth_shares, x.scenario, x.req,
         x.req * d.p_h::float8                                 AS src_notional_at_req
    FROM d
    CROSS JOIN LATERAL (VALUES ('Q_A', 0.10 * d.size::float8),
                               ('Q_B', d.size::float8),
                               ('Q_C', 1000.0 / d.p_h::float8))
                 AS x(scenario, req)
)
SELECT scenario, stratum,
       count(*)                                                AS requested_events,
       count(*) FILTER (WHERE depth_shares >= req)             AS fully_depth_supported,
       count(*) FILTER (WHERE depth_shares <  req)             AS depth_exhausted,
       round(100.0 * count(*) FILTER (WHERE depth_shares >= req)
             / NULLIF(count(*), 0), 3)                         AS retention_pct,
       round(sum(req)::numeric, 2)                             AS requested_shares,
       round(sum(least(COALESCE(depth_shares, 0), COALESCE(req, 0)))::numeric, 2)
                                                               AS depth_supported_shares,
       round((sum(least(COALESCE(depth_shares, 0), COALESCE(req, 0)))
              / NULLIF(sum(req), 0))::numeric, 6)              AS depth_support_fraction,
       round(sum(src_notional_at_req)::numeric, 2)             AS src_notional_at_requested_size
  FROM sc GROUP BY scenario, stratum ORDER BY scenario, stratum;

\echo ''
\echo '== 5. DRAG ON THE DEPTH_SUPPORTED_SUBSET, per scenario and lane =='
-- The three disjoint components, per share and in dollars, on FULLY DEPTH
-- SUPPORTED rows ONLY. Retention against the requested cohort prints beside
-- every figure so the subset is never mistaken for the population.
--   TOP_OF_BOOK_MOVE_PER_SHARE    = best_ask - p_h
--   DEPTH_SLIPPAGE_PER_SHARE(q)   = depth_vwap(q) - best_ask
--   TOTAL_OBSERVED_DRAG_PER_SHARE = depth_vwap(q) - p_h
-- The walk is price-ascending with ordinality as the tie-break, so the result
-- does not depend on the stored array order.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.condition_id, t.notional, t.size,
         t.price::float8 AS p_h, c.best_ask::float8 AS ask, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND c.best_ask IS NOT NULL AND c.best_ask > 0 AND c.best_ask <= 1
     AND t.price > 0 AND t.price < 1 AND t.size > 0
), q AS MATERIALIZED (
  SELECT v.id, v.source, v.condition_id, v.notional, v.p_h, v.ask, v.depth,
         0.10 * v.size::float8                                 AS q_a,
         v.size::float8                                        AS q_b,
         1000.0 / v.p_h                                        AS q_c
    FROM v
), lv AS (
  SELECT q.id, (e.value ->> 0)::float8 AS px, (e.value ->> 1)::float8 AS sh,
         e.ordinality AS ord
    FROM q, jsonb_array_elements(q.depth) WITH ORDINALITY AS e
), cw AS (
  SELECT lv.id, lv.px, lv.sh,
         sum(lv.sh) OVER (PARTITION BY lv.id ORDER BY lv.px, lv.ord
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           - lv.sh                                             AS before_sh
    FROM lv
), w AS (
  SELECT cw.id,
         sum(cw.sh)                                            AS depth_shares,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(q.q_a, 0) - COALESCE(cw.before_sh, 0)), 0)) AS cost_a,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(q.q_b, 0) - COALESCE(cw.before_sh, 0)), 0)) AS cost_b,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(q.q_c, 0) - COALESCE(cw.before_sh, 0)), 0)) AS cost_c
    FROM cw JOIN q ON q.id = cw.id
   GROUP BY cw.id
), sc AS (
  SELECT q.id,
         CASE WHEN q.source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
              WHEN q.source = 'chain'    THEN 'A_CHAIN'
              WHEN q.source = 'poll'     THEN 'A_POLL'
              WHEN q.source = 's1'       THEN 'A_S1'
              ELSE 'HALT_UNKNOWN_LANE_' || q.source END        AS stratum,
         q.condition_id, q.notional, q.p_h, q.ask, w.depth_shares,
         x.scenario, x.req, x.cost
    FROM q JOIN w ON w.id = q.id
    CROSS JOIN LATERAL (VALUES ('Q_A', q.q_a, w.cost_a),
                               ('Q_B', q.q_b, w.cost_b),
                               ('Q_C', q.q_c, w.cost_c))
                 AS x(scenario, req, cost)
), m AS (
  SELECT sc.scenario, sc.stratum, sc.condition_id, sc.notional, sc.req,
         (sc.ask - sc.p_h) * 100.0                             AS tob_cps,
         (sc.cost / sc.req - sc.ask) * 100.0                   AS dep_cps,
         (sc.cost / sc.req - sc.p_h) * 100.0                   AS tot_cps,
         (sc.ask - sc.p_h) * sc.req                            AS tob_usd,
         (sc.cost / sc.req - sc.ask) * sc.req                  AS dep_usd,
         (sc.cost / sc.req - sc.p_h) * sc.req                  AS tot_usd
    FROM sc WHERE sc.depth_shares >= sc.req AND sc.req > 0
)
SELECT scenario, stratum,
       'DEPTH_SUPPORTED_SUBSET'                                AS population,
       count(*)                                                AS events,
       count(DISTINCT condition_id)                            AS conditions,
       round(sum(notional)::numeric, 2)                        AS source_notional,
       round(avg(tot_cps)::numeric, 4)                         AS mean_total_cps,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY tot_cps)::numeric, 4)
                                                               AS median_total_cps,
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY tot_cps)::numeric, 4)
                                                               AS p10_total_cps,
       round(percentile_cont(0.25) WITHIN GROUP (ORDER BY tot_cps)::numeric, 4)
                                                               AS p25_total_cps,
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY tot_cps)::numeric, 4)
                                                               AS p75_total_cps,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY tot_cps)::numeric, 4)
                                                               AS p90_total_cps,
       round(avg(tob_cps)::numeric, 4)                         AS mean_tob_cps,
       round(avg(dep_cps)::numeric, 4)                         AS mean_depth_cps,
       round(sum(tob_usd)::numeric, 2)                         AS tob_move_usd,
       round(sum(dep_usd)::numeric, 2)                         AS depth_slippage_usd,
       round(sum(tot_usd)::numeric, 2)                         AS total_observed_drag_usd,
       round(100.0 * count(*) FILTER (WHERE tot_cps > 0)
             / NULLIF(count(*), 0), 3)                         AS pct_positive,
       round(100.0 * count(*) FILTER (WHERE tot_cps = 0)
             / NULLIF(count(*), 0), 3)                         AS pct_zero,
       round(100.0 * count(*) FILTER (WHERE tot_cps < 0)
             / NULLIF(count(*), 0), 3)                         AS pct_negative
  FROM m GROUP BY scenario, stratum ORDER BY scenario, stratum;

\echo ''
\echo '== 6. CLOSURE ASSERTION -- per event and in aggregate =='
-- TOP_OF_BOOK_MOVE_DOLLARS + DEPTH_SLIPPAGE_DOLLARS = TOTAL_OBSERVED_DRAG_DOLLARS
-- on FULLY_DEPTH_SUPPORTED rows. Tolerance <= $0.005 per event. A witness count
-- prints beside the violation count: zero violations beside zero witnesses is
-- NOT TESTED, never PASS.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.size, t.price::float8 AS p_h,
         c.best_ask::float8 AS ask, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND c.best_ask IS NOT NULL AND c.best_ask > 0 AND c.best_ask <= 1
     AND t.price > 0 AND t.price < 1 AND t.size > 0
), q AS MATERIALIZED (
  SELECT v.id, v.p_h, v.ask, v.depth,
         0.10 * v.size::float8 AS q_a, v.size::float8 AS q_b,
         1000.0 / v.p_h AS q_c
    FROM v
), lv AS (
  SELECT q.id, (e.value ->> 0)::float8 AS px, (e.value ->> 1)::float8 AS sh,
         e.ordinality AS ord
    FROM q, jsonb_array_elements(q.depth) WITH ORDINALITY AS e
), cw AS (
  SELECT lv.id, lv.px, lv.sh,
         sum(lv.sh) OVER (PARTITION BY lv.id ORDER BY lv.px, lv.ord
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           - lv.sh                                             AS before_sh
    FROM lv
), w AS (
  SELECT cw.id,
         sum(cw.sh)                                            AS depth_shares,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(q.q_a, 0) - COALESCE(cw.before_sh, 0)), 0)) AS cost_a,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(q.q_b, 0) - COALESCE(cw.before_sh, 0)), 0)) AS cost_b,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(q.q_c, 0) - COALESCE(cw.before_sh, 0)), 0)) AS cost_c
    FROM cw JOIN q ON q.id = cw.id
   GROUP BY cw.id
), sc AS (
  SELECT q.id, q.p_h, q.ask, w.depth_shares, x.scenario, x.req, x.cost
    FROM q JOIN w ON w.id = q.id
    CROSS JOIN LATERAL (VALUES ('Q_A', q.q_a, w.cost_a),
                               ('Q_B', q.q_b, w.cost_b),
                               ('Q_C', q.q_c, w.cost_c))
                 AS x(scenario, req, cost)
   WHERE w.depth_shares >= x.req AND x.req > 0
), r AS (
  SELECT sc.scenario,
         (sc.ask - sc.p_h) * sc.req                            AS tob_usd,
         (sc.cost / sc.req - sc.ask) * sc.req                  AS dep_usd,
         (sc.cost / sc.req - sc.p_h) * sc.req                  AS tot_usd
    FROM sc
)
SELECT scenario,
       count(*)                                                AS witnesses,
       count(*) FILTER (WHERE abs(tob_usd + dep_usd - tot_usd) > 0.005)
                                                               AS per_event_violations,
       round(max(abs(tob_usd + dep_usd - tot_usd))::numeric, 8) AS worst_per_event_gap,
       round(abs(sum(tob_usd) + sum(dep_usd) - sum(tot_usd))::numeric, 6)
                                                               AS aggregate_gap,
       CASE WHEN count(*) = 0 THEN 'NOT TESTED -- zero witnesses'
            WHEN count(*) FILTER (WHERE abs(tob_usd + dep_usd - tot_usd) > 0.005) = 0
             AND abs(sum(tob_usd) + sum(dep_usd) - sum(tot_usd)) <= 0.005
            THEN 'CLOSES'
            ELSE 'FAILS -- the decomposition does not close' END AS verdict
  FROM r GROUP BY scenario ORDER BY scenario;

\echo ''
\echo '== 7. DEPTH_EXHAUSTED -- NOT IDENTIFIABLE FROM RETAINED DEPTH =='
-- No VWAP, no drag, no extrapolated last price. Only what the retained rows do
-- say: how many events, their source notional, how much was requested, how much
-- the retained book covers, and the coverage fraction.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.notional, t.size, t.price AS p_h, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND c.best_ask IS NOT NULL AND c.best_ask > 0 AND c.best_ask <= 1
     AND t.price > 0 AND t.price < 1 AND t.size > 0
), d AS MATERIALIZED (
  SELECT v.id, v.source, v.notional, v.size, v.p_h,
         (SELECT COALESCE(sum((e.value ->> 1)::float8), 0)
            FROM jsonb_array_elements(v.depth) AS e)           AS depth_shares
    FROM v
), sc AS (
  SELECT d.id,
         CASE WHEN d.source = 'backfill' THEN 'A_BACKFILL_DIAGNOSTIC'
              WHEN d.source = 'chain'    THEN 'A_CHAIN'
              WHEN d.source = 'poll'     THEN 'A_POLL'
              WHEN d.source = 's1'       THEN 'A_S1'
              ELSE 'HALT_UNKNOWN_LANE_' || d.source END        AS stratum,
         d.notional, d.depth_shares, x.scenario, x.req
    FROM d
    CROSS JOIN LATERAL (VALUES ('Q_A', 0.10 * d.size::float8),
                               ('Q_B', d.size::float8),
                               ('Q_C', 1000.0 / d.p_h::float8))
                 AS x(scenario, req)
   WHERE d.depth_shares < x.req
)
SELECT scenario, stratum,
       'NOT_IDENTIFIABLE_FROM_RETAINED_DEPTH'                  AS status,
       count(*)                                                AS depth_exhausted_events,
       round(sum(notional)::numeric, 2)                        AS source_notional,
       round(sum(req)::numeric, 2)                             AS requested_shares,
       round(sum(depth_shares)::numeric, 2)                    AS observed_shares,
       round((sum(depth_shares) / NULLIF(sum(req), 0))::numeric, 6)
                                                               AS coverage_fraction,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY depth_shares / NULLIF(req, 0))::numeric, 6)
                                                               AS median_row_coverage
  FROM sc GROUP BY scenario, stratum ORDER BY scenario, stratum;

\echo ''
\echo '== 8. SEGMENTS -- decision-time-valid variables only =='
-- sport, source price band, RN1 fill-size band. Top-of-book on ALL valid-ask
-- rows (left half) beside Q_A on its DEPTH_SUPPORTED_SUBSET (right half), with
-- the Q_A retention printed so the two halves are never read as one population.
-- No settlement variable appears here. Lane is already segmented above.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.sport, t.notional, t.size,
         t.price::float8 AS p_h, c.best_ask::float8 AS ask, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
     AND c.best_ask IS NOT NULL AND c.best_ask > 0 AND c.best_ask <= 1
     AND t.price > 0 AND t.price < 1 AND t.size > 0
     AND t.source IN ('chain', 'poll', 's1')
), lv AS (
  SELECT v.id, (e.value ->> 0)::float8 AS px, (e.value ->> 1)::float8 AS sh,
         e.ordinality AS ord
    FROM v, jsonb_array_elements(v.depth) WITH ORDINALITY AS e
), cw AS (
  SELECT lv.id, lv.px, lv.sh,
         sum(lv.sh) OVER (PARTITION BY lv.id ORDER BY lv.px, lv.ord
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
           - lv.sh                                             AS before_sh
    FROM lv
), w AS (
  SELECT cw.id,
         sum(cw.sh)                                            AS depth_shares,
         sum(cw.px * greatest(least(COALESCE(cw.sh, 0),
               COALESCE(0.10 * v.size::float8, 0)
                 - COALESCE(cw.before_sh, 0)), 0))             AS cost_a
    FROM cw JOIN v ON v.id = cw.id
   GROUP BY cw.id
), m AS (
  SELECT v.id,
         COALESCE(v.sport, '(null)')                           AS sport,
         CASE WHEN v.p_h < 0.10 THEN 'p1 [0.00,0.10)'
              WHEN v.p_h < 0.25 THEN 'p2 [0.10,0.25)'
              WHEN v.p_h < 0.50 THEN 'p3 [0.25,0.50)'
              WHEN v.p_h < 0.75 THEN 'p4 [0.50,0.75)'
              WHEN v.p_h < 0.90 THEN 'p5 [0.75,0.90)'
              ELSE                   'p6 [0.90,1.00)' END      AS price_band,
         CASE WHEN v.notional <   10 THEN 'n1 <$10'
              WHEN v.notional <  100 THEN 'n2 $10-100'
              WHEN v.notional < 1000 THEN 'n3 $100-1k'
              WHEN v.notional <10000 THEN 'n4 $1k-10k'
              ELSE                        'n5 >=$10k' END      AS size_band,
         v.notional,
         (v.ask - v.p_h) * 100.0                               AS tob_cps,
         0.10 * v.size::float8                                 AS q_a,
         w.depth_shares,
         CASE WHEN w.depth_shares >= 0.10 * v.size::float8
                   AND v.size > 0
              THEN (w.cost_a / (0.10 * v.size::float8) - v.p_h) * 100.0 END
                                                               AS qa_tot_cps
    FROM v JOIN w ON w.id = v.id
)
SELECT 'sport' AS segment_kind, sport AS segment, count(*) AS events,
       round(sum(notional)::numeric, 2) AS source_notional,
       round(avg(tob_cps)::numeric, 4) AS mean_tob_cps,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4)
                                        AS median_tob_cps,
       count(*) FILTER (WHERE qa_tot_cps IS NOT NULL) AS qa_supported_events,
       round(100.0 * count(*) FILTER (WHERE qa_tot_cps IS NOT NULL)
             / NULLIF(count(*), 0), 2) AS qa_retention_pct,
       round(avg(qa_tot_cps)::numeric, 4) AS qa_mean_total_cps,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY qa_tot_cps)::numeric, 4)
                                        AS qa_median_total_cps
  FROM m GROUP BY sport
 HAVING count(*) >= 200
UNION ALL
SELECT 'price_band', price_band, count(*),
       round(sum(notional)::numeric, 2),
       round(avg(tob_cps)::numeric, 4),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       count(*) FILTER (WHERE qa_tot_cps IS NOT NULL),
       round(100.0 * count(*) FILTER (WHERE qa_tot_cps IS NOT NULL)
             / NULLIF(count(*), 0), 2),
       round(avg(qa_tot_cps)::numeric, 4),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY qa_tot_cps)::numeric, 4)
  FROM m GROUP BY price_band
UNION ALL
SELECT 'size_band', size_band, count(*),
       round(sum(notional)::numeric, 2),
       round(avg(tob_cps)::numeric, 4),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY tob_cps)::numeric, 4),
       count(*) FILTER (WHERE qa_tot_cps IS NOT NULL),
       round(100.0 * count(*) FILTER (WHERE qa_tot_cps IS NOT NULL)
             / NULLIF(count(*), 0), 2),
       round(avg(qa_tot_cps)::numeric, 4),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY qa_tot_cps)::numeric, 4)
  FROM m GROUP BY size_band
 ORDER BY 1, 2;

\echo ''
\echo '== 9. WITNESS LEDGER =='
-- Every test above, with the count of rows that could have falsified it. Zero
-- witnesses prints NOT TESTED and never PASS.
WITH rn1 AS MATERIALIZED (
  SELECT id FROM whales WHERE lower(username) = 'rn1'
), v AS MATERIALIZED (
  SELECT t.id, t.source, t.size, t.price AS p_h, c.best_ask, c.depth
    FROM trades t
    JOIN copy_probes c ON c.trade_id = t.id
   WHERE t.whale_id IN (SELECT id FROM rn1)
     AND t.ts <= timestamptz '2026-09-12 00:00:00+00'
     AND t.detected_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.whale_id IN (SELECT id FROM rn1)
     AND c.probe_at <= timestamptz '2026-09-12 00:00:00+00'
     AND c.book_ok AND c.error IS NULL AND c.depth IS NOT NULL
), d AS MATERIALIZED (
  SELECT v.id, v.source, v.size, v.p_h, v.best_ask,
         (SELECT COALESCE(sum((e.value ->> 1)::float8), 0)
            FROM jsonb_array_elements(v.depth) AS e)           AS depth_shares
    FROM v
), t AS (
  SELECT 1 AS ord, 'U2 at the pinned cutoff' AS test_name,
         count(*) AS witnesses FROM d
  UNION ALL
  SELECT 2, 'rows with a valid best ask (top-of-book population)',
         count(*) FROM d
   WHERE best_ask IS NOT NULL AND best_ask > 0 AND best_ask <= 1
  UNION ALL
  SELECT 3, 'rows on an ONLINE lane (backfill fenced out)',
         count(*) FROM d WHERE source IN ('chain', 'poll', 's1')
  UNION ALL
  SELECT 4, 'rows on a lane outside the eligibility matrix -- HALT if not zero',
         count(*) FROM d
   WHERE source NOT IN ('chain', 'poll', 's1', 'backfill')
  UNION ALL
  SELECT 5, 'rows where q is definable (p_h in (0,1) and size > 0)',
         count(*) FROM d WHERE p_h > 0 AND p_h < 1 AND size > 0
  UNION ALL
  SELECT 6, 'FULLY_DEPTH_SUPPORTED at Q_A', count(*) FROM d
   WHERE p_h > 0 AND p_h < 1 AND size > 0 AND depth_shares >= 0.10 * size
  UNION ALL
  SELECT 7, 'FULLY_DEPTH_SUPPORTED at Q_B', count(*) FROM d
   WHERE p_h > 0 AND p_h < 1 AND size > 0 AND depth_shares >= size
  UNION ALL
  SELECT 8, 'FULLY_DEPTH_SUPPORTED at Q_C', count(*) FROM d
   WHERE p_h > 0 AND p_h < 1 AND size > 0 AND depth_shares >= 1000.0 / p_h
)
SELECT ord, test_name, witnesses,
       CASE WHEN witnesses = 0 THEN 'NOT TESTED' ELSE 'TESTED' END AS verdict
  FROM t ORDER BY ord;

\echo ''
\echo '== RUN 81A ENDS. No settlement. No latency figure. mirror_live=false. =='
