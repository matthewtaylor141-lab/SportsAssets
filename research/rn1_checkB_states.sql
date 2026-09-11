-- ============================================================================
-- CHECK B: two state machines, gated by two validity flags
-- (2026-09-11, read-only.)
--
-- THE TWO STATE MACHINES, and they are never mixed:
--
--   CONTINUOUS_TARGET_STATE   what a corrected mirror CONTINUOUSLY TRACKING the
--       signed target would request. cf state propagates in RN1 event order,
--       cf_position_after := cf_target_after. This is the execution-feasibility
--       question over 37 days.
--   ACTUAL_STATE              what a corrected decision would have requested
--       FROM THE HISTORICAL STATE THE DEFECTIVE PRODUCTION SYSTEM ACTUALLY
--       CREATED. Confined to 09-06..09-10, the only days we truly held
--       positions.
--
-- NEITHER IS THE A->D P&L COUNTERFACTUAL. Both classify a REQUESTED action.
-- Execution -- depth, fills, slippage -- comes later and is not modelled here.
-- This pass does not reduce state for missing depth or failed fills.
--
-- VALIDITY FLAG 1, INITIALIZATION. CALENDAR COVERAGE IS NOT PROVEN ZERO. A
-- first retained fill after a boundary with no missing days proves only that
-- NO EARLIER RETAINED FILL EXISTS DURING A CALENDAR-COVERED INTERVAL. It does
-- NOT prove RN1's inventory was zero, because this system has already been
-- shown to undercount fills WHILE the calendar looked complete -- D1, where
-- one whale's position read 92,145 from our fills against the venue's 55,993.
-- Calendar completeness and fill-level completeness are different claims.
--
-- So initialization is graded by EVIDENCE LEVEL:
--   INIT_PROVEN_ZERO         requires an INDEPENDENT fill-completeness proof,
--                            not merely a calendar. See below for what that is
--                            here, and why it is scarce.
--   INIT_COVERAGE_SUPPORTED  first retained fill is after the calendar-complete
--                            boundary, but fill-level completeness is NOT
--                            independently proven.
--   INIT_LEFT_CENSORED       the condition or the exposure may predate complete
--                            coverage.
--   INIT_UNKNOWN             insufficient evidence.
--
-- WHAT PROOF IS ACTUALLY AVAILABLE, established by reading the schema and the
-- worker rather than assumed. There is NO condition_created_at and NO market
-- open/start timestamp anywhere for these conditions: `markets` carries only
-- title/slug/sport/closed/resolved/resolved_at/updated_at, and updated_at is
-- OUR upsert clock (DEFAULT now()), not market creation. Statement 2 proves
-- that from information_schema rather than from my reading of a migration.
-- Requirement (1) as stated -- condition did not exist before capture was
-- complete -- therefore cannot be met from stored data at all.
--
-- That leaves the alternative: an independent fill-completeness proof. One
-- exists, and exactly one. In _read_market (mirror_live.py:6555) his_long /
-- his_other are `mi.net_positions(fills)` -- OUR OWN FILLS, so they prove
-- nothing about themselves -- while `snap` comes from _snapshot(), a genuine
-- independent read of his positions. Both land on the same mirror_books row as
-- his_long/his_other and snap_long/snap_other. Where they AGREE, our fill
-- reconstruction equals an independently observed position, which is a real
-- fill-completeness proof for that condition: a missing earlier fill would
-- leave the fills-derived figure short of the observed one. The caveat is
-- stated rather than hidden -- exactly offsetting errors would also agree --
-- and the comparison uses SIGNED all-fill positions (net_positions counts
-- SELLs as negative), not the BUY-only cumulative the dM construction uses.
--
-- Expect this to be scarce: snapshots exist only for markets we actually
-- opened, over 09-06..09-10, and only when the read was fresh. If
-- INIT_PROVEN_ZERO is tiny or empty, that is reported as such and
-- COVERAGE_SUPPORTED is NOT promoted to known.
--
-- VALIDITY FLAG 2, DESIGNATION. designation_at <= event_at, always. Where no
-- causal long_asset existed by the event, the action stays DESIGNATION_UNKNOWN
-- and is NEVER synthesised from a later book or refusal row.
--
-- THE HEADLINE is reported TWICE where the sample permits: PROVEN_ZERO with
-- causal designation, and PROVEN_ZERO-or-COVERAGE_SUPPORTED with causal
-- designation. The broader populations sit beside them as labelled
-- sensitivities, and the excluded initialization-uncertain population is
-- carried with its own event count, dM shares and matched notional so nothing
-- disappears silently.
--
-- THE GRANULARITY, from code. analytics/mirror.py:280 in target_shares:
--     tgt = int(raw) if raw >= 0 else -int(-raw)      # toward zero, whole shares
-- The target itself is whole shares, truncated toward zero, and Postgres
-- trunc() matches that exactly. So HOLD is a real venue state -- a fill too
-- small to move the integer target -- not a tolerance band.
--
-- THE ALGEBRAIC EXPECTATION IS TESTED, NOT ASSUMED. Statement 6 checks directly
-- that sign(required_change) agrees with sign(rn1_net_after - rn1_net_before)
-- except where truncation produces HOLD. Note that cf_position_before is the
-- PREVIOUS EVENT'S cf_target_after via lag() -- not recomputed from this
-- event's designation -- precisely so that a designation that CHANGED between
-- two events shows up as a disagreement instead of being silently smoothed
-- over. Any non-HOLD disagreement is a bug or a state/designation
-- discontinuity, and statement 6 splits it by whether the designation moved.
--
-- Read-only: ten SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. EVIDENCE: where does retained coverage actually become complete? =='
-- The initialization boundary must be measured. Per feed: its window, and the
-- daily fill counts across the suspected seam, so a gap is visible as a gap.
SELECT t.source,
       to_char(min(t.ts), 'YYYY-MM-DD HH24:MI') AS first_ts,
       to_char(max(t.ts), 'YYYY-MM-DD HH24:MI') AS last_ts,
       count(*) AS fills,
       count(DISTINCT t.ts::date) AS distinct_days,
       (max(t.ts)::date - min(t.ts)::date) + 1 AS span_days,
       ((max(t.ts)::date - min(t.ts)::date) + 1) - count(DISTINCT t.ts::date)
         AS MISSING_DAYS_INSIDE_ITS_OWN_WINDOW
  FROM trades t JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
   AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
 GROUP BY 1 ORDER BY 2;


