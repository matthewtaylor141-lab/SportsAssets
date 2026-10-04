-- P0 INCIDENT (coverage -> trade starvation), segment VENUE EVENT + MARKET
-- CATALOGUE COVERAGE. Read-only. Bounded: every statement is windowed and
-- every row dump carries a LIMIT.
--
-- QUESTION. What does the execution venue (Polymarket US, via the premap
-- sweep's catalogue `us_premap`) list right now -- by sport, league and
-- MARKET FAMILY -- and how much of it does BETTOR read, map and value?
--
-- DEFINITIONS (stated once, used by every statement below)
--   current listing  a us_premap row with game_start >= now() - 6 h (the
--                    census / board base window) that the sweep re-saw within
--                    90 min (bettor_venue_native_identity.RESEEN_WITHIN_S)
--   sport            the venue sportsMarketType's leading word
--                    (table_tennis kept whole)
--   league           the venue event slug's first segment (`nfl-ind-was-...`)
--   family           derived from the venue's own sportsMarketType text:
--                    WINNER / SPREAD / TOTAL / TEAM_TOTAL / PERIOD /
--                    PLAYER_PROP / BTTS / EXACT_SCORE / DRAW / OTHER / NO_TYPE
--   BETTOR mapped    any ext_candidate_outcomes row in the last 24 h whose
--                    us_market_slug belongs to the venue event
--   BETTOR valued    any external_valuations row in the last 24 h whose
--                    us_market_slug belongs to the venue event
\echo '== V0 · read instant and schema head =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et,
       (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== V1a · premap sweep state (full and fast lanes) =='
SELECT key, value->>'at' AS at, value->>'lane' AS lane, value->>'mode' AS mode,
       value->>'events' AS events, value->>'rows' AS sweep_rows,
       value->>'pages_walked' AS pages_walked, value->>'max_pages' AS max_pages,
       value->>'truncated' AS truncated, value->>'pruned' AS pruned,
       value->'window_h' AS window_h, left(coalesce(value->>'err', ''), 160) AS err,
       left(coalesce(value->>'events_err', ''), 160) AS events_err
  FROM ingestion_state WHERE key IN ('premap_last', 'premap_last_fast');

\echo '== V1b · us_premap size, recency and start-time spread =='
SELECT count(*) AS rows_all,
       count(*) FILTER (WHERE updated_at >= now() - interval '35 minutes') AS reseen_35m,
       count(*) FILTER (WHERE updated_at >= now() - interval '90 minutes') AS reseen_90m,
       count(*) FILTER (WHERE updated_at <  now() - interval '90 minutes') AS not_reseen_90m,
       min(updated_at) AS oldest_seen, max(updated_at) AS newest_seen,
       count(*) FILTER (WHERE game_start IS NULL) AS no_game_start,
       count(*) FILTER (WHERE game_start <  now() - interval '6 hours') AS start_before_minus6h,
       count(*) FILTER (WHERE game_start >= now() - interval '6 hours' AND game_start < now()) AS start_last_6h,
       count(*) FILTER (WHERE game_start >= now() AND game_start < now() + interval '24 hours') AS start_next_24h,
       count(*) FILTER (WHERE game_start >= now() + interval '24 hours' AND game_start < now() + interval '96 hours') AS start_24_96h,
       count(*) FILTER (WHERE game_start >= now() + interval '96 hours') AS start_after_96h,
       count(DISTINCT event_slug) AS events_all, count(DISTINCT market_slug) AS contracts_all
  FROM us_premap;

\echo '== V2 · EVERY venue sportsMarketType in the current listing (raw vocabulary) =='
SELECT coalesce(nullif(sports_type, ''), '(none)') AS sports_type,
       count(*) AS sides, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT lower(split_part(coalesce(event_slug, ''), '-', 1))) AS leagues,
       (array_agg(DISTINCT lower(split_part(coalesce(event_slug, ''), '-', 1))))[1:8] AS league_sample,
       (array_agg(DISTINCT split_part(market_slug, '-', 1)))[1:6] AS slug_prefixes,
       (array_agg(DISTINCT kind))[1:3] AS kinds,
       count(*) FILTER (WHERE coalesce(line, '') <> '') AS sides_with_line,
       count(*) FILTER (WHERE intent IS NULL) AS sides_without_intent,
       min(market_slug) AS example
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
 GROUP BY 1
 ORDER BY 4 DESC, 3 DESC
 LIMIT 400;

\echo '== V3 · rows with NO sportsMarketType in the current listing, by slug prefix and league =='
SELECT split_part(market_slug, '-', 1) AS slug_prefix,
       lower(split_part(coalesce(event_slug, ''), '-', 1)) AS league,
       kind, count(*) AS sides, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) AS events, min(market_slug) AS example
  FROM us_premap
 WHERE game_start >= now() - interval '6 hours'
   AND updated_at >= now() - interval '90 minutes'
   AND coalesce(sports_type, '') = ''
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 60;

