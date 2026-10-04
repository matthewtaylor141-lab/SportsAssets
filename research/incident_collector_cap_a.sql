-- P0 INCIDENT (2026-10-04) segment COLLECTOR SPORT CAP / NCAAF STARVATION.
-- Read-only. Last 7 days. Bounded: aggregates only, LIMITs on every row dump.
-- Never reads or prints a key. Measures how often each competition was not
-- fetched by the metered collector (MAX_METERED_SPORTS_PER_CYCLE = 4) and how
-- often MAX_PER_CYCLE = 40 truncated a fetched competition, per sport/league.
--
-- SCHEDULED vs REACTIVE CYCLES. ext_candidate_outcomes holds one row per
-- provider event per cycle for BOTH the scheduled collector cycle and the
-- PinnAPI reactive one-event cycle (stream_seed, queue_position 0, one row).
-- A scheduled cycle is a cycle_id with >= 2 rows (every scheduled cycle
-- fetches the confirmed baseball_mlb key first).
\echo '== C0 read instant =='
SELECT now() AS read_at;

\echo '== C1 latest scheduled collector heartbeat: selection, budget, credits, timing =='
SELECT to_timestamp((value->>'at')::float8) AS at, value->>'state' AS state,
       value->>'elapsed_s' AS elapsed_s, value->>'evaluated' AS evaluated,
       value->>'written' AS written, value->'credits' AS credits,
       value->'step_timing_s' AS step_timing_s,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS metered_budget,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'rejected' AS rejected,
       value->'sports_selection'->'venue_board'->'tokens' AS soccer_board_tokens,
       value->'sports_selection'->'venue_football_board'->'tokens' AS football_board,
       value->'odds_freshness'->'deferred_candidates' AS deferred_candidates,
       value->'odds_freshness'->'venue_requests' AS venue_requests,
       value->'writer' AS writer
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== C1b latest heartbeat: confirmed_by_provider with fixture confirmation =='
SELECT c->>'key' AS key, c->>'our_token' AS token, c->>'venue_events' AS venue_events,
       c->>'provider_title' AS provider_title, c->'fixture_confirmation' AS fixture_confirmation
  FROM ingestion_state,
       jsonb_array_elements(CASE WHEN jsonb_typeof(value->'sports_selection'->'confirmed_by_provider') = 'array'
                                 THEN value->'sports_selection'->'confirmed_by_provider' ELSE '[]'::jsonb END) AS c
 WHERE key = 'ext_pinnacle_last_cycle' LIMIT 20;

\echo '== C1c latest heartbeat: funnel by provider sport =='
SELECT f.key AS sport_key, f.value->>'provider_events' AS provider_events,
       f.value->>'with_pinnacle_h2h' AS with_pinnacle,
       f.value->>'mapped_to_a_venue_contract' AS mapped,
       f.value->>'identity_resolved' AS identity_resolved,
       f.value->>'evaluated' AS evaluated, f.value->>'written' AS written,
       left((f.value->'refusals')::text, 300) AS refusals
  FROM ingestion_state s,
       jsonb_each(CASE WHEN jsonb_typeof(s.value->'funnel_by_provider_sport') = 'object'
                       THEN s.value->'funnel_by_provider_sport' ELSE '{}'::jsonb END) AS f
 WHERE s.key = 'ext_pinnacle_last_cycle';

\echo '== C2 PinnAPI arm / scope rows and feed owner sport_ids, state =='
SELECT key, CASE WHEN key IN ('pinnapi_feed', 'pinnapi_feed_scope') THEN value::text END AS value
  FROM ingestion_state WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope');
