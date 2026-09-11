-- ============================================================================
-- CHECK A: is the matched-notional-weighted edge real, or a duplication artifact?
-- CHECK B: does dM > 0 actually imply a SELL on the US venue?
-- (2026-09-11, read-only.)
--
-- WHY CHECK A EXISTS. My selection statement attached a CONDITION-level
-- gross_pair_edge to every FILL row and then weighted by that fill's dM
-- notional. Two things are wrong with reading the result as a cohort
-- comparison:
--
--   1. The condition attribute repeats across every fill in the condition, so
--      a condition with 40 fills states its pair edge 40 times.
--   2. Worse, cohort membership is per FILL, so a condition with some fills
--      probed and some not contributes its edge to BOTH cohorts. The two
--      cohorts are not disjoint sets of conditions, which is what a selection
--      comparison requires.
--
-- So the -1.77% / -2.10% figures are WITHDRAWN pending statement 2 below, which
-- computes matched economics ONCE PER CONDITION with an explicit, mutually
-- exclusive assignment rule.
--
-- WHY CHECK B EXISTS, and what the code says. The execution-state table
-- asserted that every dM event requires a SELL. Read from OUR OWN code rather
-- than from Polymarket global, that assertion is CONDITIONAL, not an invariant.
-- The derivation, in three steps, every one of them from this repository:
--
--   (i)  THE POSITION MODEL. analytics/mirror.py:125
--          def his_net(long_shares, other_shares) -> long_shares - other_shares
--        "The signed net on a netting venue, in long-token shares. His matched
--        pairs cancel; what is left is the directional residual." Our target is
--        that signed figure scaled by ratio, and mirror_books.ledger_net is
--        "long-token shares BY OUR BOOKING" -- ONE signed number per market.
--
--   (ii) SO THE REQUIRED ACTION IS SET BY WHICH TOKEN HE BOUGHT, not by dM:
--          he buys other_asset -> other_shares up -> his_net DOWN -> we SELL
--          he buys long_asset  -> long_shares  up -> his_net UP   -> we BUY
--        dM > 0 only says he bought the leg he held LESS of. Whether that leg
--        is the venue's long or other side is a separate fact.
--
--  (iii) WHO PICKS long_asset. mirror_shadow.py:748 `_choose_long`. On the
--        per-side-identifier shape it is
--          a = max(longs, key=lambda x: float(pos.get(x, 0.0)))
--        -- HIS LARGER LEG, measured once at book-open. On that shape the
--        invariant DOES hold at open: the deficient leg is by definition the
--        smaller one, so a dM completion lands on other_asset and we SELL.
--        But the same function has two other branches where his position never
--        enters the choice: a single BUY_LONG intent takes `longs[0]`, and a
--        BUY_SHORT intent takes `other_of(a)`. mirror_live.py:13958 adds a
--        third, `long_from="catalogue"`, naming the sibling token outright.
--        On those, long_asset may be his SMALLER leg and the completion is a
--        BUY. And because the choice is made ONCE at open and never revisited,
--        any later crossover flips the mapping for every subsequent event.
--
-- So Check B is an empirical question with four exception classes to size:
-- intent-designated markets, catalogue-named markets, post-open crossover, and
-- our own pre-position (flat = nothing to complete; short = the action
-- inverts). Statements 4-6 measure them instead of asserting the mapping.
--
-- A NAMING FIX THE OWNER ASKED FOR, and it matters. The earlier table put
-- "26.53% MEASURABLE_BUY_SIDE" next to rows carrying zero dM. Those are ENTRY /
-- RESIDUAL-OPENING events, not completions. They are reported separately here
-- so no one reads that 26.53% as "a quarter of matched completions have
-- measurable execution" -- the true figure for completions is in statement 3.
--
-- Read-only: seven SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. THE DUPLICATION DIAGNOSTIC: does the old weighting repeat conditions? =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), rowprobe AS (
  SELECT b.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = b.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS covered
    FROM base b
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, r.covered FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    JOIN rowprobe r ON r.id = b.id
)
SELECT count(DISTINCT condition_id) AS conditions,
       count(*) AS fill_rows,
       round((count(*)::numeric / NULLIF(count(DISTINCT condition_id), 0)), 2)
         AS ROWS_PER_CONDITION_each_repeating_its_pair_cost,
       count(DISTINCT condition_id) FILTER (WHERE covered) AS conditions_touching_covered,
       count(DISTINCT condition_id) FILTER (WHERE NOT covered) AS conditions_touching_missing,
       (count(DISTINCT condition_id) FILTER (WHERE covered)
        + count(DISTINCT condition_id) FILTER (WHERE NOT covered)
        - count(DISTINCT condition_id)) AS CONDITIONS_IN_BOTH_COHORTS,
       round((100.0 * (count(DISTINCT condition_id) FILTER (WHERE covered)
                       + count(DISTINCT condition_id) FILTER (WHERE NOT covered)
                       - count(DISTINCT condition_id))
              / NULLIF(count(DISTINCT condition_id), 0))::numeric, 2) AS pct_straddling
  FROM canon;


