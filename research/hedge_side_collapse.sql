-- HOW MANY SIDES DOES THE SIBLING READ ITSELF ALREADY DROP?
--
-- Run 268 established the identity model: 2.000 rows per market_slug, the two
-- rows always differ on `side_norm` and on `intent` (26,475 of 26,475), never
-- on `line` or `sports_type`, and `identifier` is 1:1 with the slug so it is
-- NOT the side key. `intent` takes exactly two values --
-- ORDER_INTENT_BUY_LONG and ORDER_INTENT_BUY_SHORT -- one row each per slug.
--
-- SIBLINGS_SQL is `DISTINCT ON (market_slug, coalesce(team_abbr, side_norm))`.
-- Run 268 also measured that `team_abbr` varies within the slug on only 6,085
-- of 26,475 slugs. So wherever team_abbr is PRESENT and EQUAL on both rows,
-- that DISTINCT ON collapses the two sides to ONE and the sibling read never
-- offers the other side at all -- a drop before any identity keying, upstream
-- of the condition_id collapse §4 names.
--
-- This counts it exactly, and separates the three cases so the fix is aimed at
-- the real one rather than at the one I guessed.
--
-- Read-only. Only \echo.

\echo == 0 THE THREE CASES FOR coalesce(team_abbr, side_norm) WITHIN A SLUG ==
WITH per_slug AS (
  SELECT market_slug,
         count(*)                                              AS n,
         count(*) FILTER (WHERE team_abbr IS NULL
                             OR team_abbr = '')                AS abbr_absent,
         count(DISTINCT coalesce(nullif(team_abbr, ''), side_norm)) AS d_key,
         count(DISTINCT side_norm)                             AS d_side,
         count(DISTINCT intent)                                AS d_intent
    FROM us_premap
   GROUP BY market_slug)
SELECT CASE WHEN abbr_absent = n THEN 'team_abbr absent on both rows'
            WHEN abbr_absent = 0 AND d_key > 1 THEN 'team_abbr present and DIFFERENT'
            WHEN abbr_absent = 0 AND d_key = 1 THEN 'team_abbr present and EQUAL -- DISTINCT ON DROPS A SIDE'
            ELSE 'team_abbr present on one row only'
       END                                          AS case_,
       count(*)                                     AS slugs,
       sum(n)                                       AS rows,
       count(*) FILTER (WHERE d_key = 1)            AS slugs_where_the_distinct_on_key_collapses,
       count(*) FILTER (WHERE d_side > 1)           AS side_norm_still_distinguishes,
       count(*) FILTER (WHERE d_intent > 1)         AS intent_still_distinguishes
  FROM per_slug
 GROUP BY 1
 ORDER BY 2 DESC;

\echo == 1 THE SAME, RESTRICTED TO FUTURE FIXTURES (the tradable window) ==
WITH per_slug AS (
  SELECT market_slug,
         count(DISTINCT coalesce(nullif(team_abbr, ''), side_norm)) AS d_key,
         count(DISTINCT side_norm)                                  AS d_side,
         count(DISTINCT intent)                                     AS d_intent
    FROM us_premap
   WHERE game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug)
SELECT count(*)                                  AS future_slugs,
       count(*) FILTER (WHERE d_key = 1)         AS a_side_is_dropped_by_the_current_key,
       count(*) FILTER (WHERE d_side > 1)        AS side_norm_would_keep_both,
       count(*) FILTER (WHERE d_intent > 1)      AS intent_would_keep_both
  FROM per_slug;

\echo == 2 EXAMPLES OF THE COLLAPSING CASE, PRINTED IN FULL ==
WITH bad AS (
  SELECT market_slug
    FROM us_premap
   WHERE game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug
  HAVING count(DISTINCT coalesce(nullif(team_abbr, ''), side_norm)) = 1
     AND count(*) = 2
   LIMIT 4)
SELECT u.market_slug, u.side_norm, u.team_abbr, u.intent, u.signed, u.line,
       u.sports_type
  FROM bad b JOIN us_premap u USING (market_slug)
 ORDER BY u.market_slug, u.intent;

\echo == 3 IS (market_slug, intent) A UNIQUE KEY OVER THE WHOLE TABLE ==
-- The candidate identity the repair will use. If this is not unique the key is
-- wrong and the run says so rather than the code assuming it.
SELECT count(*)                                        AS rows,
       count(DISTINCT (market_slug, intent))           AS slug_intent_pairs,
       count(DISTINCT (market_slug, side_norm))        AS slug_side_pairs,
       count(*) - count(DISTINCT (market_slug, intent)) AS duplicate_slug_intent_rows
  FROM us_premap;
