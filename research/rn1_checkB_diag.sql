-- ============================================================================
-- WILL CHECK B RUN? Measure the two landmines BEFORE spending a timeout on it.
-- (2026-09-11, read-only.)
--
-- Check A cost three twenty-minute timeouts because I fixed causes I had
-- reasoned to and never measured the population or the plan. Run 36 measured it
-- in ten seconds and the real cause was a nested-loop rescan nobody had
-- guessed. Applying that lesson here BEFORE dispatching Check B.
--
-- Reading the migrations first found two sites in Check B with exactly the
-- shape that killed Check A -- a per-row lookup with no index behind it:
--
--   1  mirror_orders has NO index on us_market_slug. Its indexes are
--      (order_id), (book_id, placed_at DESC), a partial on state, and a unique
--      on open-per-book. The ACTUAL_STATE statement runs
--          (SELECT sum(...) FROM mirror_orders WHERE us_market_slug = e.slug
--             AND done_at <= e.ts)
--      once PER EVENT, so every event scans the whole table.
--
--   2  the designation LATERAL selects from a CTE union of mirror_books,
--      mirror_candidate_refusals and mirror_shadow. A CTE is materialised and
--      carries NO INDEXES AT ALL, so the lateral cannot do better than scan the
--      whole union per event -- O(events x designation rows) regardless of what
--      indexes those base tables have.
--
-- Whether either matters depends on sizes nobody has measured, so this measures
-- them. If mirror_shadow is large, the designation lateral has to be replaced
-- by an as-of carry-forward (union the events with the designations, sort once,
-- carry the last non-null forward with a window) rather than a per-row lookup.
-- Same for the position lookup.
--
-- Read-only: four statements, none of which runs Check B.
-- ============================================================================


\echo '== 1. TABLE SIZES: what do the per-row lookups actually have to scan? =='
SELECT t.relname AS table_name,
       to_char(c.reltuples::bigint, 'FM999,999,999') AS planner_row_estimate,
       pg_size_pretty(pg_total_relation_size(t.oid)) AS total_size,
       pg_size_pretty(pg_relation_size(t.oid)) AS heap_size
  FROM pg_class c JOIN pg_class t ON t.oid = c.oid
 WHERE t.relname IN ('mirror_shadow', 'mirror_candidate_refusals',
                     'mirror_books', 'mirror_orders', 'trades', 'copy_probes')
   AND t.relkind = 'r'
 ORDER BY pg_total_relation_size(t.oid) DESC;


\echo '== 2. EXACT COUNTS on the two landmine tables =='
SELECT (SELECT count(*) FROM mirror_orders) AS mirror_orders_rows,
       (SELECT count(*) FROM mirror_orders WHERE filled > 0 AND done_at IS NOT NULL)
         AS our_filled_orders,
       (SELECT count(DISTINCT us_market_slug) FROM mirror_orders) AS distinct_slugs,
       (SELECT count(*) FROM mirror_shadow) AS mirror_shadow_rows,
       (SELECT count(*) FROM mirror_shadow WHERE long_asset IS NOT NULL)
         AS shadow_designation_rows,
       (SELECT count(*) FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL)
         AS refusal_designation_rows,
       (SELECT count(*) FROM mirror_books WHERE long_asset IS NOT NULL)
         AS book_designation_rows;


\echo '== 3. ANCHOR SUPPLY: independent snapshots, and how many predate the window =='
SELECT count(*) AS rows_with_snapshot,
       count(DISTINCT condition_id) AS conditions_with_any_snapshot,
       count(*) FILTER (WHERE at <= timestamptz '2026-08-05 00:00Z')
         AS snapshot_rows_at_or_before_window_start,
       count(DISTINCT condition_id) FILTER (WHERE at <= timestamptz '2026-08-05 00:00Z')
         AS CONDITIONS_ANCHORABLE_AT_WINDOW_START,
       count(*) FILTER (WHERE snap_long = 0 AND snap_other = 0) AS anchored_zero_rows,
       to_char(min(at), 'MM-DD HH24:MI') AS first_snapshot,
       to_char(max(at), 'MM-DD HH24:MI') AS last_snapshot
  FROM mirror_shadow
 WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL;


\echo '== 4. HOW MANY RN1 CONDITIONS EVER RECEIVE A DESIGNATION AT ALL? =='
-- If this is small relative to 26,154 conditions, DESIGNATION_UNKNOWN dominates
-- and the causal-designation population is the binding constraint on check B,
-- whatever the performance turns out to be.
WITH rn AS (
  SELECT DISTINCT t.condition_id
    FROM trades t JOIN whales w ON w.id = t.whale_id
   WHERE lower(w.username) = 'rn1' AND t.side = 'BUY'
     AND t.condition_id IS NOT NULL AND t.outcome_index IN (0, 1)
     AND t.ts >= timestamptz '2026-08-05 00:00Z'
), d AS (
  SELECT DISTINCT condition_id FROM mirror_books WHERE long_asset IS NOT NULL
  UNION
  SELECT DISTINCT condition_id FROM mirror_candidate_refusals WHERE long_asset IS NOT NULL
  UNION
  SELECT DISTINCT condition_id FROM mirror_shadow WHERE long_asset IS NOT NULL
), a AS (
  SELECT DISTINCT condition_id FROM mirror_shadow
   WHERE snap_long IS NOT NULL AND snap_other IS NOT NULL AND long_asset IS NOT NULL
)
SELECT (SELECT count(*) FROM rn) AS rn1_conditions_in_window,
       (SELECT count(*) FROM d) AS conditions_with_any_designation,
       (SELECT count(*) FROM rn JOIN d USING (condition_id)) AS RN1_CONDITIONS_DESIGNATED,
       round((100.0 * (SELECT count(*) FROM rn JOIN d USING (condition_id))
              / NULLIF((SELECT count(*) FROM rn), 0))::numeric, 2) AS pct_designated,
       (SELECT count(*) FROM rn JOIN a USING (condition_id)) AS RN1_CONDITIONS_ANCHORABLE,
       round((100.0 * (SELECT count(*) FROM rn JOIN a USING (condition_id))
              / NULLIF((SELECT count(*) FROM rn), 0))::numeric, 2) AS pct_anchorable;
