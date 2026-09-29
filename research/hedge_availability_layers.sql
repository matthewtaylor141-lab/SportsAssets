-- SEPARATING FIVE DIFFERENT THINGS I PREVIOUSLY REPORTED AS ONE.
--
-- THE CLAIM BEING WITHDRAWN. I reported "2,440 of 2,531 fixtures carry exactly
-- one admitted contract, so an indirect hedge is structurally unavailable on
-- 96% of them". That is a measurement of OUR PARSER'S SUPPORTED-TYPE FILTER,
-- not of the venue. The venue can carry a perfectly good second instrument on
-- a fixture that my six suffix patterns do not recognise, and run 265 itself
-- showed exactly that: `soccer_team_full_time_winner`, 1,338 rows over 223
-- events, excluded for no reason but a spelling my list did not have.
--
-- So catalogue presence, parser support and trading admission are three
-- different questions and this asks them separately:
--
--   L0  the fixture carries no second venue instrument at all
--   L1  second instruments exist, our parser does not support their type
--   L2  supported, but the settlement rule was never captured
--   L3  compatible, but no price / no depth / no qualified probability
--   L4  fully evaluable
--
-- AND THE WINDOW IS STATED, because "2,440 fixtures" is meaningless without
-- it: `us_premap` is a rolling catalogue with no delete, so it holds finished
-- fixtures alongside live ones and a count over the whole table mixes them.

\echo == 0 THE CATALOGUE WINDOW, WHICH EVERY LATER COUNT DEPENDS ON ==
-- What period does this table actually span, and how much of it is in the past?
SELECT min(game_start)                                          AS earliest,
       max(game_start)                                          AS latest,
       now()                                                    AS read_at,
       count(DISTINCT event_slug)                               AS events_all,
       count(DISTINCT event_slug) FILTER (
         WHERE game_start > now())                              AS events_future,
       count(DISTINCT event_slug) FILTER (
         WHERE game_start <= now())                             AS events_past,
       count(DISTINCT event_slug) FILTER (
         WHERE game_start IS NULL)                              AS events_undated,
       max(updated_at)                                          AS freshest_row
  FROM us_premap;

\echo == 1 L0..L1: DOES A SECOND VENUE INSTRUMENT EXIST, PARSER ASIDE ==
-- Counted on FUTURE fixtures only, because a hedge on a finished game is not
-- a trading opportunity. `market_slug` is the instrument (both sides share
-- one), so a second instrument means a second distinct slug.
SELECT CASE WHEN slugs = 1 THEN 'L0_one_instrument_only'
            ELSE 'has_a_second_instrument' END      AS venue_presence,
       count(*)                                     AS fixtures,
       sum(slugs)                                   AS instruments
  FROM (SELECT event_slug, count(DISTINCT market_slug) AS slugs
          FROM us_premap
         WHERE event_slug IS NOT NULL AND game_start > now()
         GROUP BY event_slug) t
 GROUP BY 1
 ORDER BY 1;

\echo == 2 THE LAYERS, PER FUTURE FIXTURE ==
-- L1 is the one I previously mislabelled as L0. It is OUR gap, not the
-- venue's, and separating them is the whole point of this run.
WITH fut AS (
  SELECT * FROM us_premap
   WHERE event_slug IS NOT NULL AND game_start > now()),
flagged AS (
  SELECT event_slug, market_slug,
         (sports_type LIKE '%\_team\_full\_game\_winner'
       OR sports_type LIKE '%\_team\_full\_time\_winner'
       OR sports_type LIKE '%\_match\_winner'
       OR sports_type LIKE '%\_fight\_winner'
       OR sports_type LIKE '%\_team\_full\_game\_spread'
       OR sports_type LIKE '%\_team\_first\_half\_spread'
       OR sports_type LIKE '%\_game\_total\_points'
       OR sports_type LIKE '%\_game\_first\_half\_total\_points') AS supported
    FROM fut),
per AS (
  SELECT event_slug,
         count(DISTINCT market_slug)                                AS slugs,
         count(DISTINCT market_slug) FILTER (WHERE supported)       AS supported_slugs
    FROM flagged GROUP BY event_slug)