\echo '== 2. DIRECT CONDITION-LEVEL MATCHED ECONOMICS, one row per condition =='
-- M = min(total yes shares, total no shares)
-- matched_cost = M * (vwap_yes + vwap_no);  matched_pnl = M - matched_cost
-- Cohort assignment is MUTUALLY EXCLUSIVE and stated: a condition is
-- PROBE_COVERED when a majority of its dM shares sit on probe-covered fills.
-- The strict all-or-nothing rule is reported beside it so the choice of rule
-- cannot drive the answer.
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), rowprobe AS (
  SELECT b.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = b.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS covered
    FROM base b
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, r.covered FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    JOIN rowprobe r ON r.id = b.id
), rn AS (
  SELECT condition_id, ts, tx_hash, asset, sh, px, covered,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM canon
), dm AS (
  SELECT condition_id, covered,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), cohort AS (
  SELECT condition_id,
         sum(d_m) AS dm_total,
         sum(d_m) FILTER (WHERE covered) AS dm_covered,
         bool_and(covered) AS all_covered,
         bool_or(covered) AS any_covered
    FROM dm GROUP BY 1
), cond AS (
  SELECT condition_id,
         sum(sh) FILTER (WHERE outcome_index = 0) AS qy,
         sum(sh) FILTER (WHERE outcome_index = 1) AS qn,
         sum(sh * px) FILTER (WHERE outcome_index = 0)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 0), 0) AS vy,
         sum(sh * px) FILTER (WHERE outcome_index = 1)
           / NULLIF(sum(sh) FILTER (WHERE outcome_index = 1), 0) AS vn
    FROM canon GROUP BY 1
), m AS (
  SELECT c.condition_id, LEAST(c.qy, c.qn) AS mm, (c.vy + c.vn) AS pair_cost,
         LEAST(c.qy, c.qn) * (c.vy + c.vn) AS matched_cost,
         LEAST(c.qy, c.qn) * (1.0 - (c.vy + c.vn)) AS matched_pnl,
         CASE WHEN co.dm_total > 0 AND co.dm_covered / co.dm_total >= 0.5
                THEN 'PROBE_COVERED' ELSE 'PROBE_MISSING' END AS cohort_majority,
         CASE WHEN co.all_covered THEN 'PROBE_COVERED'
              WHEN NOT co.any_covered THEN 'PROBE_MISSING'
              ELSE 'MIXED' END AS cohort_strict
    FROM cond c JOIN cohort co ON co.condition_id = c.condition_id
   WHERE c.qy > 0 AND c.qn > 0
), u AS (
  SELECT 'ALL_GRADEABLE' AS cohort, * FROM m
  UNION ALL SELECT cohort_majority, * FROM m
  UNION ALL SELECT 'strict: ' || cohort_strict, * FROM m
)
SELECT cohort, count(*) AS condition_count,
       round(sum(mm)::numeric, 0) AS sum_M_shares,
       round(sum(matched_cost)::numeric, 0) AS sum_matched_cost,
       round(sum(matched_pnl)::numeric, 0) AS SUM_MATCHED_PNL,
       round((sum(matched_pnl) / NULLIF(sum(matched_cost), 0))::numeric, 5) AS MATCHED_ROI,
       round(avg(pair_cost)::numeric, 5) AS mean_pair_cost,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY pair_cost)::numeric, 5)
         AS median_pair_cost,
       count(*) FILTER (WHERE pair_cost < 1.0) AS conditions_under_one,
       round((100.0 * count(*) FILTER (WHERE pair_cost < 1.0) / count(*))::numeric, 2)
         AS pct_under_one
  FROM u GROUP BY 1 ORDER BY 2 DESC;