\echo '== 2a. DOES MARKET-OPEN METADATA EXIST AT ALL? (from information_schema) =='
-- Proving the absence from the catalogue itself, not from my reading of a
-- migration file. If no creation/open/start column is listed here, then
-- PROVEN_ZERO requirement (1) cannot be satisfied from stored data and the
-- independent-completeness route in 2b is the only one available.
SELECT c.table_name, c.column_name, c.data_type,
       (c.column_name ~* '(creat|open|start|begin|launch)') AS looks_like_market_open
  FROM information_schema.columns c
 WHERE c.table_schema = 'public'
   AND c.table_name IN ('markets', 'market_tokens')
 ORDER BY c.table_name, c.ordinal_position;


\echo '== 2b. COVERAGE RATE of what metadata does exist, over RN1 conditions =='
WITH rn AS (
  SELECT DISTINCT t.condition_id
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.condition_id IS NOT NULL
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT count(*) AS rn1_conditions_in_window,
       count(m.condition_id) AS present_in_markets,
       round((100.0 * count(m.condition_id) / NULLIF(count(*), 0))::numeric, 2) AS pct_in_markets,
       count(m.resolved_at) AS with_resolved_at,
       count(m.updated_at) AS with_updated_at_OUR_UPSERT_CLOCK,
       0 AS with_condition_created_at_NO_SUCH_COLUMN,
       count(*) FILTER (WHERE m.condition_id IS NULL) AS missing_from_catalogue
  FROM rn LEFT JOIN markets m ON m.condition_id = rn.condition_id;


\echo '== 2c. THE ONLY INDEPENDENT FILL-COMPLETENESS EVIDENCE: fills vs snapshot =='
-- his_long/his_other = mi.net_positions(OUR fills). snap_long/snap_other = an
-- independent position read. Agreement is the proof; disagreement is the D1
-- failure mode restated. Tolerance: 1 whole share or 0.5%, whichever is larger.
SELECT count(*) AS book_rows,
       count(*) FILTER (WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL)
         AS rows_with_independent_snapshot,
       count(DISTINCT condition_id) FILTER (WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL)
         AS CONDITIONS_WITH_ANY_SNAPSHOT,
       count(DISTINCT condition_id) FILTER (
         WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
           AND abs(COALESCE(his_long, 0) - snap_long)
                 <= GREATEST(1.0, 0.005 * abs(snap_long))
           AND abs(COALESCE(his_other, 0) - snap_other)
                 <= GREATEST(1.0, 0.005 * abs(snap_other)))
         AS CONDITIONS_WHERE_FILLS_AGREE_WITH_SNAPSHOT,
       round(percentile_cont(0.5) WITHIN GROUP (
         ORDER BY abs(COALESCE(his_long, 0) - snap_long))::numeric, 2) AS p50_abs_long_gap,
       round(percentile_cont(0.95) WITHIN GROUP (
         ORDER BY abs(COALESCE(his_long, 0) - snap_long))::numeric, 2) AS p95_abs_long_gap,
       to_char(min(opened_at), 'MM-DD') AS snapshot_window_from,
       to_char(max(opened_at), 'MM-DD') AS snapshot_window_to
  FROM mirror_books;


\echo '== 3. VALIDITY FLAG 1: initialization EVIDENCE LEVEL, two boundaries =='
-- b1 = 2026-07-24, the venue poll feed's start: the first moment a continuous
--      feed exists, so absence of an earlier fill is informative from there.
-- b2 = 2026-08-10, the chain feed's start: stricter, two independent feeds.
-- PROVEN_ZERO additionally requires the independent snapshot agreement of 2c.
WITH proven AS (
  SELECT DISTINCT condition_id FROM mirror_books
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
     AND abs(COALESCE(his_long, 0) - snap_long) <= GREATEST(1.0, 0.005 * abs(snap_long))
     AND abs(COALESCE(his_other, 0) - snap_other) <= GREATEST(1.0, 0.005 * abs(snap_other))
), f AS (
  SELECT t.condition_id, t.ts, t.size::float8 AS sh, t.price::float8 AS px,
         t.tx_hash, t.asset, t.outcome_index
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), pc AS (
  SELECT condition_id, min(ts) AS first_fill, max(ts) AS last_fill FROM f GROUP BY 1
), gs AS (SELECT min(ts) AS data_start FROM f
), rn AS (
  SELECT f.condition_id, f.ts,
         sum(CASE WHEN f.outcome_index = 0 THEN f.sh ELSE 0 END)
           OVER (PARTITION BY f.condition_id ORDER BY f.ts, f.tx_hash, f.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN f.outcome_index = 1 THEN f.sh ELSE 0 END)
           OVER (PARTITION BY f.condition_id ORDER BY f.ts, f.tx_hash, f.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn,
         f.px, f.tx_hash, f.asset
    FROM f
), dm AS (
  SELECT condition_id, ts, px,
         GREATEST(LEAST(cy, cn)
                  - COALESCE(LEAST(lag(cy) OVER w, lag(cn) OVER w), 0), 0) AS d_m
    FROM rn WINDOW w AS (PARTITION BY condition_id ORDER BY ts, tx_hash, asset)
), b AS (
  SELECT * FROM (VALUES
    ('b1 2026-07-24 venue poll start', timestamptz '2026-07-24 00:00Z'),
    ('b2 2026-08-10 chain start',      timestamptz '2026-08-10 00:00Z')) AS v(label, cut)
)
SELECT b.label AS coverage_complete_boundary,
       CASE WHEN pc.first_fill >= b.cut
                 AND pc.condition_id IN (SELECT condition_id FROM proven)
              THEN 'A INIT_PROVEN_ZERO (independent fill-completeness proof)'
            WHEN pc.first_fill >= b.cut
              THEN 'B INIT_COVERAGE_SUPPORTED (calendar only, NOT proven)'
            WHEN pc.first_fill <= (SELECT data_start FROM gs) + interval '24 hours'
              THEN 'D INIT_UNKNOWN (at the edge of all retained data)'
            ELSE 'C INIT_LEFT_CENSORED (exposure may predate complete coverage)' END AS init_class,
       count(DISTINCT pc.condition_id) AS conditions,
       count(d.*) AS events_in_window,
       round(sum(d.d_m)::numeric, 0) AS dM_shares,
       round(sum(d.d_m * d.px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d.d_m) / NULLIF(sum(sum(d.d_m)) OVER (PARTITION BY b.label), 0))::numeric, 2)
         AS pct_dM_within_boundary
  FROM b CROSS JOIN pc
  JOIN dm d ON d.condition_id = pc.condition_id AND d.ts >= timestamptz '2026-08-05 00:00Z'
 WHERE pc.last_fill >= timestamptz '2026-08-05 00:00Z'
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 4. VALIDITY FLAG 2: causal-designation coverage, by initialization level =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, opened_at AS at FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, at FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), proven AS (
  SELECT DISTINCT condition_id FROM mirror_books
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
     AND abs(COALESCE(his_long, 0) - snap_long) <= GREATEST(1.0, 0.005 * abs(snap_long))
     AND abs(COALESCE(his_other, 0) - snap_other) <= GREATEST(1.0, 0.005 * abs(snap_other))
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset, t.outcome_index,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), pc AS (SELECT condition_id, min(ts) AS first_fill FROM canon GROUP BY 1
), rn AS (
  SELECT c.condition_id, c.ts, c.px, c.tx_hash, c.asset,
         sum(CASE WHEN c.outcome_index = 0 THEN c.sh ELSE 0 END)
           OVER (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cy,
         sum(CASE WHEN c.outcome_index = 1 THEN c.sh ELSE 0 END)
           OVER (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cn
    FROM canon c
), ev AS (
  SELECT r.condition_id, r.ts, r.px,
         GREATEST(LEAST(r.cy, r.cn)
                  - COALESCE(LEAST(lag(r.cy) OVER w, lag(r.cn) OVER w), 0), 0) AS d_m
    FROM rn r WINDOW w AS (PARTITION BY r.condition_id ORDER BY r.ts, r.tx_hash, r.asset)
), g AS (
  SELECT e.*, pc.first_fill,
         EXISTS (SELECT 1 FROM desig_rows d
                  WHERE d.condition_id = e.condition_id AND d.at <= e.ts) AS causal_desig,
         (pc.first_fill >= timestamptz '2026-07-24 00:00Z'
          AND e.condition_id IN (SELECT condition_id FROM proven)) AS init_proven,
         (pc.first_fill >= timestamptz '2026-07-24 00:00Z') AS init_cov_supported
    FROM ev e JOIN pc ON pc.condition_id = e.condition_id
   WHERE e.ts >= timestamptz '2026-08-05 00:00Z'
)
SELECT 'ALL causally classifiable' AS population,
       round((100.0 * count(*) FILTER (WHERE causal_desig) / count(*))::numeric, 2) AS pct_events_with_causal_designation,
       round((100.0 * sum(d_m) FILTER (WHERE causal_desig) / NULLIF(sum(d_m), 0))::numeric, 2) AS pct_dM_with_causal_designation,
       round((100.0 * sum(d_m * px) FILTER (WHERE causal_desig) / NULLIF(sum(d_m * px), 0))::numeric, 2) AS pct_matched_notional_with_causal,
       count(*) AS events, round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM g
UNION ALL
SELECT 'INIT_PROVEN_ZERO only',
       round((100.0 * count(*) FILTER (WHERE causal_desig) / NULLIF(count(*), 0))::numeric, 2),
       round((100.0 * sum(d_m) FILTER (WHERE causal_desig) / NULLIF(sum(d_m), 0))::numeric, 2),
       round((100.0 * sum(d_m * px) FILTER (WHERE causal_desig) / NULLIF(sum(d_m * px), 0))::numeric, 2),
       count(*), round(sum(d_m)::numeric, 0), round(sum(d_m * px)::numeric, 0)
  FROM g WHERE init_proven
UNION ALL
SELECT 'PROVEN_ZERO or COVERAGE_SUPPORTED',
       round((100.0 * count(*) FILTER (WHERE causal_desig) / NULLIF(count(*), 0))::numeric, 2),
       round((100.0 * sum(d_m) FILTER (WHERE causal_desig) / NULLIF(sum(d_m), 0))::numeric, 2),
       round((100.0 * sum(d_m * px) FILTER (WHERE causal_desig) / NULLIF(sum(d_m * px), 0))::numeric, 2),
       count(*), round(sum(d_m)::numeric, 0), round(sum(d_m * px)::numeric, 0)
  FROM g WHERE init_cov_supported
UNION ALL
SELECT 'EXCLUDED: initialization-uncertain',
       NULL, NULL, NULL,
       count(*), round(sum(d_m)::numeric, 0), round(sum(d_m * px)::numeric, 0)
  FROM g WHERE NOT init_cov_supported;


\echo '== 5. HEADLINE: CONTINUOUS_TARGET_STATE action mix, by evidence population =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), proven AS (
  SELECT DISTINCT condition_id FROM mirror_books
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
     AND abs(COALESCE(his_long, 0) - snap_long) <= GREATEST(1.0, 0.005 * abs(snap_long))
     AND abs(COALESCE(his_other, 0) - snap_other) <= GREATEST(1.0, 0.005 * abs(snap_other))
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2, min(ts) AS first_fill
    FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px,
         l.a1, l.a2, l.first_fill,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e
), c AS (
  -- cf_position_before is the PREVIOUS event's target, carried forward; only
  -- the first in-window event falls back to the reconstructed initialization.
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before,
         (t.first_fill >= timestamptz '2026-07-24 00:00Z'
          AND t.condition_id IN (SELECT condition_id FROM proven)) AS init_proven,
         (t.first_fill >= timestamptz '2026-07-24 00:00Z') AS init_cov_supported
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
), u AS (
  SELECT 'A HEADLINE-1 causal designation + PROVEN_ZERO' AS population, * FROM c
   WHERE long_asset IS NOT NULL AND init_proven
  UNION ALL
  SELECT 'B HEADLINE-2 causal designation + PROVEN_or_COVERAGE_SUPPORTED', * FROM c
   WHERE long_asset IS NOT NULL AND init_cov_supported
  UNION ALL
  SELECT 'C sensitivity: causal designation, any init', * FROM c WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT 'D sensitivity: every event in window', * FROM c
)
SELECT population,
       CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN'
            WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       count(*) AS dM_events,
       round((100.0 * count(*) / sum(count(*)) OVER (PARTITION BY population))::numeric, 2) AS pct_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (PARTITION BY population), 0))::numeric, 2) AS PCT_dM,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM u WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 6. ACTUAL_STATE 09-06..09-10: corrected rule from the real book =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, us_market_slug AS slug, COALESCE(ratio, 0.10) AS ratio,
         opened_at AS at FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, us_slug, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2, min(ts) AS first_fill
    FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2, l.first_fill,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio, d.slug,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.slug FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-09-06 00:00Z' AND c.ts < timestamptz '2026-09-11 00:00Z'
), a AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         trunc(e.ratio * CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
                              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END) AS tgt_after,
         COALESCE((SELECT sum(CASE WHEN o.side = 'BUY_LONG' THEN o.filled ELSE -o.filled END)
                     FROM mirror_orders o
                    WHERE o.us_market_slug = e.slug AND o.filled > 0
                      AND o.done_at IS NOT NULL AND o.done_at <= e.ts), 0) AS bpp,
         (e.first_fill >= timestamptz '2026-07-24 00:00Z') AS init_cov_supported
    FROM ev e
)
SELECT CASE WHEN long_asset IS NULL THEN '0 DESIGNATION_UNKNOWN'
            WHEN trunc(tgt_after - bpp) > 0 THEN '1 BUY'
            WHEN trunc(tgt_after - bpp) < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       CASE WHEN abs(bpp) < 1.0 THEN 'pre-position FLAT'
            WHEN bpp > 0 THEN 'pre-position LONG' ELSE 'pre-position SHORT' END AS pre_position,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       count(*) FILTER (WHERE init_cov_supported) AS of_which_COVERAGE_SUPPORTED,
       round(avg(bpp)::numeric, 1) AS mean_actual_pre_position
  FROM a WHERE d_m > 0.000001
 GROUP BY 1, 2 ORDER BY 1, 2;


