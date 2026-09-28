-- EVERY sports_type THE VENUE USES, AND EVERY LEAGUE TOKEN, IN FULL.
--
-- WHY: `bettor_venue_realism.classify` returned REAL_FIXTURE for a row with no
-- sports_type at all, for an unrecognized sports_type, and for a row carrying
-- nothing but a title. It treated the ABSENCE of a simulation marker as
-- affirmative evidence of a real fixture -- the exact "absence of evidence is
-- not evidence" error the module's own docstring claims to avoid.
--
-- The repair is to require an affirmative, recognized classification, which
-- needs an ALLOWLIST. An allowlist assembled from memory would break the one
-- sport this lane has ever traded if `baseball_*` were missing from it, so it is
-- assembled from the venue's own vocabulary here, in full, with no LIMIT that
-- could hide a family.
--
-- Statement 3 is the check that matters for the allowlist's safety: whether
-- every OPEN market the lane's own label filter selects has a sports_type this
-- allowlist would recognise. A prefix I omit becomes a refusal in production.

\echo == 1 · EVERY sports_type, WITH ITS PREFIX, NO LIMIT ==
SELECT split_part(sports_type, '_', 1)      AS first_token,
       count(DISTINCT sports_type)           AS distinct_types,
       count(*)                              AS premap_rows,
       count(DISTINCT event_slug)            AS events,
       min(sports_type)                      AS example_type
  FROM us_premap
 GROUP BY 1
 ORDER BY 3 DESC;

\echo == 2 · THE SAME, TWO TOKENS DEEP, SO table_tennis IS NOT READ AS tennis ==
SELECT coalesce(sports_type, 'NULL')         AS sports_type,
       count(*)                              AS premap_rows,
       count(DISTINCT event_slug)            AS events
  FROM us_premap
 GROUP BY 1
 ORDER BY 2 DESC;

\echo == 3 · WOULD AN ALLOWLIST REFUSE ANY OPEN MARKET THE LANE SELECTS ==
SELECT m.sport                                       AS lane_label,
       count(*)                                      AS open_markets,
       count(*) FILTER (WHERE p.market_slug IS NULL)  AS no_premap_row,
       count(*) FILTER (WHERE p.sports_type IS NULL)  AS premap_row_no_type,
       count(DISTINCT p.sports_type)                  AS distinct_types,
       min(p.sports_type)                             AS example_type
  FROM markets m
  LEFT JOIN us_premap p ON p.market_slug = m.slug
 WHERE coalesce(m.closed, false) = false
   AND coalesce(m.resolved, false) = false
   AND m.sport IN ('Soccer', 'MLB')
 GROUP BY 1;

\echo == 4 · AND WHAT engnl ACTUALLY IS -- League Two or the National League ==
SELECT split_part(market_slug, '-', 2)        AS token,
       coalesce(team_league, 'null')          AS venue_league_label,
       count(DISTINCT event_slug)             AS events,
       string_agg(DISTINCT left(event_title, 44), ' / ')
         AS fixtures
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) IN ('engnl', 'mls', 'unl', 'uwcl',
                                           'cnl', 'uslc', 'arg2', 'brb',
                                           'lco', 'uru1', 'irl1', 'nwsl',
                                           'lmx')
 GROUP BY 1, 2
 ORDER BY 3 DESC;
