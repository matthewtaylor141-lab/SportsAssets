-- P0 INCIDENT (coverage -> trade starvation), segment MARKET-FAMILY MAPPING +
-- SETTLEMENT COMPATIBILITY + PROBABILITY / FAIR VALUE + FRESHNESS. READ-ONLY.
--
-- F1  venue catalogue by market family (sports_type) x league token, games
--     starting in [now-6h, now+48h]: what the venue LISTS per family.
-- F2  the PinnAPI census as last published in the feed heartbeat (sport id x
--     family x phase x state) and its unsupported-family reasons.
-- F3  collector candidates (ext_candidate_outcomes) last 24 h by provider sport
--     x stage x first refusal: events and rows.
-- F4  external_valuations last 24 h by sport family x league x market x period
--     x provider x purpose.
-- F5  every refusal code on those rows, by sport family x league.
-- F6  settlement blockers recorded on those rows (settlement_comparison).
-- F7  paper decisions last 24 h by strategy x league x verdict x first refusal.
-- F8  completed-game match: failing checks by league (last 24 h).
-- F9  freshness: provider quote time vs our receipt vs decision, by provider x
--     family; rows with a missing clock; freshness-stage refusals.
\echo '== F0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== F1a · venue catalogue: sports_type x league token, games in [now-6h, now+48h] =='
SELECT coalesce(sports_type, '<null>') AS sports_type,
       lower(split_part(coalesce(market_slug, ''), '-', 2)) AS league_token,
       split_part(coalesce(market_slug, ''), '-', 1) AS slug_prefix,
       count(*) AS rows, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) AS events,
       count(*) FILTER (WHERE line IS NOT NULL AND line::text <> '') AS with_line
  FROM us_premap
 WHERE game_start > now() - interval '6 hours'
   AND game_start < now() + interval '48 hours'
 GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 120;

\echo '== F1b · venue catalogue family classes (pinnapi_census.family_of in SQL), [now-6h, now+48h] =='
SELECT fam_sport, family_class, count(DISTINCT market_slug) AS contracts,
       count(DISTINCT event_slug) AS events
  FROM (SELECT market_slug, event_slug,
               split_part(lower(coalesce(sports_type, '')), '_', 1) AS fam_sport,
               CASE
                 WHEN coalesce(sports_type, '') = '' THEN 'NO_SPORTS_TYPE'
                 WHEN sports_type LIKE '%\_player\_%' THEN 'PLAYER_PROP'
                 WHEN sports_type ~ '(first_five|inning|half|quarter|_period|_set)' THEN 'PERIOD'
                 WHEN sports_type ~ '(spread|handicap)' THEN 'SPREAD'
                 WHEN sports_type ~ 'team_total' THEN 'TEAM_TOTAL'
                 WHEN sports_type ~ 'total' THEN 'TOTAL'
                 WHEN sports_type ~ '(full_game_winner|full_time_winner|match_winner|_winner)$' THEN 'WINNER'
                 ELSE 'OTHER:' || sports_type
               END AS family_class
          FROM us_premap
         WHERE game_start > now() - interval '6 hours'
           AND game_start < now() + interval '48 hours') t
 GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 150;

\echo '== F2a · PinnAPI feed scope and heartbeat freshness =='
SELECT key, left(value::text, 300) AS value_head
  FROM ingestion_state WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope');
