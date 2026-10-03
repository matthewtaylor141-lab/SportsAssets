-- cand22 NCAAF funnel BY STAGE for the America/New_York day of the read
-- (read-only). League identity: venue token `cfb` <-> provider key
-- `americanfootball_ncaaf` <-> "NCAAF" (ext_pinnacle_loop.
-- provider_key_for_venue_token, shared by the coverage census and
-- coverage_integrity). Window: [ET midnight, next ET midnight) of now().
\echo '== F0 · the window =='
SELECT now() AS read_at,
       (date_trunc('day', now() AT TIME ZONE 'America/New_York')
          AT TIME ZONE 'America/New_York') AS et_day_start_utc,
       ((date_trunc('day', now() AT TIME ZONE 'America/New_York') + interval '1 day')
          AT TIME ZONE 'America/New_York') AS et_day_end_utc,
       (SELECT value->'writer'->>'build' FROM ingestion_state
         WHERE key = 'ext_pinnacle_last_cycle') AS writer_build;

\echo '== F1 · the funnel, one row per stage (events unless the unit says otherwise) =='
WITH w AS (
  SELECT (date_trunc('day', now() AT TIME ZONE 'America/New_York')
            AT TIME ZONE 'America/New_York') AS t0,
         ((date_trunc('day', now() AT TIME ZONE 'America/New_York') + interval '1 day')
            AT TIME ZONE 'America/New_York') AS t1),
