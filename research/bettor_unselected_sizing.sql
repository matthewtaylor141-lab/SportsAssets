-- Why is every captured row marked SLICE_TRUNCATED?
--
-- The frozen rule sizes the rotation on an assumed ~9,700 eligible legs
-- against 277 slices and a cap of 40, giving ~35 per slice. Every row
-- written in the first two buckets carries slice_truncated = true, so
-- that assumption is wrong and the rotation is NOT covering the
-- universe: a stable within-slice ordering plus a binding cap draws the
-- same top-N markets every pass and never reaches the rest.
--
-- This measures the real eligible set, applying the frozen eligibility
-- rule in SQL exactly as bettor_state_capture.eligible applies it in
-- Python. Read only, descriptive, not one of the pre-registered tests.

SELECT 'A_PREMAP' AS section, k, v FROM (
    SELECT 'ROWS_IN_PREMAP' AS k, count(*)::text AS v FROM us_premap
    UNION ALL
    SELECT 'ROWS_FRESH_7200S',
           count(*)::text FROM us_premap
     WHERE updated_at > now() - interval '7200 seconds'
    UNION ALL
    -- The frozen ELIGIBILITY_RULE: identity resolvable, premap fresh,
    -- non-sport categories excluded.
    SELECT 'ELIGIBLE_LEGS',
           count(*)::text FROM us_premap
     WHERE updated_at > now() - interval '7200 seconds'
       AND market_slug IS NOT NULL
       AND event_slug IS NOT NULL
       AND side_norm IS NOT NULL
       AND coalesce(kind, '') NOT LIKE 'election\_%'
    UNION ALL
    SELECT 'ELIGIBLE_DISTINCT_IDENTIFIERS',
           count(DISTINCT identifier)::text FROM us_premap
     WHERE updated_at > now() - interval '7200 seconds'
       AND market_slug IS NOT NULL
       AND event_slug IS NOT NULL
       AND side_norm IS NOT NULL
       AND coalesce(kind, '') NOT LIKE 'election\_%'
    UNION ALL
    SELECT 'ELIGIBLE_DISTINCT_MARKETS',
           count(DISTINCT market_slug)::text FROM us_premap
     WHERE updated_at > now() - interval '7200 seconds'
       AND market_slug IS NOT NULL
       AND event_slug IS NOT NULL
       AND side_norm IS NOT NULL
       AND coalesce(kind, '') NOT LIKE 'election\_%'
    UNION ALL
    SELECT 'ELIGIBLE_DISTINCT_EVENTS',
           count(DISTINCT event_slug)::text FROM us_premap
     WHERE updated_at > now() - interval '7200 seconds'
       AND market_slug IS NOT NULL
       AND event_slug IS NOT NULL
       AND side_norm IS NOT NULL
       AND coalesce(kind, '') NOT LIKE 'election\_%'
    UNION ALL
    -- THE NUMBER THE ROTATION ACTUALLY NEEDS: eligible legs divided by
    -- slices. If this exceeds the per-cycle cap, the cap binds on every
    -- pass and the rotation is a fixed panel.
    SELECT 'IMPLIED_PER_SLICE_AT_277',
           round(count(*)::numeric / 277, 1)::text FROM us_premap
     WHERE updated_at > now() - interval '7200 seconds'
       AND market_slug IS NOT NULL
       AND event_slug IS NOT NULL
       AND side_norm IS NOT NULL
       AND coalesce(kind, '') NOT LIKE 'election\_%'
) a

UNION ALL

-- What the excluded rows are excluded FOR, so a rule that is quietly
-- dropping most of the venue is visible rather than assumed.
SELECT 'B_INELIGIBLE',
       CASE
         WHEN market_slug IS NULL THEN 'NO_VENUE_MARKET_SLUG'
         WHEN event_slug  IS NULL THEN 'NO_VENUE_EVENT_SLUG'
         WHEN side_norm   IS NULL THEN 'NO_VENUE_SIDE'
         WHEN coalesce(kind, '') LIKE 'election\_%' THEN 'EXCLUDED_ELECTION'
         ELSE 'ELIGIBLE'
       END,
       count(*)::text
  FROM us_premap
 WHERE updated_at > now() - interval '7200 seconds'
 GROUP BY 2

UNION ALL

-- Freshness, because the 7200s window is itself part of the frozen
-- rule and a premap that refreshes slowly would shrink the frame for
-- reasons unrelated to the venue.
SELECT 'C_FRESHNESS',
       CASE
         WHEN updated_at > now() - interval '600 seconds'  THEN 'LE_10MIN'
         WHEN updated_at > now() - interval '3600 seconds' THEN 'LE_1H'
         WHEN updated_at > now() - interval '7200 seconds' THEN 'LE_2H'
         ELSE 'STALE_BEYOND_RULE'
       END,
       count(*)::text
  FROM us_premap
 GROUP BY 2

ORDER BY 1, 2