\echo '== V4 · per SPORT: events and contracts by derived family (current listing) =='
WITH cur AS (
    SELECT event_slug, market_slug, game_start,
           CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
                ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END AS sport,
           CASE WHEN coalesce(sports_type, '') = '' THEN 'NO_TYPE'
                WHEN sports_type ~ '_player_' THEN 'PLAYER_PROP'
                WHEN sports_type ~ '(first_five|inning|half|quarter|period|_set_|_set$|sets_|_map|frame|_leg_)' THEN 'PERIOD'
                WHEN sports_type ~ '(spread|handicap)' THEN 'SPREAD'
                WHEN sports_type ~ 'team_total' THEN 'TEAM_TOTAL'
                WHEN sports_type ~ 'total' THEN 'TOTAL'
                WHEN sports_type ~ '(btts|both_teams)' THEN 'BTTS'
                WHEN sports_type ~ '(exact_score|correct_score)' THEN 'EXACT_SCORE'
                WHEN sports_type ~ 'draw' THEN 'DRAW'
                WHEN sports_type ~ 'winner$' THEN 'WINNER'
                ELSE 'OTHER' END AS fam
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes')
SELECT sport,
       count(DISTINCT lower(split_part(coalesce(event_slug, ''), '-', 1))) AS leagues,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT event_slug) FILTER (WHERE game_start < now() + interval '24 hours') AS events_next_24h,
       count(DISTINCT event_slug) FILTER (WHERE game_start <= now()) AS events_started,
       count(DISTINCT event_slug) FILTER (WHERE fam = 'WINNER') AS events_with_winner,
       count(DISTINCT market_slug) AS contracts,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'WINNER') AS winner,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'SPREAD') AS spread,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'TOTAL') AS total,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'TEAM_TOTAL') AS team_total,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'PERIOD') AS period,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'PLAYER_PROP') AS player_prop,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'BTTS') AS btts,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'EXACT_SCORE') AS exact_score,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'DRAW') AS draw,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'OTHER') AS other,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'NO_TYPE') AS no_type,
       round(count(DISTINCT market_slug)::numeric / nullif(count(DISTINCT event_slug), 0), 1) AS contracts_per_event
  FROM cur GROUP BY 1 ORDER BY 3 DESC LIMIT 60;

\echo '== V5 · per SPORT x LEAGUE: events and contracts by derived family (current listing) =='
WITH cur AS (
    SELECT event_slug, market_slug, game_start,
           lower(split_part(coalesce(event_slug, ''), '-', 1)) AS league,
           CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
                ELSE coalesce(nullif(split_part(coalesce(sports_type, ''), '_', 1), ''), '(none)') END AS sport,
           CASE WHEN coalesce(sports_type, '') = '' THEN 'NO_TYPE'
                WHEN sports_type ~ '_player_' THEN 'PLAYER_PROP'
                WHEN sports_type ~ '(first_five|inning|half|quarter|period|_set_|_set$|sets_|_map|frame|_leg_)' THEN 'PERIOD'
                WHEN sports_type ~ '(spread|handicap)' THEN 'SPREAD'
                WHEN sports_type ~ 'team_total' THEN 'TEAM_TOTAL'
                WHEN sports_type ~ 'total' THEN 'TOTAL'
                WHEN sports_type ~ '(btts|both_teams)' THEN 'BTTS'
                WHEN sports_type ~ '(exact_score|correct_score)' THEN 'EXACT_SCORE'
                WHEN sports_type ~ 'draw' THEN 'DRAW'
                WHEN sports_type ~ 'winner$' THEN 'WINNER'
                ELSE 'OTHER' END AS fam
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes')
SELECT sport, league,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT event_slug) FILTER (WHERE game_start < now() + interval '24 hours') AS ev_24h,
       count(DISTINCT event_slug) FILTER (WHERE fam = 'WINNER') AS ev_winner,
       count(DISTINCT market_slug) AS contracts,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'WINNER') AS winner,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'SPREAD') AS spread,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'TOTAL') AS total,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'TEAM_TOTAL') AS team_tot,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'PERIOD') AS period,
       count(DISTINCT market_slug) FILTER (WHERE fam = 'PLAYER_PROP') AS prop,
       count(DISTINCT market_slug) FILTER (WHERE fam IN ('BTTS', 'EXACT_SCORE', 'DRAW', 'OTHER', 'NO_TYPE')) AS rest
  FROM cur GROUP BY 1, 2
 ORDER BY 3 DESC LIMIT 250;