\echo '== 3. THE TABLE THE OWNER ASKED FOR: entry/opening vs dM COMPLETION events =='
WITH base AS (
  SELECT t.id, t.tx_hash, t.asset, t.side, t.ts, t.condition_id, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), rowprobe AS (
  SELECT b.id, EXISTS (SELECT 1 FROM copy_probes p
                        WHERE p.trade_id = b.id AND p.book_ok
                          AND p.best_ask IS NOT NULL) AS covered
    FROM base b
), env AS (
  SELECT tx_hash, asset, side,
         CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS canon_feed
    FROM base GROUP BY 1, 2, 3
), canon AS (
  SELECT b.*, r.covered FROM base b
    JOIN env e ON e.tx_hash = b.tx_hash AND e.asset = b.asset
              AND e.side = b.side AND e.canon_feed = b.feed
    JOIN rowprobe r ON r.id = b.id
), rn AS (
  SELECT condition_id, ts, tx_hash, asset, sh, px, covered,
         sum(CASE WHEN outcome_index = 0 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN outcome_index = 1 THEN sh ELSE 0 END)
           OVER (PARTITION BY condition_id ORDER BY ts, tx_hash, asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM canon
), dm AS (
  SELECT sh, px, covered,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
)
SELECT CASE WHEN d_m <= 0.000001 THEN 'A ENTRY / RESIDUAL-OPENING EVENT'
            ELSE                      'B dM COMPLETION EVENT' END AS event_class,
       CASE WHEN covered THEN 'fill-specific probe' ELSE 'no probe' END AS probe,
       count(*) AS events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_all_events,
       round(sum(sh * px)::numeric, 0) AS notional,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS dM_notional,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2)
         AS PCT_OF_ALL_dM
  FROM dm GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 4. CHECK B, THE CORE TEST: which leg does a dM completion land on? =='