lc AS (SELECT value FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle'),
ev AS (SELECT o.* FROM ext_candidate_outcomes o, w
        WHERE o.sport_key = 'americanfootball_ncaaf'
          AND o.cycle_at >= w.t0 AND o.cycle_at < w.t1),
va AS (SELECT v.* FROM external_valuations v, w
        WHERE v.sport_family = 'football'
          AND split_part(v.us_market_slug, '-', 2) = 'cfb'
          AND v.decided_at >= w.t0 AND v.decided_at < w.t1),
pd AS (SELECT d.* FROM paper_decisions d, w
        WHERE split_part(d.us_market_slug, '-', 2) = 'cfb'
          AND d.decided_at >= w.t0 AND d.decided_at < w.t1)
SELECT * FROM (VALUES
 (1, 'venue listed: cfb full-game-winner events (game_start in ET day)',
  (SELECT count(DISTINCT event_slug) FROM us_premap, w
    WHERE split_part(market_slug, '-', 2) = 'cfb'
      AND sports_type = 'football_team_full_game_winner'
      AND game_start >= w.t0 AND game_start < w.t1)),
 (2, 'collector requested americanfootball_ncaaf on its last cycle (1 = yes)',
  (SELECT CASE WHEN (value->'sports_selection'->'requested') ? 'americanfootball_ncaaf'
               THEN 1 ELSE 0 END FROM lc)),
 (3, 'provider events (distinct, ledger rows today)',
  (SELECT count(DISTINCT provider_event_id) FROM ev)),
 (4, 'provider events past the Pinnacle-price check',
  (SELECT count(DISTINCT provider_event_id) FROM ev
    WHERE coalesce(first_refusal, '') <> 'NO_PINNACLE_ON_EVENT')),
 (5, 'fixture-confirmed (not refused by the competition mapping check)',
  (SELECT count(DISTINCT provider_event_id) FROM ev
    WHERE coalesce(first_refusal, '') NOT IN (
      'THE_PROVIDER_COMPETITION_FIXTURES_DO_NOT_MATCH_THE_VENUE_COMPETITION',
      'THE_MAPPING_COULD_NOT_BE_CONFIRMED_AGAINST_ANY_FIXTURE',
      'NO_PINNACLE_ON_EVENT'))),
 (6, 'mapped to a venue contract (identity resolved)',
  (SELECT count(DISTINCT provider_event_id) FROM ev WHERE us_market_slug IS NOT NULL)),
 (7, 'valued (football valuations, distinct contracts)',
  (SELECT count(DISTINCT us_market_slug) FROM va)),
 (8, 'valued with the book price set recorded (raw_odds present)',
  (SELECT count(DISTINCT us_market_slug) FROM va
    WHERE raw_odds IS NOT NULL AND raw_odds <> '{}'::jsonb)),
 (9, 'settlement: overtime established (no OVERTIME_* refusal), contracts',
  (SELECT count(DISTINCT us_market_slug) FROM va
    WHERE NOT ('OVERTIME_RULE_NOT_ESTABLISHED' = ANY(refusals))
      AND NOT ('OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE' = ANY(refusals)))),
 (10, 'settlement fully supported (no VOID_* / OVERTIME_* refusal), contracts',
  (SELECT count(DISTINCT us_market_slug) FROM va
    WHERE NOT EXISTS (SELECT 1 FROM unnest(refusals) r
                       WHERE r LIKE 'VOID_%' OR r LIKE 'OVERTIME_%'))),
 (11, 'Derek evaluated (DEREK_ENTRY_POLICY_V2 decisions, contracts)',
  (SELECT count(DISTINCT us_market_slug) FROM pd WHERE strategy = 'DEREK_ENTRY_POLICY_V2')),
 (12, 'ENTER decisions (any strategy, rows)',
  (SELECT count(*) FROM pd WHERE verdict = 'ENTER')),
 (13, 'REFUSE decisions (any strategy, rows)',
  (SELECT count(*) FROM pd WHERE verdict = 'REFUSE')),
 (14, 'paper orders (rows)',
  (SELECT count(*) FROM paper_orders o, w WHERE split_part(o.us_market_slug, '-', 2) = 'cfb'
      AND o.created_at >= w.t0 AND o.created_at < w.t1)),
 (15, 'paper fills (rows)',
  (SELECT count(*) FROM paper_fills f, w WHERE split_part(f.us_market_slug, '-', 2) = 'cfb'
      AND f.filled_at >= w.t0 AND f.filled_at < w.t1)),
 (16, 'execution intents (rows)',
  (SELECT count(*) FROM execution_intents i, w WHERE split_part(i.us_market_slug, '-', 2) = 'cfb'
      AND i.created_at >= w.t0 AND i.created_at < w.t1))
) AS f(stage, what, n) ORDER BY stage;

\echo '== F2 · where NCAAF provider events stopped today (first refusal) =='
WITH w AS (
  SELECT (date_trunc('day', now() AT TIME ZONE 'America/New_York')
            AT TIME ZONE 'America/New_York') AS t0)
SELECT stage, outcome, first_refusal, count(DISTINCT provider_event_id) AS events,
       (array_agg(DISTINCT home || ' v ' || away))[1:4] AS examples
  FROM ext_candidate_outcomes, w
 WHERE sport_key = 'americanfootball_ncaaf' AND cycle_at >= w.t0
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;

\echo '== F3 · Derek / paper refusals on cfb today =='
WITH w AS (
  SELECT (date_trunc('day', now() AT TIME ZONE 'America/New_York')
            AT TIME ZONE 'America/New_York') AS t0)
SELECT strategy, verdict, refusal, count(*) AS n,
       (array_agg(DISTINCT us_market_slug))[1:3] AS examples
  FROM paper_decisions, w
 WHERE split_part(us_market_slug, '-', 2) = 'cfb' AND decided_at >= w.t0
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;

\echo '== F4 · coverage_integrity (migration 209) and the coverage census, same league identity =='
SELECT tz, day, league, provider_events, normalized_events, venue_discovered,
       mapped_events, settlement_supported, evaluated_events, decided_events,
       entered_events, refused_events, ordered_events, filled_events,
       venue_catalogue_events, computed_at
  FROM coverage_funnel_snapshots
 WHERE league = 'americanfootball_ncaaf'
 ORDER BY day DESC, computed_at DESC LIMIT 3;
SELECT at, categories->'by_league'->'NCAAF' AS ncaaf_census,
       categories->'mandate'->'requested_keys' AS mandate_requested
  FROM derek_coverage_census ORDER BY at DESC LIMIT 1;