\echo '== V6 · winner contracts: sides per contract and contracts per event, by winner type =='
WITH w AS (
    SELECT sports_type, event_slug, market_slug, count(*) AS sides,
           count(*) FILTER (WHERE intent = 'ORDER_INTENT_BUY_LONG') AS long_sides,
           count(*) FILTER (WHERE intent = 'ORDER_INTENT_BUY_SHORT') AS short_sides,
           count(*) FILTER (WHERE coalesce(team_name, '') = '') AS sides_without_team
      FROM us_premap
     WHERE game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
       AND sports_type ~ 'winner$'
     GROUP BY 1, 2, 3)
SELECT sports_type, count(DISTINCT event_slug) AS events, count(*) AS contracts,
       round(count(*)::numeric / nullif(count(DISTINCT event_slug), 0), 2) AS contracts_per_event,
       sum(sides) AS sides, sum(long_sides) AS long_sides, sum(short_sides) AS short_sides,
       sum(sides_without_team) AS sides_without_team
  FROM w GROUP BY 1 ORDER BY 2 DESC LIMIT 60;

\echo '== V7 · WHAT BETTOR VALUED (external_valuations, last 24 h) by venue league x family x market =='
WITH ms AS (SELECT DISTINCT market_slug, event_slug FROM us_premap
             WHERE game_start >= now() - interval '40 hours')
SELECT lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league,
       v.sport_family, v.market, coalesce(v.period, '-') AS period,
       split_part(coalesce(v.us_market_slug, ''), '-', 1) AS slug_prefix,
       count(*) AS valuation_rows,
       count(*) FILTER (WHERE v.record_purpose = 'ENTRY_DECISION') AS entry_rows,
       count(*) FILTER (WHERE v.record_purpose = 'CALIBRATION_ONLY') AS calibration_rows,
       count(DISTINCT v.us_market_slug) AS contracts,
       count(DISTINCT v.us_market_slug || '|' || coalesce(v.buy_intent, '')) AS instruments,
       count(DISTINCT v.event_key) AS provider_events,
       count(DISTINCT ms.event_slug) AS venue_events,
       count(DISTINCT v.matched_side_norm) AS distinct_sides
  FROM external_valuations v
  LEFT JOIN ms ON ms.market_slug = v.us_market_slug
 WHERE v.decided_at >= now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 80;

\echo '== V8 · instruments valued PER PROVIDER EVENT (one-market-per-event check), last 24 h =='
WITH e AS (
    SELECT sport_family, event_key,
           count(DISTINCT us_market_slug) AS contracts,
           count(DISTINCT us_market_slug || '|' || coalesce(buy_intent, '')) AS instruments,
           count(DISTINCT coalesce(payout_event, mapped_outcome, '')) AS payout_events,
           count(DISTINCT market) AS markets
      FROM external_valuations
     WHERE decided_at >= now() - interval '24 hours' AND event_key IS NOT NULL
     GROUP BY 1, 2)
SELECT sport_family, count(*) AS provider_events,
       count(*) FILTER (WHERE instruments = 1) AS one_instrument,
       count(*) FILTER (WHERE instruments = 2) AS two_instruments,
       count(*) FILTER (WHERE instruments >= 3) AS three_plus,
       count(*) FILTER (WHERE payout_events = 1) AS one_payout_event,
       max(instruments) AS max_instruments, max(markets) AS max_markets
  FROM e GROUP BY 1 ORDER BY 2 DESC;

\echo '== V9 · collection ledger (ext_candidate_outcomes, last 24 h) per provider competition =='
WITH r AS (
    SELECT sport_key, cycle_id, provider_event_id, us_market_slug, outcome,
           first_refusal, mapped_by
      FROM ext_candidate_outcomes
     WHERE cycle_at >= now() - interval '24 hours')