\echo '== 7. THE ALGEBRA TEST: sign(required_change) vs sign(net_after - net_before) =='
-- Truncation may turn a small move into HOLD; that is expected and is reported
-- separately. Any NON-HOLD disagreement is a bug or a state/designation
-- discontinuity, so the split by "did the designation change at this event"
-- names which of the two it is.
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), proven AS (
  SELECT DISTINCT condition_id FROM mirror_books
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
     AND abs(COALESCE(his_long, 0) - snap_long) <= GREATEST(1.0, 0.005 * abs(snap_long))
     AND abs(COALESCE(his_other, 0) - snap_other) <= GREATEST(1.0, 0.005 * abs(snap_other))
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2 FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), ev AS (
  SELECT c.*, d.long_asset, d.ratio,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e
), c AS (
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before,
         lag(t.long_asset) OVER w AS prev_long_asset
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
)
SELECT sign(cf_target_after - cf_position_before) AS sign_required_change,
       sign(net_after - net_before) AS sign_net_change,
       CASE WHEN prev_long_asset IS NULL THEN 'first event in market'
            WHEN prev_long_asset IS DISTINCT FROM long_asset
              THEN 'DESIGNATION CHANGED at this event'
            ELSE 'designation stable' END AS designation_continuity,
       CASE WHEN sign(cf_target_after - cf_position_before) = sign(net_after - net_before)
              THEN 'A AGREES'
            WHEN cf_target_after - cf_position_before = 0
              THEN 'B HOLD from whole-share truncation (expected)'
            WHEN prev_long_asset IS DISTINCT FROM long_asset
              THEN 'C NON-HOLD DISAGREEMENT: DESIGNATION_CHANGED (explained)'
            ELSE 'D NON-HOLD DISAGREEMENT: UNEXPLAINED -- TREAT AS A BUG UNTIL RECONCILED'
       END AS verdict,
       count(*) AS events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional
  FROM c WHERE long_asset IS NOT NULL
 GROUP BY 1, 2, 3, 4 ORDER BY 4, 1, 2;