SELECT value->>'beat_at' AS heartbeat_beat_at, value->>'state' AS feed_state,
       value->>'heartbeat_truncated' AS truncated,
       value->'coverage_census'->'total_contracts' AS census_total,
       value->'coverage_census'->'states' AS census_states,
       value->'coverage_census'->'unsupported_reasons' AS unsupported_reasons,
       value->'coverage_census'->'events_by_state' AS events_by_state
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== F2b · PinnAPI census: sport id | family | phase | state =='
SELECT k AS sid_family_phase_state, (v::text)::numeric AS contracts
  FROM ingestion_state s,
       LATERAL jsonb_each(CASE WHEN jsonb_typeof(s.value->'coverage_census'->'by_sport_family_phase_state') = 'object'
                               THEN s.value->'coverage_census'->'by_sport_family_phase_state' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE s.key = 'pinnapi_feed_last'
 ORDER BY 2 DESC LIMIT 80;

\echo '== F2c · PinnAPI cache: markets by sport id | market type | stream =='
SELECT k AS sport_type_stream, (v::text)::numeric AS markets
  FROM ingestion_state s,
       LATERAL jsonb_each(CASE WHEN jsonb_typeof(s.value->'cache'->'markets_by_sport_type_phase') = 'object'
                               THEN s.value->'cache'->'markets_by_sport_type_phase' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE s.key = 'pinnapi_feed_last'
 ORDER BY 2 DESC LIMIT 80;

\echo '== F3 · collector candidates last 24 h: provider sport x stage x first refusal =='
SELECT sport_key, coalesce(stage, '<none>') AS stage, outcome,
       coalesce(first_refusal, '-') AS first_refusal,
       count(DISTINCT provider_event_id) AS events, count(*) AS rows
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 5 DESC LIMIT 200;

\echo '== F4 · valuations last 24 h: family x league x market x period x provider x purpose =='
SELECT sport_family, lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS league,
       market, coalesce(period, '<null>') AS period, provider, record_purpose,
       count(*) AS rows, count(DISTINCT us_market_slug) AS contracts,
       count(DISTINCT event_key) AS events,
       count(*) FILTER (WHERE probability IS NOT NULL) AS with_probability,
       count(*) FILTER (WHERE admissible) AS admissible,
       count(*) FILTER (WHERE outcomes_priced = expected_outcomes) AS complete_set,
       min(expected_outcomes) AS exp_min, max(expected_outcomes) AS exp_max
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 7 DESC LIMIT 80;

\echo '== F5 · refusal codes on valuations last 24 h, by family x league =='
SELECT v.sport_family, lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league,
       split_part(r.code, ':', 1) AS code,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v, LATERAL unnest(v.refusals) AS r(code)
 WHERE v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC LIMIT 250;

\echo '== F6a · settlement comparison verdicts last 24 h, by family x league =='
SELECT v.sport_family, lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league,
       coalesce(v.settlement_comparison->>'compatibility', '<null>') AS compatibility,
       coalesce(v.settlement_comparison->>'overall_established', '<null>') AS overall_established,
       (v.settlement_comparison ? 'venue_rules_text') AS has_venue_text,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v
 WHERE v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 6 DESC LIMIT 80;

\echo '== F6b · settlement blockers last 24 h, by family x league =='
SELECT v.sport_family, lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league,
       b.x AS blocker, count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v,
       LATERAL jsonb_array_elements_text(
         CASE WHEN jsonb_typeof(v.settlement_comparison->'blockers') = 'array'
              THEN v.settlement_comparison->'blockers' ELSE '[]'::jsonb END) AS b(x)
 WHERE v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC LIMIT 200;

\echo '== F7a · paper decisions last 24 h: strategy x family x league x verdict x first refusal =='
SELECT d.strategy, v.sport_family, lower(split_part(coalesce(d.us_market_slug, ''), '-', 2)) AS league,
       d.verdict, coalesce(d.refusal, 'ENTER') AS first_refusal,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 6 DESC LIMIT 200;

\echo '== F7b · every refusal on paper decisions last 24 h: strategy x family x league x code =='
SELECT d.strategy, v.sport_family, lower(split_part(coalesce(d.us_market_slug, ''), '-', 2)) AS league,
       split_part(r.code, ':', 1) AS code,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id,
       LATERAL unnest(d.refusals) AS r(code)
 WHERE d.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 5 DESC LIMIT 300;

\echo '== F8 · completed-game match checks that FAILED last 24 h, by family x league =='
SELECT d.strategy, v.sport_family, lower(split_part(coalesce(d.us_market_slug, ''), '-', 2)) AS league,
       x->>'check' AS failing_check, coalesce(x->>'refusal', '<null>') AS refusal,
       count(*) AS decisions, count(DISTINCT d.us_market_slug) AS markets
  FROM paper_decisions d LEFT JOIN external_valuations v ON v.id = d.valuation_id,
       LATERAL jsonb_array_elements(
         CASE WHEN jsonb_typeof(d.pinnacle->'contract_match'->'checks') = 'array'
              THEN d.pinnacle->'contract_match'->'checks' ELSE '[]'::jsonb END) AS c(x)
 WHERE d.decided_at > now() - interval '24 hours'
   AND (x->>'passed')::boolean IS NOT TRUE
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 3, 6 DESC LIMIT 200;

\echo '== F9a · freshness clocks last 24 h by provider x family =='
SELECT provider, sport_family, count(*) AS rows,
       count(*) FILTER (WHERE observed_at IS NULL) AS no_provider_time,
       count(*) FILTER (WHERE received_at IS NULL) AS no_receipt_time,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - observed_at))::numeric, 2) AS p50_receipt_minus_source_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - observed_at))::numeric, 2) AS p90_receipt_minus_source_s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM decided_at - observed_at))::numeric, 2) AS p50_decided_minus_source_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY extract(epoch FROM decided_at - observed_at))::numeric, 2) AS p90_decided_minus_source_s,
       count(*) FILTER (WHERE extract(epoch FROM decided_at - observed_at) > 30) AS decided_over_30s,
       count(*) FILTER (WHERE extract(epoch FROM received_at - observed_at) > 30) AS received_over_30s,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY age_s)::numeric, 2) AS p50_age_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY age_s)::numeric, 2) AS p90_age_s,
       count(*) FILTER (WHERE age_s IS NULL) AS age_null
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo '== F9b · freshness-stage refusals last 24 h by provider x family =='
SELECT v.provider, v.sport_family, split_part(r.code, ':', 1) AS code,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v, LATERAL unnest(v.refusals) AS r(code)
 WHERE v.decided_at > now() - interval '24 hours'
   AND split_part(r.code, ':', 1) IN ('QUOTE_STALE', 'QUOTE_STALE_ON_ARRIVAL',
       'QUOTE_HAS_NO_TIMESTAMP', 'ONE_CLOCK_IS_NOT_MEASURED', 'VENUE_BOOK_STALE',
       'VENUE_QUOTE_STALE', 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED',
       'VENUE_BOOK_CURRENCY_CONTRADICTED_BY_CONTRACT',
       'OUR_OWN_PROCESSING_DELAY_EXCEEDED_BEFORE_THE_DECISION',
       'NO_CONTEMPORANEOUS_VENUE_QUOTE')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 60;