SELECT sport_key,
       count(DISTINCT cycle_id) AS cycles_requested,
       count(DISTINCT provider_event_id) AS provider_events,
       count(DISTINCT provider_event_id) FILTER (WHERE us_market_slug IS NOT NULL) AS events_with_contract,
       count(DISTINCT provider_event_id) FILTER (WHERE outcome IN ('ADMITTED', 'ALREADY_RECORDED')) AS events_admitted,
       count(DISTINCT provider_event_id) FILTER (WHERE outcome = 'DEFERRED') AS events_deferred,
       count(DISTINCT provider_event_id) FILTER (WHERE mapped_by = 'VENUE_NATIVE') AS events_venue_native,
       count(*) AS rows
  FROM r GROUP BY 1 ORDER BY 3 DESC LIMIT 40;

\echo '== V9b · first refusals per competition (distinct provider events, last 24 h) =='
SELECT sport_key, first_refusal, count(DISTINCT provider_event_id) AS events, count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '24 hours' AND first_refusal IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 120;

\echo '== V9c · cycles in the last 24 h and how many competitions each requested =='
SELECT count(DISTINCT cycle_id) AS cycles,
       round(avg(n), 2) AS avg_competitions_per_cycle, max(n) AS max_competitions_per_cycle
  FROM (SELECT cycle_id, count(DISTINCT sport_key) AS n
          FROM ext_candidate_outcomes
         WHERE cycle_at >= now() - interval '24 hours'
         GROUP BY 1) t;

\echo '== V10 · VENUE vs BETTOR per sport x league: venue events starting in [now-24h, now+24h) =='
WITH ev AS (
    SELECT event_slug,
           lower(split_part(event_slug, '-', 1)) AS league,
           max(CASE WHEN sports_type LIKE 'table_tennis%' THEN 'table_tennis'
                    ELSE split_part(coalesce(sports_type, ''), '_', 1) END) AS sport,
           bool_or(sports_type ~ 'winner$') AS has_winner,
           bool_or(sports_type ~ '(spread|handicap)') AS has_spread,
           bool_or(sports_type ~ 'total' AND sports_type !~ 'team_total') AS has_total
      FROM us_premap
     WHERE game_start >= now() - interval '24 hours'
       AND game_start <  now() + interval '24 hours'
       AND coalesce(event_slug, '') <> ''
     GROUP BY 1),
ms AS (SELECT DISTINCT market_slug, event_slug FROM us_premap
        WHERE game_start >= now() - interval '40 hours'),
co AS (SELECT DISTINCT ms.event_slug
         FROM ext_candidate_outcomes c JOIN ms ON ms.market_slug = c.us_market_slug
        WHERE c.cycle_at >= now() - interval '24 hours'),
va AS (SELECT ms.event_slug,
              count(DISTINCT v.us_market_slug || '|' || coalesce(v.buy_intent, '')) AS instruments
         FROM external_valuations v JOIN ms ON ms.market_slug = v.us_market_slug
        WHERE v.decided_at >= now() - interval '24 hours'
        GROUP BY 1)
SELECT ev.sport, ev.league,
       count(*) AS venue_events,
       count(*) FILTER (WHERE has_winner) AS with_winner,
       count(*) FILTER (WHERE has_spread) AS with_spread,
       count(*) FILTER (WHERE has_total) AS with_total,
       count(*) FILTER (WHERE co.event_slug IS NOT NULL) AS bettor_mapped,
       count(*) FILTER (WHERE va.event_slug IS NOT NULL) AS bettor_valued,
       coalesce(sum(va.instruments), 0) AS valued_instruments
  FROM ev LEFT JOIN co USING (event_slug) LEFT JOIN va USING (event_slug)
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 250;

\echo '== V11 · PinnAPI scope and feed heartbeat (Pinnacle side, subscribed sports only) =='
SELECT key, left(value::text, 400) AS value
  FROM ingestion_state WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope');