\echo '== 8. HEADLINE population: dM action mix by designation class =='
WITH desig_rows AS (
  SELECT condition_id, long_asset, COALESCE(ratio, 0.10) AS ratio, opened_at AS at,
         'book row -- branch not recorded' AS long_from
    FROM mirror_books WHERE long_asset IS NOT NULL
  UNION ALL
  SELECT condition_id, long_asset, 0.10, at,
         CASE WHEN long_from = 'catalogue' THEN 'catalogue'
              WHEN long_from IS NULL OR long_from = '' THEN 'mapper -- unlabelled'
              ELSE long_from END
    FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
), proven AS (
  SELECT DISTINCT condition_id FROM mirror_books
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL
     AND abs(COALESCE(his_long, 0) - snap_long) <= GREATEST(1.0, 0.005 * abs(snap_long))
     AND abs(COALESCE(his_other, 0) - snap_other) <= GREATEST(1.0, 0.005 * abs(snap_other))
), first_desig AS (
  SELECT condition_id, min(at) AS first_at FROM desig_rows GROUP BY 1
), f AS (
  SELECT t.id, t.condition_id, t.ts, t.tx_hash, t.asset,
         t.size::float8 AS sh, t.price::float8 AS px,
         CASE WHEN t.source IN ('poll', 'backfill') THEN 'venue' ELSE 'cash' END AS feed
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
), env AS (
  SELECT tx_hash, asset, CASE WHEN bool_or(feed = 'venue') THEN 'venue' ELSE 'cash' END AS cf
    FROM f GROUP BY 1, 2
), canon AS (
  SELECT f.* FROM f JOIN env e ON e.tx_hash = f.tx_hash AND e.asset = f.asset AND e.cf = f.feed
), legs AS (
  SELECT condition_id, min(asset) AS a1, max(asset) AS a2, min(ts) AS first_fill
    FROM canon GROUP BY 1
), cum AS (
  SELECT c.id, c.condition_id, c.ts, c.tx_hash, c.asset, c.sh, c.px, l.a1, l.a2, l.first_fill,
         sum(CASE WHEN c.asset = l.a1 THEN c.sh ELSE 0 END) OVER w AS cum1,
         sum(CASE WHEN c.asset = l.a2 THEN c.sh ELSE 0 END) OVER w AS cum2
    FROM canon c JOIN legs l ON l.condition_id = c.condition_id
  WINDOW w AS (PARTITION BY c.condition_id ORDER BY c.ts, c.tx_hash, c.asset
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
), dpos AS (
  SELECT DISTINCT ON (c.condition_id) c.condition_id, c.a1,
         c.cum1 AS c1_at_desig, c.cum2 AS c2_at_desig
    FROM cum c JOIN first_desig fd ON fd.condition_id = c.condition_id
   WHERE c.ts <= fd.first_at
   ORDER BY c.condition_id, c.ts DESC
), ev AS (
  SELECT c.*, d.long_asset, d.ratio, d.long_from,
         c.cum1 - CASE WHEN c.asset = c.a1 THEN c.sh ELSE 0 END AS pre1,
         c.cum2 - CASE WHEN c.asset = c.a2 THEN c.sh ELSE 0 END AS pre2
    FROM cum c
    LEFT JOIN LATERAL (
      SELECT dr.long_asset, dr.ratio, dr.long_from FROM desig_rows dr
       WHERE dr.condition_id = c.condition_id AND dr.at <= c.ts
       ORDER BY dr.at DESC LIMIT 1) d ON TRUE
   WHERE c.ts >= timestamptz '2026-08-05 00:00Z'
), t AS (
  SELECT e.*, dp.c1_at_desig, dp.c2_at_desig,
         GREATEST(LEAST(e.cum1, e.cum2) - LEAST(e.pre1, e.pre2), 0) AS d_m,
         CASE WHEN e.long_asset = e.a1 THEN e.cum1 - e.cum2
              WHEN e.long_asset = e.a2 THEN e.cum2 - e.cum1 END AS net_after,
         CASE WHEN e.long_asset = e.a1 THEN e.pre1 - e.pre2
              WHEN e.long_asset = e.a2 THEN e.pre2 - e.pre1 END AS net_before
    FROM ev e LEFT JOIN dpos dp ON dp.condition_id = e.condition_id
), c AS (
  SELECT t.*, trunc(t.ratio * t.net_after) AS cf_target_after,
         COALESCE(lag(trunc(t.ratio * t.net_after)) OVER w,
                  trunc(t.ratio * t.net_before)) AS cf_position_before
    FROM t WINDOW w AS (PARTITION BY t.condition_id ORDER BY t.ts, t.tx_hash, t.asset)
)
SELECT CASE WHEN long_from = 'catalogue' THEN '3 CATALOGUE_LONG'
            WHEN c1_at_desig IS NULL THEN '4 NO_POSITION_AT_DESIGNATION'
            WHEN (CASE WHEN long_asset = a1 THEN c1_at_desig ELSE c2_at_desig END)
               < (CASE WHEN long_asset = a1 THEN c2_at_desig ELSE c1_at_desig END)
              THEN '2 INTENT_DESIGNATED_LONG (proven)'
            ELSE '1 POSITION_CONSISTENT_LONG (not proven)' END AS designation_class,
       CASE WHEN cf_target_after - cf_position_before > 0 THEN '1 BUY'
            WHEN cf_target_after - cf_position_before < 0 THEN '2 SELL'
            ELSE '3 HOLD' END AS required_action,
       count(*) AS dM_events,
       round(sum(d_m)::numeric, 0) AS dM_shares,
       round(sum(d_m * px)::numeric, 0) AS matched_notional,
       round((100.0 * sum(d_m) / NULLIF(sum(sum(d_m)) OVER (), 0))::numeric, 2) AS PCT_dM
  FROM c
 WHERE d_m > 0.000001 AND long_asset IS NOT NULL
   AND first_fill >= timestamptz '2026-07-24 00:00Z'
   AND condition_id IN (SELECT condition_id FROM proven)
 GROUP BY 1, 2 ORDER BY 1, 2;