SELECT value->>'state' AS state, value->'sport_ids' AS sport_ids, value->'streams' AS streams,
       round((extract(epoch FROM now()) - (value->>'beat_at')::float8)::numeric, 1) AS beat_age_s,
       value->'cache'->'authority' AS authority,
       (value->'cache'->>'events')::int AS cache_events
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== C2b PinnAPI cache markets by sport | type | stream (moneyline only) =='
SELECT split_part(e.k, '|', 1) AS sport_id, split_part(e.k, '|', 3) AS stream, e.v::text AS markets
  FROM ingestion_state,
       jsonb_each(CASE WHEN jsonb_typeof(value->'cache'->'markets_by_sport_type_phase') = 'object'
                       THEN value->'cache'->'markets_by_sport_type_phase' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE key = 'pinnapi_feed_last' AND split_part(e.k, '|', 2) = 'moneyline'
 ORDER BY 1, 2;

\echo '== C3 reactive (WS) attempts by day and state, 7 d =='
SELECT date_trunc('day', created_at) AS day, state, count(*) AS n
  FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== C3b reactive scheduler counters at the latest attempt (discovery-seed dependence) =='
SELECT created_at, state, detail->'counters' AS counters
  FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '7 days'
 ORDER BY created_at DESC LIMIT 1;

\echo '== C4 scheduled cycles per day: cadence (start-to-start gap), rows, MAX_PER_CYCLE truncation =='
WITH cyc AS (
  SELECT cycle_id, min(cycle_at) AS cycle_at, count(*) AS rows,
         count(DISTINCT sport_key) AS sports,
         count(*) FILTER (WHERE outcome = 'DEFERRED') AS deferred,
         count(*) FILTER (WHERE outcome <> 'DEFERRED') AS judged
    FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2
), g AS (
  SELECT *, extract(epoch FROM cycle_at - lag(cycle_at) OVER (ORDER BY cycle_at)) AS gap_s
    FROM cyc)
SELECT date_trunc('day', cycle_at) AS day, count(*) AS scheduled_cycles,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY gap_s) AS gap_p50_s,
       percentile_disc(0.9) WITHIN GROUP (ORDER BY gap_s) AS gap_p90_s,
       round(max(gap_s)::numeric, 0) AS gap_max_s,
       count(*) FILTER (WHERE gap_s > 960) AS gaps_over_960s,
       count(*) FILTER (WHERE deferred > 0) AS cycles_truncated_by_max_per_cycle,
       sum(deferred) AS deferred_rows, sum(judged) AS judged_rows,
       round(avg(rows), 1) AS avg_rows, max(sports) AS max_sports
  FROM g GROUP BY 1 ORDER BY 1;

\echo '== C4b which build wrote the scheduled cycles (deploy boundaries) =='
SELECT writer, min(cycle_at) AS first, max(cycle_at) AS last, count(DISTINCT cycle_id) AS cycles,
       array_agg(DISTINCT sport_key ORDER BY sport_key) AS sport_keys
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '7 days'
   AND cycle_id IN (SELECT cycle_id FROM ext_candidate_outcomes
                     WHERE cycle_at >= now() - interval '7 days'
                     GROUP BY cycle_id HAVING count(*) >= 2)
 GROUP BY writer ORDER BY 2 LIMIT 30;

\echo '== C5 per day x provider sport: scheduled cycles that fetched it, MAX_PER_CYCLE deferrals =='
WITH cyc AS (
  SELECT cycle_id, min(cycle_at) AS cycle_at FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2
), per_day AS (
  SELECT date_trunc('day', cycle_at) AS day, count(*) AS cycles FROM cyc GROUP BY 1
)
SELECT date_trunc('day', c.cycle_at) AS day, o.sport_key,
       max(p.cycles) AS scheduled_cycles_that_day,
       count(DISTINCT o.cycle_id) AS cycles_fetched,
       count(*) AS rows,
       count(*) FILTER (WHERE o.outcome = 'DEFERRED') AS deferred_rows,
       count(DISTINCT o.cycle_id) FILTER (WHERE o.outcome = 'DEFERRED') AS cycles_with_deferral,
       count(DISTINCT o.provider_event_id) AS distinct_events,
       round(avg(o.queue_position) FILTER (WHERE o.outcome = 'DEFERRED'), 1) AS avg_deferred_queue_pos
  FROM ext_candidate_outcomes o JOIN cyc c USING (cycle_id)
  JOIN per_day p ON p.day = date_trunc('day', c.cycle_at)
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== C5b events ONLY EVER DEFERRED (never judged by any scheduled or reactive cycle that day), per day x sport =='
WITH sched AS (
  SELECT cycle_id FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2
), ev AS (
  SELECT date_trunc('day', o.cycle_at) AS day, o.sport_key, o.provider_event_id,
         count(*) FILTER (WHERE o.outcome <> 'DEFERRED' AND s.cycle_id IS NOT NULL) AS judged_sched,
         count(*) FILTER (WHERE o.outcome <> 'DEFERRED') AS judged_any,
         count(*) FILTER (WHERE o.outcome = 'DEFERRED') AS deferred
    FROM ext_candidate_outcomes o LEFT JOIN sched s USING (cycle_id)
   WHERE o.cycle_at >= now() - interval '7 days'
   GROUP BY 1, 2, 3)