SELECT value->>'state' AS state, to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'sport_ids' AS sport_ids, value->'cache'->'events' AS cache_events,
       value->'cache'->'markets' AS cache_markets,
       value->'coverage_census'->'states' AS census_states,
       value->'coverage_census'->'events_by_state' AS census_events_by_state,
       value->'coverage_census'->'unsupported_reasons' AS census_unsupported_reasons,
       value->'coverage_census'->'total_contracts' AS census_total_contracts,
       value->'coverage_census'->'matched_events' AS census_matched_events,
       value->'coverage_census'->'truncated_at' AS census_truncated_at
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT k AS sport_type_phase, v AS markets
  FROM ingestion_state,
       jsonb_each(coalesce(value->'cache'->'markets_by_sport_type_phase', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'pinnapi_feed_last' ORDER BY 1 LIMIT 80;
SELECT k AS sport_family_phase_state, v AS contracts
  FROM ingestion_state,
       jsonb_each(CASE WHEN jsonb_typeof(value->'coverage_census'->'by_sport_family_phase_state') = 'object'
                       THEN value->'coverage_census'->'by_sport_family_phase_state'
                       ELSE '{}'::jsonb END) AS e(k, v)
 WHERE key = 'pinnapi_feed_last' ORDER BY (v::text)::numeric DESC LIMIT 80;

\echo '== V12 · the collector heartbeat: selection, budget, funnel per provider competition =='
SELECT to_timestamp((value->>'at')::float8) AS at, value->>'state' AS state,
       value->'evaluated' AS evaluated, value->'written' AS written,
       value->'markets_considered' AS markets_considered,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS budget,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'rejected' AS rejected,
       value->'sports_selection'->'venue_board'->'tokens' AS soccer_board_tokens,
       value->'sports_selection'->'venue_football_board'->'tokens' AS football_board_tokens,
       value->'venue_universe_by_label' AS venue_universe_by_label
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT k AS provider_competition, left(v::text, 900) AS funnel
  FROM ingestion_state, jsonb_each(coalesce(value->'funnel_by_provider_sport', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle' LIMIT 12;
SELECT k AS refusal, v AS n
  FROM ingestion_state, jsonb_each(coalesce(value->'refusals', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle' LIMIT 60;

\echo '== V13 · the soccer board as the collector ranks it (LIMIT 30 in _board_sql), real fixtures only =='
SELECT split_part(market_slug, '-', 2) AS token,
       count(DISTINCT event_slug) AS events,
       count(DISTINCT left(event_title, 80)) AS titles,
       rank() OVER (ORDER BY count(DISTINCT event_slug) DESC) AS board_rank
  FROM us_premap
 WHERE sports_type LIKE 'soccer%'
   AND game_start > now() - interval '6 hours'
   AND lower(coalesce(event_title, '') || ' ' || coalesce(question, '')) !~
       '(ebattles|e-battles|esoccer|e-soccer|ebasketball|efootball|e-football|simulated|cyber|virtual|e-cricket|ehockey|e-hockey|etennis|e-tennis)'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 80;

\echo '== V14 · resolver read bound (VENUE_NATIVE_SQL LIMIT 2000 per +-90 min): max rows in a +-2 h bucket window =='
WITH b AS (
    SELECT sports_type, date_trunc('hour', game_start) AS h, count(*) AS n
      FROM us_premap
     WHERE sports_type ~ 'winner$'
       AND game_start >= now() - interval '6 hours'
       AND updated_at >= now() - interval '90 minutes'
     GROUP BY 1, 2)
SELECT sports_type, max(s) AS max_rows_in_window, sum(n) AS rows_total
  FROM (SELECT sports_type, n,
               sum(n) OVER (PARTITION BY sports_type ORDER BY h
                            RANGE BETWEEN interval '2 hours' PRECEDING
                                      AND interval '2 hours' FOLLOWING) AS s
          FROM b) t
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo '== V15 · other venue catalogues present in the database (tables named kalshi / catalogue) =='
SELECT c.relname AS table_name, c.reltuples::bigint AS est_rows
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname = 'public'
   AND (c.relname LIKE '%kalshi%' OR c.relname LIKE '%catalog%' OR c.relname LIKE '%venue_market%'
        OR c.relname LIKE '%premap%' OR c.relname = 'markets')
 ORDER BY 1 LIMIT 40;

\echo '== V16 · the GLOBAL catalogue (`markets`) the cycle filters with VENUE_SPORT_LABELS (Soccer, MLB) =='
SELECT coalesce(sport, '(null)') AS sport_label,
       count(*) FILTER (WHERE NOT closed AND NOT resolved
                        AND updated_at >= now() - interval '2 days') AS open_and_fresh,
       count(*) AS rows_all
  FROM markets GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
