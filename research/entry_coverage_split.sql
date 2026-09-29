-- READ-ONLY. THE ENTRY LANE'S TWO LARGEST REFUSALS, SEPARATED AND MEASURED.
--
-- NO_VENUE_CONTRACT_FOR_EVENT is raised by `bettor_venue_mapping.map_event`,
-- which matches the odds provider's team names against the GLOBAL catalogue
-- (`markets`, written from Gamma) -- not against the US venue's own
-- catalogue (`us_premap`). So the question for each such event is whether
-- the VENUE lists the fixture at all (on the same date, with a full-game
-- winner), or only the global catalogue lacks it.
--
-- NO_PINNACLE_ON_EVENT means the provider's response for that event had no
-- Pinnacle h2h market. No alias or catalogue can supply a missing price; the
-- question is when (relative to the start) and in which competitions it is
-- missing.
--
-- No balance, cash, credential or account row is selected.

\echo '== N0 · last 24 h of per-event outcomes: distinct events by first refusal and sport =='
SELECT sport_key, coalesce(first_refusal, outcome) AS first,
       count(DISTINCT provider_event_id) AS events, count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, 3 DESC;

\echo '== N1 · NO_VENUE_CONTRACT events of the latest cycle, and whether the VENUE lists them =='
WITH last AS (
  SELECT cycle_id FROM ext_candidate_outcomes
   ORDER BY cycle_at DESC, id DESC LIMIT 1
), ev AS (
  SELECT o.sport_key, o.home, o.away, o.commence_time,
         lower(regexp_replace(o.home, '^.* ', '')) AS h_last,
         lower(regexp_replace(o.away, '^.* ', '')) AS a_last,
         nullif(o.commence_time, '')::timestamptz AS starts
    FROM ext_candidate_outcomes o, last
   WHERE o.cycle_id = last.cycle_id
     AND o.first_refusal = 'NO_VENUE_CONTRACT_FOR_EVENT'
)
SELECT ev.sport_key, ev.home, ev.away, ev.starts,
       (SELECT count(DISTINCT p.event_slug) FROM us_premap p
         WHERE p.game_start BETWEEN ev.starts - interval '18 hours'
                                AND ev.starts + interval '18 hours'
           AND lower(p.event_title) LIKE '%' || ev.h_last || '%'
           AND lower(p.event_title) LIKE '%' || ev.a_last || '%')
                                                     AS venue_events_same_day,
       (SELECT string_agg(DISTINCT p.event_slug, ',') FROM us_premap p
         WHERE p.game_start BETWEEN ev.starts - interval '18 hours'
                                AND ev.starts + interval '18 hours'
           AND lower(p.event_title) LIKE '%' || ev.h_last || '%'
           AND lower(p.event_title) LIKE '%' || ev.a_last || '%')
                                                     AS venue_event_slugs,
       (SELECT string_agg(DISTINCT p.sports_type, ',') FROM us_premap p
         WHERE p.game_start BETWEEN ev.starts - interval '18 hours'
                                AND ev.starts + interval '18 hours'
           AND lower(p.event_title) LIKE '%' || ev.h_last || '%'
           AND lower(p.event_title) LIKE '%' || ev.a_last || '%'
           AND p.sports_type ~ '(_full_game_winner|_full_time_winner)$')
                                                     AS venue_winner_types,
       (SELECT count(*) FROM markets m
         WHERE NOT m.closed AND NOT m.resolved
           AND lower(m.title || ' ' || coalesce(m.event_title, ''))
               LIKE '%' || ev.h_last || '%'
           AND lower(m.title || ' ' || coalesce(m.event_title, ''))
               LIKE '%' || ev.a_last || '%')          AS global_rows_matching
  FROM ev
 ORDER BY ev.sport_key, ev.starts;

\echo '== N2 · NO_PINNACLE events of the latest cycle: competition and hours to the start =='
WITH last AS (
  SELECT cycle_id, cycle_at FROM ext_candidate_outcomes
   ORDER BY cycle_at DESC, id DESC LIMIT 1
)
SELECT o.sport_key, o.home, o.away,
       round(extract(epoch FROM nullif(o.commence_time, '')::timestamptz
                                - last.cycle_at)::numeric / 3600, 1)
                                                        AS hours_to_start
  FROM ext_candidate_outcomes o, last
 WHERE o.cycle_id = last.cycle_id
   AND o.first_refusal = 'NO_PINNACLE_ON_EVENT'
 ORDER BY 1, 4;

\echo '== N3 · last 24 h: Pinnacle presence by hours-to-start band (distinct events) =='
SELECT sport_key,
       CASE WHEN h < 3 THEN 'a <3h' WHEN h < 12 THEN 'b 3-12h'
            WHEN h < 24 THEN 'c 12-24h' WHEN h < 72 THEN 'd 1-3d'
            ELSE 'e >3d' END AS band,
       count(DISTINCT provider_event_id) FILTER (
           WHERE first_refusal = 'NO_PINNACLE_ON_EVENT') AS no_pinnacle,
       count(DISTINCT provider_event_id) FILTER (
           WHERE first_refusal IS DISTINCT FROM 'NO_PINNACLE_ON_EVENT')
                                                          AS with_pinnacle
  FROM (SELECT sport_key, provider_event_id, first_refusal,
               extract(epoch FROM nullif(commence_time, '')::timestamptz
                                  - cycle_at) / 3600 AS h
          FROM ext_candidate_outcomes
         WHERE cycle_at > now() - interval '24 hours') x
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '== N4 · the latest heartbeat: stale-on-arrival split and every mapped candidate =='
SELECT value->'odds_freshness'->'latency'                   AS latency,
       jsonb_path_query_array(value, '$.mapped_candidate_ledger[*] ? (@.stage == "2_FRESHNESS")')
                                                            AS stale_on_arrival_rows
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== N5 · VENUE_MAPPING_AMBIGUOUS and LINE events of the latest cycle =='
WITH last AS (
  SELECT cycle_id FROM ext_candidate_outcomes
   ORDER BY cycle_at DESC, id DESC LIMIT 1
)
SELECT o.sport_key, o.home, o.away, o.commence_time, o.first_refusal, o.codes
  FROM ext_candidate_outcomes o, last
 WHERE o.cycle_id = last.cycle_id
   AND o.first_refusal NOT IN ('NO_VENUE_CONTRACT_FOR_EVENT',
                               'NO_PINNACLE_ON_EVENT')
 ORDER BY 1;
