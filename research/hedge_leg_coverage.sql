-- HOW MUCH OF THE CATALOGUE CAN A LEG ACTUALLY BE BUILT FROM.
--
-- Run 264 settled the two remaining derivations and closed one guess:
--
--   * `line` on an aec- row is the GAME START MINUTE. Every value is a
--     two-digit 00..59 and the baseball rows sitting on '00' start at 9:00 AM
--     UTC. It is not a handicap in any row, so "a line is stated" carries no
--     information about the market kind at all.
--   * EVERY slug prefix has exactly 2.00 rows per market_slug and exactly 2
--     distinct intents -- asta, tsc-, asc-, atc-, tec-, aec- and eleven more,
--     without exception. One instrument per market, two sides carried by the
--     intent, venue-wide. So the opposite side of a held contract is netting
--     on the same instrument and can never be a second settling holding.
--     That is now measured across 60,540 rows rather than asserted.
--   * event_slug lists the participant codes in the fixture's order:
--     npb-clm-nhf-2026-10-01 against the title "Chiba Lotte Marines vs.
--     Nippon Ham Fighters". team_abbr on each row names which of the two it
--     backs, so orientation is an equality rather than a reading.
--
-- WHAT REMAINS IS COVERAGE, AND IT MUST BE MEASURED, NOT CLAIMED. The Leg
-- vocabulary grades exactly three variables -- signed MARGIN, combined TOTAL,
-- three-way WIN3 -- over six periods. 215 sports_types exist and most are
-- neither: a player's receiving yards and a game's total corners are real
-- markets graded against variables this module does not represent, and
-- mapping them onto VAR_TOTAL would give two legs a shared grading key for
-- different underlying counts. So the allowlist is narrow BY DESIGN and this
-- run measures what it admits and what it leaves out.

\echo == 0 WHAT THE NARROW ALLOWLIST WOULD ADMIT, AND WHAT IT WOULD REFUSE ==
SELECT CASE
         WHEN sports_type LIKE '%\_team\_full\_game\_winner'  THEN 'moneyline_full_game'
         WHEN sports_type LIKE '%\_match\_winner'             THEN 'moneyline_match'
         WHEN sports_type LIKE '%\_team\_full\_game\_spread'  THEN 'spread_full_game'
         WHEN sports_type LIKE '%\_team\_first\_half\_spread' THEN 'spread_first_half'
         WHEN sports_type LIKE '%\_game\_total\_points'       THEN 'total_points_full_game'
         WHEN sports_type LIKE '%\_game\_first\_half\_total\_points'
                                                             THEN 'total_points_first_half'
         WHEN sports_type IS NULL                            THEN 'no_sports_type'
         ELSE 'NOT_A_VARIABLE_THIS_MODULE_GRADES' END        AS admitted_as,
       count(*)                          AS rows,
       count(DISTINCT event_slug)        AS events,
       count(DISTINCT sports_type)       AS types
  FROM us_premap
 GROUP BY 1
 ORDER BY rows DESC;

\echo == 1 THE ADMITTED TYPES THEMSELVES, SO THE LIST IS COMPLETE NOT PLAUSIBLE ==
SELECT sports_type, count(*) AS rows, count(DISTINCT event_slug) AS events,
       min(left(market_slug, 4)) AS a_prefix
  FROM us_premap
 WHERE sports_type LIKE '%\_team\_full\_game\_winner'
    OR sports_type LIKE '%\_match\_winner'
    OR sports_type LIKE '%\_team\_full\_game\_spread'
    OR sports_type LIKE '%\_team\_first\_half\_spread'
    OR sports_type LIKE '%\_game\_total\_points'
    OR sports_type LIKE '%\_game\_first\_half\_total\_points'
 GROUP BY sports_type
 ORDER BY rows DESC;

\echo == 2 THE BIGGEST TYPES THE ALLOWLIST LEAVES OUT, WITH THE REASON VISIBLE ==
-- Named so that the exclusions read as a decision rather than an oversight.
-- A player prop and a corners total are graded against variables the module
-- has no representation for; a hockey first period has no member of the
-- period vocabulary.
SELECT sports_type, count(*) AS rows, count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE sports_type IS NOT NULL
   AND sports_type NOT LIKE '%\_team\_full\_game\_winner'
   AND sports_type NOT LIKE '%\_match\_winner'
   AND sports_type NOT LIKE '%\_team\_full\_game\_spread'
   AND sports_type NOT LIKE '%\_team\_first\_half\_spread'
   AND sports_type NOT LIKE '%\_game\_total\_points'
   AND sports_type NOT LIKE '%\_game\_first\_half\_total\_points'
 GROUP BY sports_type
 ORDER BY rows DESC
 LIMIT 25;

\echo == 3 CAN A HEDGE EVEN BE BUILT: ADMITTED CONTRACTS PER EVENT ==
-- A hedge needs a SECOND admitted contract on the same fixture that is not
-- the held instrument. Both sides of one market share a slug, so what
-- matters is DISTINCT admitted market_slugs per event.
SELECT n_slugs AS admitted_slugs_on_event, count(*) AS events
  FROM (SELECT event_slug, count(DISTINCT market_slug) AS n_slugs
          FROM us_premap
         WHERE event_slug IS NOT NULL
           AND (sports_type LIKE '%\_team\_full\_game\_winner'
             OR sports_type LIKE '%\_match\_winner'
             OR sports_type LIKE '%\_team\_full\_game\_spread'
             OR sports_type LIKE '%\_team\_first\_half\_spread'
             OR sports_type LIKE '%\_game\_total\_points'
             OR sports_type LIKE '%\_game\_first\_half\_total\_points')
         GROUP BY event_slug) t
 GROUP BY 1
 ORDER BY 1
 LIMIT 20;

\echo == 4 AN EVENT WITH SEVERAL ADMITTED CONTRACTS, WHICH IS A CANDIDATE SET ==
SELECT left(p.event_slug, 30) AS event_slug, p.sports_type, p.side_norm,
       p.team_abbr, p.signed, left(p.market_slug, 44) AS market_slug
  FROM us_premap p
 WHERE p.event_slug = (
        SELECT event_slug FROM us_premap
         WHERE event_slug IS NOT NULL
           AND (sports_type LIKE '%\_team\_full\_game\_winner'
             OR sports_type LIKE '%\_team\_full\_game\_spread'
             OR sports_type LIKE '%\_game\_total\_points')
         GROUP BY event_slug
        HAVING count(DISTINCT sports_type) >= 2
         ORDER BY count(DISTINCT market_slug) LIMIT 1)
 ORDER BY p.sports_type, p.market_slug, p.side_norm
 LIMIT 24;

\echo == 5 EVENT SLUG SHAPE: DOES IT ALWAYS YIELD TWO PARTICIPANT CODES ==
-- Orientation depends on it. league + two codes + date is three tokens once
-- the date is dropped; anything else must make the leg refuse, and this says
-- how often that would happen.
SELECT tokens_after_date, count(*) AS events
  FROM (SELECT event_slug,
               cardinality(
                 array_remove(
                   (SELECT array_agg(t) FROM unnest(
                       string_to_array(event_slug, '-')) AS t
                     WHERE t !~ '^[0-9]{1,4}$'), NULL)) AS tokens_after_date
          FROM us_premap
         WHERE event_slug IS NOT NULL
         GROUP BY event_slug) s
 GROUP BY 1
 ORDER BY 1;