\echo '== F9c · PinnAPI primary selection: provider used and fallback reason, last 24 h, by family x league =='
SELECT v.sport_family, lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league, v.provider,
       coalesce(v.settlement_comparison->'reference_input'->>'fallback_reason', '-') AS pinnapi_fallback_reason,
       coalesce(v.settlement_comparison->'reference_input'->'decision_check'->>'reason', '-') AS pinnapi_recheck_reason,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v
 WHERE v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 6 DESC LIMIT 80;

\echo '== F10 · 7-day shape: valuations and CG decisions per family x league per day =='
WITH cg AS (
    SELECT valuation_id, count(*) AS n, count(*) FILTER (WHERE verdict = 'ENTER') AS n_enter
      FROM paper_decisions
     WHERE strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
       AND decided_at > now() - interval '8 days'
     GROUP BY 1)
SELECT date_trunc('day', v.decided_at)::date AS day, v.sport_family,
       lower(split_part(coalesce(v.us_market_slug, ''), '-', 2)) AS league,
       count(*) AS valuations, count(DISTINCT v.us_market_slug) AS contracts,
       count(*) FILTER (WHERE v.probability IS NOT NULL) AS with_probability,
       coalesce(sum(cg.n), 0) AS cg_decisions, coalesce(sum(cg.n_enter), 0) AS cg_enter
  FROM external_valuations v LEFT JOIN cg ON cg.valuation_id = v.id
 WHERE v.decided_at > now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC LIMIT 120;
