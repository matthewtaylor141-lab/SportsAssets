-- WHERE team_abbr IS EQUAL ON BOTH ROWS, IS THE MARKET ONE WE GRADE?
--
-- Run 269 measured 5,930 of 16,540 future slugs carrying the SAME team_abbr on
-- both rows -- e.g. `aachc-nhl-fewestpts-2027-04-10-ana`, yes/no both `ana`.
--
-- `orientation_of` reads `team_abbr` alone, so on those rows it returns the
-- SAME orientation for both sides. Two legs then claim to back the same
-- participant while holding opposite outcome tokens, and one of them has its
-- payout function inverted. Exercised against a fixture of that shape, that is
-- exactly what happened: both sides came back backs='A'.
--
-- BUT the example run 269 printed carries sports_type='futures', which
-- `derive_kind` refuses before orientation is ever consulted. So the defect may
-- be confined to rows already refused, or it may reach graded markets. That
-- decides whether `orientation_of` needs the side at all, and it is a
-- measurement rather than a judgement.
--
-- The graded suffixes the parser supports are the _winner / _spread / _total
-- families; statement 1 crosses the collapsing set against sports_type so the
-- answer is read off rather than argued.
--
-- Read-only. Only \echo.

\echo == 0 THE COLLAPSING FUTURE SLUGS, BY SLUG FAMILY PREFIX ==
WITH collapsing AS (
  SELECT market_slug, left(market_slug, 4) AS fam
    FROM us_premap
   WHERE game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug
  HAVING count(*) = 2
     AND count(DISTINCT coalesce(nullif(team_abbr, ''), side_norm)) = 1
     AND count(*) FILTER (WHERE team_abbr IS NOT NULL
                            AND team_abbr <> '') = 2)
SELECT fam, count(*) AS collapsing_slugs
  FROM collapsing
 GROUP BY fam
 ORDER BY 2 DESC
 LIMIT 25;

\echo == 1 THE COLLAPSING SET BY sports_type -- DO WE GRADE ANY OF THESE ==
WITH collapsing AS (
  SELECT market_slug
    FROM us_premap
   WHERE game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug
  HAVING count(*) = 2
     AND count(DISTINCT coalesce(nullif(team_abbr, ''), side_norm)) = 1
     AND count(*) FILTER (WHERE team_abbr IS NOT NULL
                            AND team_abbr <> '') = 2)
SELECT u.sports_type,
       count(DISTINCT u.market_slug) AS collapsing_slugs,
       bool_or(u.sports_type LIKE '%\_winner'
            OR u.sports_type LIKE '%\_spread'
            OR u.sports_type LIKE '%\_total%') AS looks_like_a_graded_family
  FROM collapsing c JOIN us_premap u USING (market_slug)
 GROUP BY u.sports_type
 ORDER BY 2 DESC
 LIMIT 30;

\echo == 2 THE SAME QUESTION AS ONE NUMBER ==
WITH collapsing AS (
  SELECT market_slug
    FROM us_premap
   WHERE game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug
  HAVING count(*) = 2
     AND count(DISTINCT coalesce(nullif(team_abbr, ''), side_norm)) = 1
     AND count(*) FILTER (WHERE team_abbr IS NOT NULL
                            AND team_abbr <> '') = 2)
SELECT count(DISTINCT c.market_slug)                       AS collapsing_slugs,
       count(DISTINCT c.market_slug) FILTER (
         WHERE u.sports_type LIKE '%\_winner'
            OR u.sports_type LIKE '%\_spread'
            OR u.sports_type LIKE '%\_total%')             AS in_a_graded_family,
       count(DISTINCT c.market_slug) FILTER (
         WHERE u.market_slug LIKE 'aec-%'
            OR u.market_slug LIKE 'asc-%'
            OR u.market_slug LIKE 'tsc-%')                 AS in_a_graded_prefix
  FROM collapsing c JOIN us_premap u USING (market_slug);

\echo == 3 ON GRADED PREFIXES, HOW DO THE TWO ROWS DIFFER ==
-- If team_abbr always differs on aec-/asc-/tsc-, then team_abbr alone IS a
-- sufficient orientation there and the collapse never reaches a graded leg.
WITH g AS (
  SELECT market_slug,
         count(DISTINCT coalesce(nullif(team_abbr, ''), '')) AS d_abbr,
         count(*) FILTER (WHERE team_abbr IS NULL
                            OR team_abbr = '')              AS abbr_absent,
         count(DISTINCT side_norm)                           AS d_side,
         count(DISTINCT coalesce(signed, ''))                AS d_signed
    FROM us_premap
   WHERE (market_slug LIKE 'aec-%' OR market_slug LIKE 'asc-%'
       OR market_slug LIKE 'tsc-%')
     AND game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug)
SELECT count(*)                                      AS graded_prefix_slugs,
       count(*) FILTER (WHERE abbr_absent = 2)        AS abbr_absent_on_both,
       count(*) FILTER (WHERE abbr_absent = 0
                          AND d_abbr = 2)             AS abbr_differs,
       count(*) FILTER (WHERE abbr_absent = 0
                          AND d_abbr = 1)             AS abbr_EQUAL_the_risky_case,
       count(*) FILTER (WHERE d_side = 2)             AS side_norm_differs,
       count(*) FILTER (WHERE d_signed = 2)           AS signed_differs
  FROM g;

\echo == 4 IF THE RISKY CASE EXISTS ON A GRADED PREFIX, PRINT IT ==
WITH g AS (
  SELECT market_slug
    FROM us_premap
   WHERE (market_slug LIKE 'aec-%' OR market_slug LIKE 'asc-%'
       OR market_slug LIKE 'tsc-%')
     AND game_start IS NOT NULL AND game_start > now()
   GROUP BY market_slug
  HAVING count(*) FILTER (WHERE team_abbr IS NOT NULL
                            AND team_abbr <> '') = 2
     AND count(DISTINCT coalesce(nullif(team_abbr, ''), '')) = 1
   LIMIT 5)
SELECT u.market_slug, u.side_norm, u.team_abbr, u.intent, u.signed, u.line,
       u.sports_type
  FROM g JOIN us_premap u USING (market_slug)
 ORDER BY u.market_slug, u.intent;