SELECT day, sport_key, count(*) AS events,
       count(*) FILTER (WHERE deferred > 0) AS events_deferred_at_least_once,
       count(*) FILTER (WHERE deferred > 0 AND judged_sched = 0) AS never_judged_by_a_scheduled_cycle,
       count(*) FILTER (WHERE deferred > 0 AND judged_any = 0) AS never_judged_at_all
  FROM ev GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== C6 per competition: scheduled cycles with a venue event in the next 24 h, fetched or not (metered-slot starvation) =='
WITH tok(token, provider_key, fam) AS (VALUES
  ('mlb', 'baseball_mlb', 'baseball'),
  ('cfb', 'americanfootball_ncaaf', 'football'), ('nfl', 'americanfootball_nfl', 'football'),
  ('unl', 'soccer_uefa_nations_league', 'soccer'), ('mls', 'soccer_usa_mls', 'soccer'),
  ('lmx', 'soccer_mexico_ligamx', 'soccer'), ('uwcl', 'soccer_uefa_champs_league_women', 'soccer'),
  ('cnl', 'soccer_concacaf_nations_league', 'soccer'), ('uslc', 'soccer_usa_usl_championship', 'soccer'),
  ('arg2', 'soccer_argentina_primera_nacional', 'soccer'), ('brb', 'soccer_brazil_serie_b', 'soccer'),
  ('lco', 'soccer_colombia_primera_a', 'soccer'), ('uru1', 'soccer_uruguay_primera_division', 'soccer'),
  ('nwsl', 'soccer_usa_nwsl', 'soccer')
), ve AS (
  SELECT DISTINCT split_part(market_slug, '-', 2) AS token, event_slug, game_start
    FROM us_premap
   WHERE game_start >= now() - interval '8 days' AND game_start < now() + interval '4 days'
     AND split_part(market_slug, '-', 2) IN (SELECT token FROM tok)
     AND (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%' OR sports_type LIKE 'baseball%')
     AND lower(coalesce(event_title, '')) NOT LIKE '%ebattle%'
), cyc AS (
  SELECT cycle_id, min(cycle_at) AS cycle_at FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2
), fetched AS (
  SELECT DISTINCT o.cycle_id, o.sport_key FROM ext_candidate_outcomes o JOIN cyc USING (cycle_id)
), grid AS (
  SELECT c.cycle_id, c.cycle_at, t.token, t.provider_key,
         count(ve.event_slug) FILTER (WHERE ve.game_start >= c.cycle_at
                                        AND ve.game_start < c.cycle_at + interval '24 hours') AS ev24,
         count(ve.event_slug) FILTER (WHERE ve.game_start >= c.cycle_at
                                        AND ve.game_start < c.cycle_at + interval '6 hours') AS ev6
    FROM cyc c CROSS JOIN tok t
    LEFT JOIN ve ON ve.token = t.token AND ve.game_start >= c.cycle_at
                AND ve.game_start < c.cycle_at + interval '24 hours'
   GROUP BY 1, 2, 3, 4
), g2 AS (
  SELECT grid.*, (f.cycle_id IS NOT NULL) AS was_fetched
    FROM grid LEFT JOIN fetched f ON f.cycle_id = grid.cycle_id AND f.sport_key = grid.provider_key
)
SELECT token, provider_key, count(*) AS scheduled_cycles,
       count(*) FILTER (WHERE ev24 > 0) AS cycles_with_venue_event_24h,
       count(*) FILTER (WHERE ev24 > 0 AND was_fetched) AS fetched_with_event_24h,
       count(*) FILTER (WHERE ev24 > 0 AND NOT was_fetched) AS NOT_fetched_with_event_24h,
       count(*) FILTER (WHERE ev6 > 0) AS cycles_with_venue_event_6h,
       count(*) FILTER (WHERE ev6 > 0 AND NOT was_fetched) AS NOT_fetched_with_event_6h,
       count(*) FILTER (WHERE ev24 = 0 AND was_fetched) AS fetched_with_no_event_24h,
       round(avg(ev24) FILTER (WHERE ev24 > 0), 1) AS avg_events_24h_when_present
  FROM g2 GROUP BY 1, 2 ORDER BY 1;

\echo '== C6b the same, per day, for the football tokens and the top soccer tokens =='
WITH tok(token, provider_key) AS (VALUES
  ('mlb', 'baseball_mlb'), ('cfb', 'americanfootball_ncaaf'), ('nfl', 'americanfootball_nfl'),
  ('unl', 'soccer_uefa_nations_league'), ('brb', 'soccer_brazil_serie_b'),
  ('mls', 'soccer_usa_mls'), ('lmx', 'soccer_mexico_ligamx'), ('arg2', 'soccer_argentina_primera_nacional'),
  ('lco', 'soccer_colombia_primera_a'), ('uslc', 'soccer_usa_usl_championship'),
  ('uwcl', 'soccer_uefa_champs_league_women'), ('cnl', 'soccer_concacaf_nations_league'),
  ('uru1', 'soccer_uruguay_primera_division'), ('nwsl', 'soccer_usa_nwsl')
), ve AS (
  SELECT DISTINCT split_part(market_slug, '-', 2) AS token, event_slug, game_start
    FROM us_premap
   WHERE game_start >= now() - interval '8 days' AND game_start < now() + interval '4 days'
     AND split_part(market_slug, '-', 2) IN (SELECT token FROM tok)
     AND (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%' OR sports_type LIKE 'baseball%')
     AND lower(coalesce(event_title, '')) NOT LIKE '%ebattle%'
), cyc AS (
  SELECT cycle_id, min(cycle_at) AS cycle_at FROM ext_candidate_outcomes
   WHERE cycle_at >= now() - interval '7 days'
   GROUP BY cycle_id HAVING count(*) >= 2
), fetched AS (
  SELECT DISTINCT o.cycle_id, o.sport_key FROM ext_candidate_outcomes o JOIN cyc USING (cycle_id)
), grid AS (
  SELECT c.cycle_id, c.cycle_at, t.token, t.provider_key, count(ve.event_slug) AS ev24
    FROM cyc c CROSS JOIN tok t
    LEFT JOIN ve ON ve.token = t.token AND ve.game_start >= c.cycle_at
                AND ve.game_start < c.cycle_at + interval '24 hours'
   GROUP BY 1, 2, 3, 4
)
SELECT date_trunc('day', g.cycle_at) AS day, g.token, count(*) AS cycles,
       count(*) FILTER (WHERE g.ev24 > 0) AS with_event_24h,
       count(*) FILTER (WHERE g.ev24 > 0 AND f.cycle_id IS NOT NULL) AS fetched,
       count(*) FILTER (WHERE g.ev24 > 0 AND f.cycle_id IS NULL) AS not_fetched,
       max(g.ev24) AS max_events_24h
  FROM grid g LEFT JOIN fetched f ON f.cycle_id = g.cycle_id AND f.sport_key = g.provider_key
 GROUP BY 1, 2 HAVING count(*) FILTER (WHERE g.ev24 > 0) > 0
 ORDER BY 1, 2;

\echo '== C7 the venue board today and next 3 days: distinct events per mapped token per ET day =='
SELECT (game_start AT TIME ZONE 'America/New_York')::date AS et_day,
       split_part(market_slug, '-', 2) AS token, count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE game_start >= now() - interval '8 days' AND game_start < now() + interval '4 days'
   AND (sports_type LIKE 'soccer%' OR sports_type LIKE 'football%' OR sports_type LIKE 'baseball%'
        OR sports_type LIKE 'basketball%' OR sports_type LIKE 'hockey%' OR sports_type LIKE 'tennis%')
   AND lower(coalesce(event_title, '')) NOT LIKE '%ebattle%'
 GROUP BY 1, 2 HAVING count(DISTINCT event_slug) >= 3
 ORDER BY 1, 3 DESC LIMIT 200;

\echo '== C8 which source supplied the probability: valuations by day x family x provider, 7 d =='
SELECT date_trunc('day', decided_at) AS day, sport_family, provider, record_purpose,
       count(*) AS n, count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability
  FROM external_valuations
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

\echo '== C8b reference_input provider and fallback reason by family, 7 d =='
SELECT sport_family,
       settlement_comparison->'reference_input'->>'provider' AS ref_provider,
       settlement_comparison->'reference_input'->>'fallback_reason' AS fallback_reason,
       count(*) AS n
  FROM external_valuations
 WHERE decided_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 60;