-- This is the invariant itself. his_net = long_shares - other_shares, so the
-- required action is decided by WHICH TOKEN he bought, not by dM:
--   completing fill on other_asset -> his_net falls -> SELL   (invariant holds)
--   completing fill on long_asset  -> his_net rises -> BUY    (invariant FAILS)
-- WHERE THE DESIGNATION COMES FROM. mirror_books holds it for markets we
-- actually opened -- only 09-06..09-10, far narrower than the dM population.
-- mirror_candidate_refusals holds long_asset AND long_from for every market the
-- mapper named a long token for, opened or not, so it widens the answer by
-- weeks and is the ONLY place the catalogue-named exception class is
-- distinguishable. Both are read, books preferred where a condition has both.
-- other_asset is not on the refusal row; it is derived as the sibling of
-- long_asset among HIS OWN two tokens in that condition, which is exactly what
-- _choose_long's other_of() does.
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  SELECT condition_id, long_asset, opened_at AS at, 1 AS pref,
         'A book -- we opened it' AS designation_source
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, at, 2,
         'B refusal -- mapper named it, no book'
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), mapped AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset,
         d.designation_source,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.at
), his AS (
  SELECT t.condition_id, t.ts, t.asset, t.size::float8 AS sh, t.price::float8 AS px,
         sum(CASE WHEN t.outcome_index = 0 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN t.outcome_index = 1 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), hdm AS (
  SELECT condition_id, ts, asset, sh, px,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM his WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
)
SELECT CASE WHEN h.asset = m.other_asset
              THEN '1 completing fill on OTHER leg -> his_net FALLS -> SELL (invariant holds)'
            WHEN h.asset = m.long_asset
              THEN '2 completing fill on LONG leg  -> his_net RISES -> BUY  (INVARIANT FAILS)'
            ELSE '3 token matches neither designated leg -- mapping cannot answer'
       END AS required_bettor_action,
       m.designation_source,
       count(*) AS dM_events,
       count(DISTINCT h.condition_id) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(h.d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(h.d_m) / NULLIF(sum(sum(h.d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(h.d_m * h.px)::numeric, 0) AS dM_notional
  FROM hdm h JOIN mapped m ON m.condition_id = h.condition_id
 WHERE h.d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 5. CHECK B exception classes: designation source, and post-open crossover =='
-- Two of the four exception routes, sized. `long_from` distinguishes the
-- branch of _choose_long that used his position from the one that did not.
-- Crossover is the second: the designation is made ONCE at open, so a
-- condition whose dM events land on BOTH legs has flipped mid-life and cannot
-- satisfy the invariant throughout however it was designated.
WITH legs AS (
  SELECT t.condition_id, min(t.asset) AS a1, max(t.asset) AS a2,
         count(DISTINCT t.asset) AS n_assets
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
   GROUP BY 1
), desig_raw AS (
  -- long_from is kept ONLY on the refusal row, so the catalogue-named class is
  -- separable only there. A book row cannot say which branch chose its long
  -- token, and is labelled honestly rather than assumed to be position-chosen.
  SELECT condition_id, long_asset, opened_at AS at, 1 AS pref,
         'book row -- designating branch NOT RECORDED' AS long_from
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, at, 2,
         CASE WHEN long_from = 'catalogue'
                THEN 'catalogue -- his position did NOT choose it'
              WHEN long_from IS NULL OR long_from = ''
                THEN 'mapper -- position or intent branch, unlabelled'
              ELSE long_from END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), mapped AS (
  SELECT DISTINCT ON (d.condition_id) d.condition_id, d.long_asset, d.long_from,
         CASE WHEN l.n_assets = 2 AND d.long_asset = l.a1 THEN l.a2
              WHEN l.n_assets = 2 AND d.long_asset = l.a2 THEN l.a1 END AS other_asset
    FROM desig_raw d JOIN legs l ON l.condition_id = d.condition_id
   ORDER BY d.condition_id, d.pref, d.at
), his AS (
  SELECT t.condition_id, t.ts, t.asset, t.size::float8 AS sh, t.price::float8 AS px,
         sum(CASE WHEN t.outcome_index = 0 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN t.outcome_index = 1 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), hdm AS (
  SELECT condition_id, asset, px,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM his WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), per_cond AS (
  SELECT m.condition_id, m.long_from,
         bool_or(h.asset = m.other_asset) AS any_on_other,
         bool_or(h.asset = m.long_asset)  AS any_on_long,
         sum(h.d_m) AS dm_total,
         sum(h.d_m) FILTER (WHERE h.asset = m.long_asset) AS dm_on_long
    FROM hdm h JOIN mapped m ON m.condition_id = h.condition_id
   WHERE h.d_m > 0.000001
   GROUP BY 1, 2
)
SELECT long_from AS designating_branch,
       CASE WHEN any_on_other AND any_on_long
              THEN 'C CROSSOVER -- dM lands on BOTH legs over the market life'
            WHEN any_on_long THEN 'B every dM on the LONG leg -- invariant fails throughout'
            ELSE 'A every dM on the OTHER leg -- invariant holds throughout'
       END AS designation_outcome,
       count(*) AS conditions,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_conditions,
       round(sum(dm_total)::numeric, 0) AS dM_shares,
       round(sum(COALESCE(dm_on_long, 0))::numeric, 0) AS dM_shares_needing_a_BUY,
       round((100.0 * sum(COALESCE(dm_on_long, 0)) / NULLIF(sum(dm_total), 0))::numeric, 2)
         AS pct_of_dM_that_inverts
  FROM per_cond GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. CHECK B, our own side: what was BETTOR holding when a dM event fired? =='
-- The fourth exception route. Even where the leg designation says SELL, a
-- completion is only a SELL if we were already LONG. Flat means there is
-- nothing to complete; SHORT inverts the action. Our fills exist only
-- 09-06..09-10, so this window is narrower than the dM population above and
-- the FLAT cohort here is partly an artifact of that -- reported, not hidden.
WITH ourfills AS (
  SELECT o.us_market_slug, o.done_at,
         CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END AS signed_sh
    FROM mirror_orders o
   WHERE o.filled > 0 AND o.done_at IS NOT NULL
), pos AS (
  SELECT us_market_slug, done_at,
         sum(signed_sh) OVER (PARTITION BY us_market_slug ORDER BY done_at
                              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS net_after
    FROM ourfills
), mapped AS (
  SELECT DISTINCT ON (condition_id)
         condition_id, us_market_slug, long_asset, other_asset
    FROM mirror_books
   WHERE us_market_slug IS NOT NULL AND long_asset IS NOT NULL
   ORDER BY condition_id, opened_at
), his AS (
  SELECT t.condition_id, t.ts, t.asset, t.size::float8 AS sh, t.price::float8 AS px,
         sum(CASE WHEN t.outcome_index = 0 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN t.outcome_index = 1 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), hdm AS (
  SELECT condition_id, ts, asset, sh, px,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM his WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), ev AS (
  SELECT h.condition_id, h.ts, h.asset, h.sh, h.px, h.d_m,
         (h.asset = m.other_asset) AS on_other_leg,
         (SELECT p.net_after FROM pos p
           WHERE p.us_market_slug = m.us_market_slug AND p.done_at <= h.ts
           ORDER BY p.done_at DESC LIMIT 1) AS bettor_pre_net
    FROM hdm h JOIN mapped m ON m.condition_id = h.condition_id
   WHERE h.d_m > 0.000001
)
SELECT CASE WHEN bettor_pre_net IS NULL OR abs(bettor_pre_net) < 0.5
              THEN '1 FLAT -- nothing to complete (a round trip, not a completion)'
            WHEN bettor_pre_net > 0
              THEN '2 LONG -- a completion on the other leg IS a SELL'
            ELSE '3 SHORT -- the completion INVERTS to a BUY'
       END AS bettor_pre_position,
       CASE WHEN on_other_leg THEN 'his fill on OTHER leg' ELSE 'his fill on LONG leg' END AS leg,
       count(*) AS dM_events,
       round((100.0 * count(*) / sum(count(*)) OVER ())::numeric, 2) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS dM_notional,
       round(avg(bettor_pre_net)::numeric, 1) AS mean_pre_net
  FROM ev GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 7. CHECK B worked examples: YES-matched, NO-matched, partial =='
WITH mapped AS (
  SELECT DISTINCT ON (condition_id)
         condition_id, us_market_slug, long_asset, other_asset
    FROM mirror_books
   WHERE us_market_slug IS NOT NULL AND long_asset IS NOT NULL
   ORDER BY condition_id, opened_at
), his AS (
  SELECT t.condition_id, t.ts, t.outcome_index, t.size::float8 AS sh, t.price::float8 AS px,
         sum(CASE WHEN t.outcome_index = 0 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN t.outcome_index = 1 THEN t.size::float8 ELSE 0 END)
           OVER (PARTITION BY t.condition_id ORDER BY t.ts
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-09-06 00:00Z' AND t.ts < timestamptz '2026-09-11 00:00Z'
), hdm AS (
  SELECT condition_id, ts, asset, outcome_index, sh, px, cy, cn,
         COALESCE(lag(cy) OVER w, 0) AS pre_y, COALESCE(lag(cn) OVER w, 0) AS pre_n,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM his WINDOW w AS (PARTITION BY condition_id ORDER BY ts)
), lab AS (
  SELECT h.*, m.us_market_slug, (h.asset = m.other_asset) AS on_other_leg,
         CASE WHEN h.d_m >= h.sh - 0.000001 AND h.outcome_index = 0 THEN 'A YES fill, FULLY matched'
              WHEN h.d_m >= h.sh - 0.000001 AND h.outcome_index = 1 THEN 'B NO fill, FULLY matched'
              ELSE 'C PARTIAL -- only part of the fill creates dM' END AS example_class,
         row_number() OVER (PARTITION BY
           CASE WHEN h.d_m >= h.sh - 0.000001 AND h.outcome_index = 0 THEN 'A'
                WHEN h.d_m >= h.sh - 0.000001 AND h.outcome_index = 1 THEN 'B'
                ELSE 'C' END ORDER BY h.d_m DESC) AS rn
    FROM hdm h JOIN mapped m ON m.condition_id = h.condition_id
   WHERE h.d_m > 0.000001
)
-- The required price is leg-dependent, which is the whole point of check B.
-- His completing fill on the OTHER leg at p makes our SELL of the long token
-- worth 1 - p: selling the long at 1 - p is the same trade as buying the other
-- at p. If his completing fill is on the LONG leg, there is no sell to price --
-- the action is a BUY at his own p, and the cell says so rather than printing
-- a number that would be read as a sell level.
SELECT example_class, left(condition_id, 12) AS condition, us_market_slug,
       to_char(ts, 'MM-DD HH24:MI:SS') AS fill_ts,
       outcome_index AS rn1_fill_leg,
       CASE WHEN on_other_leg THEN 'OTHER' ELSE 'LONG' END AS venue_leg_of_his_fill,
       round(sh::numeric, 2) AS rn1_fill_shares, px AS rn1_fill_price,
       round(pre_y::numeric, 2) AS rn1_pre_YES, round(pre_n::numeric, 2) AS rn1_pre_NO,
       round(cy::numeric, 2) AS rn1_post_YES, round(cn::numeric, 2) AS rn1_post_NO,
       round(d_m::numeric, 2) AS delta_M,
       round((d_m * 0.10)::numeric, 2) AS target_copy_qty_at_10pct,
       CASE WHEN on_other_leg THEN 'SELL long @ ' || round((1.0 - px)::numeric, 4)::text
            ELSE                    'BUY long @ '  || round(px::numeric, 4)::text
       END AS required_bettor_action_and_price
  FROM lab WHERE rn <= 3 ORDER BY example_class, delta_M DESC;
