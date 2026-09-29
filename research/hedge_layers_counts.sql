-- THE LAYER COUNTS, COMPACT ENOUGH TO READ IN FULL.
--
-- Run 266 answered the question that mattered most and refuted my claim in the
-- process. Its statement 4 found FUTURE fixtures already inside the parser
-- carrying many supported instruments each:
--
--     nfl-pit-cle-2026-10-01        73 supported instruments, 3 types
--     cfb-wkent-nmxst-2026-10-01    48 supported instruments, 3 types
--     18 UNL soccer fixtures        11 supported instruments each, 3 types
--
-- So "an indirect hedge is structurally unavailable on 96% of fixtures" was
-- wrong, and wrong in the way Codex said: the 2,440 count was dominated by
-- fixtures outside the trading window and by my own narrow filter, not by the
-- venue lacking instruments.
--
-- Run 266 also failed on statement 6: `bettor_funded_models` DOES NOT EXIST in
-- production. That is not an error to route around, it is the fact -- migration
-- 135 is not deployed, consistent with production carrying 125-128 only. Every
-- table read here is guarded with to_regclass so an absent table reports as an
-- absent table instead of aborting the file.

\echo == 0 THE CATALOGUE WINDOW ==
SELECT min(game_start) AS earliest, max(game_start) AS latest, now() AS read_at,
       count(DISTINCT event_slug)                                  AS events_all,
       count(DISTINCT event_slug) FILTER (WHERE game_start > now()) AS future,
       count(DISTINCT event_slug) FILTER (WHERE game_start <= now()) AS past,
       count(DISTINCT event_slug) FILTER (WHERE game_start IS NULL) AS undated
  FROM us_premap;

\echo == 1 THE FIVE LAYERS ON FUTURE FIXTURES ==
WITH fut AS (
  SELECT event_slug, market_slug,
         (sports_type LIKE '%\_team\_full\_game\_winner'
       OR sports_type LIKE '%\_team\_full\_time\_winner'
       OR sports_type LIKE '%\_match\_winner'
       OR sports_type LIKE '%\_fight\_winner'
       OR sports_type LIKE '%\_team\_full\_game\_spread'
       OR sports_type LIKE '%\_team\_first\_half\_spread'
       OR sports_type LIKE '%\_game\_total\_points'
       OR sports_type LIKE '%\_game\_first\_half\_total\_points') AS supported
    FROM us_premap
   WHERE event_slug IS NOT NULL AND game_start > now()),
per AS (
  SELECT event_slug, count(DISTINCT market_slug) AS slugs,
         count(DISTINCT market_slug) FILTER (WHERE supported) AS sup
    FROM fut GROUP BY event_slug)
SELECT CASE WHEN slugs = 1 THEN 'L0_no_second_venue_instrument'
            WHEN sup < 2   THEN 'L1_exists_but_our_parser_does_not_support_it'
            ELSE 'L2plus_two_or_more_supported_instruments' END  AS layer,
       count(*) AS fixtures, sum(slugs) AS venue_instruments,
       sum(sup) AS supported_instruments
  FROM per GROUP BY 1 ORDER BY 1;

\echo == 2 THE SAME LAYERS ON THE WHOLE TABLE, FOR COMPARISON ==
-- The number I previously reported came from here. Shown beside the windowed
-- one so the difference between the two is visible rather than argued about.
WITH allr AS (
  SELECT event_slug, market_slug,
         (sports_type LIKE '%\_team\_full\_game\_winner'
       OR sports_type LIKE '%\_team\_full\_time\_winner'
       OR sports_type LIKE '%\_match\_winner'
       OR sports_type LIKE '%\_fight\_winner'
       OR sports_type LIKE '%\_team\_full\_game\_spread'
       OR sports_type LIKE '%\_team\_first\_half\_spread'
       OR sports_type LIKE '%\_game\_total\_points'
       OR sports_type LIKE '%\_game\_first\_half\_total\_points') AS supported
    FROM us_premap WHERE event_slug IS NOT NULL),
per AS (
  SELECT event_slug, count(DISTINCT market_slug) AS slugs,
         count(DISTINCT market_slug) FILTER (WHERE supported) AS sup
    FROM allr GROUP BY event_slug)
SELECT CASE WHEN slugs = 1 THEN 'L0_no_second_venue_instrument'
            WHEN sup < 2   THEN 'L1_exists_but_our_parser_does_not_support_it'
            ELSE 'L2plus_two_or_more_supported_instruments' END  AS layer,
       count(*) AS fixtures
  FROM per GROUP BY 1 ORDER BY 1;

\echo == 3 THE L1 GAP: UNSUPPORTED TYPES BY FUTURE FIXTURE COUNT ==
SELECT sports_type, count(DISTINCT event_slug) AS future_fixtures,
       count(DISTINCT market_slug) AS instruments
  FROM us_premap
 WHERE event_slug IS NOT NULL AND game_start > now() AND sports_type IS NOT NULL
   AND sports_type NOT LIKE '%\_team\_full\_game\_winner'
   AND sports_type NOT LIKE '%\_team\_full\_time\_winner'
   AND sports_type NOT LIKE '%\_match\_winner'
   AND sports_type NOT LIKE '%\_fight\_winner'
   AND sports_type NOT LIKE '%\_team\_full\_game\_spread'
   AND sports_type NOT LIKE '%\_team\_first\_half\_spread'
   AND sports_type NOT LIKE '%\_game\_total\_points'
   AND sports_type NOT LIKE '%\_game\_first\_half\_total\_points'
 GROUP BY sports_type ORDER BY future_fixtures DESC LIMIT 12;

\echo == 4 WHICH MIGRATION 131-135 TABLES EXIST IN PRODUCTION AT ALL ==
-- Guarded, so an absent table reports as absent. This is the deployed-schema
-- boundary, and it is why the model registry could not be read.
SELECT t AS table_name, (to_regclass(t) IS NOT NULL) AS exists_in_production
  FROM unnest(ARRAY['bettor_funded_models','bettor_funded_decisions',
                    'bettor_funded_decision_outcomes',
                    'bettor_funded_operation_evidence',
                    'bettor_funded_leg_reservations',
                    'bettor_funded_intents','us_premap',
                    'external_valuations']) AS t
 ORDER BY 1;