SELECT CASE
         WHEN slugs = 1                    THEN 'L0_no_second_venue_instrument'
         WHEN supported_slugs < 2          THEN 'L1_exists_but_parser_unsupported'
         ELSE 'L2plus_two_or_more_supported' END      AS layer,
       count(*)                                       AS fixtures,
       sum(slugs)                                     AS venue_instruments,
       sum(supported_slugs)                           AS supported_instruments
  FROM per
 GROUP BY 1
 ORDER BY 1;

\echo == 3 THE L1 GAP ITSELF: WHICH UNSUPPORTED TYPES WOULD OPEN MOST FIXTURES ==
-- This is the actionable list. A type here is a parser gap, and each row says
-- how many FUTURE fixtures it appears on -- so the next suffix to support is a
-- measurement rather than a preference.
SELECT sports_type,
       count(DISTINCT event_slug)              AS future_fixtures,
       count(DISTINCT market_slug)             AS instruments,
       count(*)                                AS rows
  FROM us_premap
 WHERE event_slug IS NOT NULL AND game_start > now()
   AND sports_type IS NOT NULL
   AND sports_type NOT LIKE '%\_team\_full\_game\_winner'
   AND sports_type NOT LIKE '%\_team\_full\_time\_winner'
   AND sports_type NOT LIKE '%\_match\_winner'
   AND sports_type NOT LIKE '%\_fight\_winner'
   AND sports_type NOT LIKE '%\_team\_full\_game\_spread'
   AND sports_type NOT LIKE '%\_team\_first\_half\_spread'
   AND sports_type NOT LIKE '%\_game\_total\_points'
   AND sports_type NOT LIKE '%\_game\_first\_half\_total\_points'
 GROUP BY sports_type
 ORDER BY future_fixtures DESC
 LIMIT 30;

\echo == 4 THE PRIORITY SET: FUTURE FIXTURES WITH 2+ SUPPORTED INSTRUMENTS ==
-- ONE COMPLETE SUPPORTED PATH is what is wanted, so these are the candidates
-- for it. Real, dated, multi-instrument and already inside the parser.
WITH fut AS (
  SELECT * FROM us_premap
   WHERE event_slug IS NOT NULL AND game_start > now()
     AND (sports_type LIKE '%\_team\_full\_game\_winner'
       OR sports_type LIKE '%\_team\_full\_time\_winner'
       OR sports_type LIKE '%\_match\_winner'
       OR sports_type LIKE '%\_fight\_winner'
       OR sports_type LIKE '%\_team\_full\_game\_spread'
       OR sports_type LIKE '%\_team\_first\_half\_spread'
       OR sports_type LIKE '%\_game\_total\_points'
       OR sports_type LIKE '%\_game\_first\_half\_total\_points'))
SELECT left(event_slug, 30)                       AS event_slug,
       count(DISTINCT market_slug)                AS supported_instruments,
       count(DISTINCT sports_type)                AS distinct_types,
       string_agg(DISTINCT sports_type, ' | ')     AS types,
       min(game_start)                            AS starts
  FROM fut
 GROUP BY event_slug
HAVING count(DISTINCT market_slug) >= 2
 ORDER BY count(DISTINCT sports_type) DESC, min(game_start)
 LIMIT 20;

\echo == 5 L2: IS ANY VENUE SETTLEMENT PROSE CAPTURED AT ALL, PER CONTRACT ==
-- The overtime rule is read per contract from the venue's own prose. Nothing
-- in this database stores it, which is itself the answer: L2 is currently
-- gated on a per-contract read that happens at cycle time and is not persisted.
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema='public'
   AND (table_name LIKE '%settlement%' OR table_name LIKE '%rules%'
     OR table_name LIKE '%terms%')
 ORDER BY 1;

\echo == 6 L3: THE MODEL REGISTRY, WHICH GATES QUALIFIED PROBABILITIES ==
SELECT model_key, state, count(*) AS rows,
       max(model_version) AS latest_version
  FROM bettor_funded_models
 GROUP BY model_key, state
 ORDER BY 1, 2;

\echo == 7 L3: PREDICTION AND OUTCOME LEDGERS, FOR THE PROSPECTIVE PATH ==
SELECT 'bettor_funded_decisions' AS ledger, count(*) AS rows FROM bettor_funded_decisions
UNION ALL SELECT 'bettor_funded_decision_outcomes', count(*) FROM bettor_funded_decision_outcomes
UNION ALL SELECT 'bettor_funded_intents', count(*) FROM bettor_funded_intents
UNION ALL SELECT 'external_valuations', count(*) FROM external_valuations;
