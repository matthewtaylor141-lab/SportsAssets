-- P0 INCIDENT (coverage -> trade starvation), FULL FUNNEL RECEIPT, PART A (read-only).
-- Provider / ledger / venue / valuation side, latest 24 h and the current live slate
-- (events starting in the next 36 h plus in-play: game start >= now() - 4 h).
-- Unit notes: the collection ledger (ext_candidate_outcomes) is one row per provider
-- event per cycle; every count below names whether it is ROWS or DISTINCT events /
-- markets. League = provider sport_key on the ledger, venue league token
-- (split_part(us_market_slug, '-', 2)) on valuation-side records.
\echo '== A0 . read instant, schema head, canonical / R30 tables present? =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;
SELECT t AS table_name, to_regclass(t) IS NOT NULL AS present
  FROM unnest(ARRAY['ext_candidate_outcomes','external_valuations','paper_decisions',
                    'paper_orders','paper_order_events','paper_fills','execution_intents',
                    'execmirror_orders','eddie_execution_estimates','karen_challenges',
                    'intel_allocations','paper_xavier_reviews','paper_handoffs',
                    'paper_evaluation_attempts','pinnapi_reactive_attempts',
                    'coverage_funnel_snapshots','coverage_collapse_alerts','lol_ledger',
                    'canonical_decision_intents','canonical_intents','live_parity_ledger',
                    'decision_intents','agent_work_items','paper_sleeve_classifications']) t
 ORDER BY 1;

\echo '== A1 . LEDGER 24 h per provider sport_key: rows, cycles, distinct events, row outcomes =='
WITH r AS (
    SELECT sport_key, family, provider_event_id, cycle_id, outcome
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours'),
cyc AS (SELECT cycle_id, count(*) AS n, count(DISTINCT sport_key) AS sports FROM r GROUP BY 1)
SELECT r.sport_key, max(r.family) AS family, count(*) AS rows,
       count(DISTINCT r.cycle_id) AS cycles,
       count(DISTINCT r.cycle_id) FILTER (WHERE cyc.n = 1) AS one_row_cycles,
       count(DISTINCT r.provider_event_id) AS events,
       count(*) FILTER (WHERE r.provider_event_id IS NULL) AS rows_without_event_id,
       count(*) FILTER (WHERE r.outcome = 'ADMITTED') AS r_admitted,
       count(*) FILTER (WHERE r.outcome = 'REFUSED') AS r_refused,
       count(*) FILTER (WHERE r.outcome = 'ALREADY_RECORDED') AS r_already,
       count(*) FILTER (WHERE r.outcome = 'DEFERRED') AS r_deferred,
       count(*) FILTER (WHERE r.outcome = 'UNCLASSIFIED') AS r_unclassified
  FROM r JOIN cyc USING (cycle_id)
 GROUP BY 1 ORDER BY events DESC;

\echo '== A1b . LEDGER cycle shape 24 h: scheduled multi-event cycles vs one-event (stream-seed) cycles, per hour =='
WITH c AS (
    SELECT cycle_id, min(cycle_at) AS at, count(*) AS n, count(DISTINCT sport_key) AS sports,
           max(writer) AS writer
      FROM ext_candidate_outcomes WHERE cycle_at > now() - interval '24 hours' GROUP BY 1)
SELECT date_trunc('hour', at) AS hour, count(*) FILTER (WHERE n > 1) AS multi_event_cycles,
       count(*) FILTER (WHERE n = 1) AS one_event_cycles, sum(n) AS rows,
       max(n) AS max_rows_in_a_cycle, string_agg(DISTINCT writer, ',') AS writers
  FROM c GROUP BY 1 ORDER BY 1;

\echo '== A2 . LEDGER 24 h per event: the coverage_integrity REACH funnel (its own definitions) vs precise stages =='
WITH r AS (
    SELECT sport_key, family, provider_event_id, cycle_at, outcome, stage, first_refusal,
           us_market_slug, mapped_by,
           CASE WHEN outcome IN ('ADMITTED','ALREADY_RECORDED') THEN 99
                WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                     greatest(substr(stage,1,1)::int, CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
                WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END AS reach
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours' AND provider_event_id IS NOT NULL),
e AS (
    SELECT sport_key, provider_event_id, max(reach) AS reach,
           bool_and(outcome = 'DEFERRED') AS only_deferred,
           bool_or(outcome = 'UNCLASSIFIED') AS any_unclassified,
           bool_and(outcome = 'UNCLASSIFIED' OR outcome = 'DEFERRED') AS only_unclassified_or_deferred,
           bool_or(first_refusal = 'NO_PINNACLE_ON_EVENT') AS ever_no_pinnacle,
           bool_and(coalesce(first_refusal,'') = 'NO_PINNACLE_ON_EVENT' OR outcome = 'DEFERRED') AS never_priced,
           bool_or(us_market_slug IS NOT NULL) AS ever_mapped,
           bool_or(mapped_by = 'VENUE_NATIVE') AS ever_venue_native,
           bool_or(reach = 3 AND NOT coalesce(first_refusal,'') = ANY(ARRAY['VENUE_DOES_NOT_LIST_THIS_FIXTURE',
               'NO_PREMAP_CONTRACT_FOR_THIS_FIXTURE','NO_VENUE_NATIVE_EVENT_FOR_FIXTURE',
               'NO_VENUE_CONTRACT_FOR_EVENT','NO_VENUE_NATIVE_CONTRACT_IN_PREMAP'])) AS found_at_3
      FROM r GROUP BY 1, 2)
SELECT sport_key, count(*) AS provider_events,
       count(*) FILTER (WHERE reach >= 3) AS ci_normalized,
       count(*) FILTER (WHERE reach >= 4 OR found_at_3) AS ci_venue_discovered,
       count(*) FILTER (WHERE reach >= 4) AS ci_mapped,
       count(*) FILTER (WHERE reach >= 5) AS ci_settlement_supported,
       count(*) FILTER (WHERE only_deferred) AS only_deferred,
       count(*) FILTER (WHERE only_unclassified_or_deferred AND NOT only_deferred) AS only_unclassified,
       count(*) FILTER (WHERE never_priced AND NOT only_deferred) AS never_priced_no_pinnacle,
       count(*) FILTER (WHERE NOT never_priced) AS priced_at_least_once,
       count(*) FILTER (WHERE ever_mapped) AS mapped_ever,
       count(*) FILTER (WHERE ever_venue_native) AS mapped_venue_native
  FROM e GROUP BY 1 ORDER BY 2 DESC;

\echo '== A3 . LEDGER 24 h: each event at its FURTHEST row -> (stage, outcome, first_refusal), distinct events =='
WITH r AS (
    SELECT sport_key, provider_event_id, cycle_at, outcome, coalesce(stage,'-') AS stage,
           coalesce(first_refusal,'-') AS first_refusal,
           CASE WHEN outcome IN ('ADMITTED','ALREADY_RECORDED') THEN 99
                WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                     greatest(substr(stage,1,1)::int, CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
                WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END AS reach
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours' AND provider_event_id IS NOT NULL),
b AS (
    SELECT DISTINCT ON (sport_key, provider_event_id) sport_key, provider_event_id, reach, stage,
           outcome, first_refusal
      FROM r ORDER BY sport_key, provider_event_id, reach DESC, cycle_at DESC)
SELECT sport_key, reach, stage, outcome, first_refusal, count(*) AS events
  FROM b GROUP BY 1,2,3,4,5 ORDER BY 1, 6 DESC;

\echo '== A4 . LEDGER 24 h: each event at its LATEST row (current state), distinct events =='
WITH b AS (
    SELECT DISTINCT ON (sport_key, provider_event_id) sport_key, provider_event_id,
           coalesce(stage,'-') AS stage, outcome, coalesce(first_refusal,'-') AS first_refusal
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours' AND provider_event_id IS NOT NULL
     ORDER BY sport_key, provider_event_id, cycle_at DESC, id DESC)
SELECT sport_key, stage, outcome, first_refusal, count(*) AS events
  FROM b GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC;

\echo '== A5 . LEDGER 24 h: every code attributed (codes jsonb), rows and distinct events =='
SELECT sport_key, c AS code, count(*) AS rows, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes, jsonb_array_elements_text(codes) c
 WHERE cycle_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 4 DESC;

\echo '== A6 . LEDGER events 24 h -> valuations (external_valuations.event_key) by purpose =='
WITH e AS (
    SELECT DISTINCT sport_key, provider_event_id FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '24 hours' AND provider_event_id IS NOT NULL),
v AS (
    SELECT event_key, record_purpose, us_market_slug, probability, admissible
      FROM external_valuations WHERE decided_at > now() - interval '24 hours')
SELECT e.sport_key, count(DISTINCT e.provider_event_id) AS events,
       count(DISTINCT e.provider_event_id) FILTER (WHERE v.event_key IS NOT NULL) AS events_valued,
       count(DISTINCT e.provider_event_id) FILTER (WHERE v.record_purpose = 'ENTRY_DECISION') AS events_entry_decision,
       count(DISTINCT e.provider_event_id) FILTER (WHERE v.record_purpose = 'CALIBRATION_ONLY') AS events_calibration_only,
       count(DISTINCT e.provider_event_id) FILTER (WHERE v.probability IS NOT NULL) AS events_with_probability,
       count(DISTINCT v.us_market_slug) AS venue_markets_valued
  FROM e LEFT JOIN v ON v.event_key = e.provider_event_id
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== A7 . VALUATIONS 24 h by venue league token / family / purpose: rows, markets, events, probability, admissible =='
SELECT split_part(coalesce(us_market_slug,'-'), '-', 2) AS league_token, sport_family, record_purpose,
       count(*) AS rows, count(DISTINCT us_market_slug) AS markets, count(DISTINCT event_key) AS events,
       count(*) FILTER (WHERE probability IS NOT NULL) AS rows_with_probability,
       count(DISTINCT us_market_slug) FILTER (WHERE probability IS NOT NULL) AS markets_with_probability,
       count(*) FILTER (WHERE admissible) AS admissible_rows,
       count(*) FILTER (WHERE coalesce(estimated_edge_per_contract, 0) > 0) AS rows_edge_pos,
       count(DISTINCT us_market_slug) FILTER (WHERE estimated_edge_per_contract > 0) AS markets_edge_pos,
       min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM external_valuations WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 4 DESC;

\echo '== A8 . VALUATIONS 24 h: settlement comparison by league (one row per valuation; markets distinct) =='
SELECT split_part(coalesce(us_market_slug,'-'), '-', 2) AS league_token,
       coalesce(settlement_comparison->>'compatibility', 'NOT_RECORDED') AS compatibility,
       coalesce(settlement_comparison->>'overall_established', 'NULL') AS overall_established,
       (settlement_comparison->>'venue_rules_read') AS rules_read,
       count(*) AS rows, count(DISTINCT us_market_slug) AS markets
  FROM external_valuations WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC;

\echo '== A9 . VALUATIONS 24 h: every refusal code by league (rows, distinct markets) =='
SELECT split_part(coalesce(us_market_slug,'-'), '-', 2) AS league_token, r AS refusal,
       count(*) AS rows, count(DISTINCT us_market_slug) AS markets
  FROM external_valuations, unnest(refusals) r
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 4 DESC, 3 DESC;

\echo '== A9b . VALUATIONS 24 h: settlement blockers / unmet rules by league =='
SELECT split_part(coalesce(us_market_slug,'-'), '-', 2) AS league_token, b AS blocker,
       count(*) AS rows, count(DISTINCT us_market_slug) AS markets
  FROM external_valuations,
       jsonb_array_elements_text(CASE WHEN jsonb_typeof(settlement_comparison->'blockers') = 'array'
                                      THEN settlement_comparison->'blockers' ELSE '[]'::jsonb END) b
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 1, 4 DESC;

\echo '== A10 . LIVE SLATE venue catalogue (us_premap): events starting now-4h .. now+36h by league token / sports_type =='
SELECT lower(split_part(coalesce(event_slug,''), '-', 1)) AS event_token,
       split_part(coalesce(market_slug,'-'), '-', 2) AS market_token, sports_type,
       count(DISTINCT event_slug) AS events, count(DISTINCT market_slug) AS markets,
       count(DISTINCT event_slug) FILTER (WHERE game_start <= now()) AS events_started,
       min(game_start) AS first_start, max(game_start) AS last_start
  FROM us_premap
 WHERE game_start >= now() - interval '4 hours' AND game_start < now() + interval '36 hours'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 120;

\echo '== A11 . LIVE SLATE provider side: ledger events (last 3 h of cycles) whose start is now-4h .. now+36h, latest row =='
WITH r AS (
    SELECT DISTINCT ON (sport_key, provider_event_id) sport_key, provider_event_id, commence_time,
           coalesce(stage,'-') AS stage, outcome, coalesce(first_refusal,'-') AS first_refusal, us_market_slug
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '3 hours' AND provider_event_id IS NOT NULL
     ORDER BY sport_key, provider_event_id, cycle_at DESC, id DESC),
s AS (SELECT r.*, CASE WHEN commence_time ~ '^\d{4}-\d{2}-\d{2}T' THEN commence_time::timestamptz END AS ct FROM r),
s2 AS (SELECT * FROM s WHERE ct >= now() - interval '4 hours' AND ct < now() + interval '36 hours')
SELECT sport_key, count(*) AS slate_events,
       count(*) FILTER (WHERE ct <= now()) AS in_play_or_started,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS mapped_now,
       count(*) FILTER (WHERE outcome = 'DEFERRED') AS deferred_now,
       count(*) FILTER (WHERE first_refusal = 'NO_PINNACLE_ON_EVENT') AS no_pinnacle_now
  FROM s2 GROUP BY 1 ORDER BY 2 DESC;
WITH r AS (
    SELECT DISTINCT ON (sport_key, provider_event_id) sport_key, provider_event_id, commence_time,
           coalesce(stage,'-') AS stage, outcome, coalesce(first_refusal,'-') AS first_refusal
      FROM ext_candidate_outcomes
     WHERE cycle_at > now() - interval '3 hours' AND provider_event_id IS NOT NULL
     ORDER BY sport_key, provider_event_id, cycle_at DESC, id DESC),
s AS (SELECT r.*, CASE WHEN commence_time ~ '^\d{4}-\d{2}-\d{2}T' THEN commence_time::timestamptz END AS ct FROM r)
SELECT sport_key, stage, outcome, first_refusal, count(*) AS slate_events
  FROM s WHERE ct >= now() - interval '4 hours' AND ct < now() + interval '36 hours'
 GROUP BY 1,2,3,4 ORDER BY 1, 5 DESC;

\echo '== A12 . LIVE SLATE valuation side: venue markets starting now-4h .. now+36h valued in the last 3 h =='
WITH m AS (
    SELECT market_slug, min(game_start) AS gs, max(split_part(market_slug,'-',2)) AS tok
      FROM us_premap
     WHERE game_start >= now() - interval '4 hours' AND game_start < now() + interval '36 hours'
     GROUP BY 1),
v AS (
    SELECT us_market_slug, record_purpose, probability, admissible, refusals
      FROM external_valuations WHERE decided_at > now() - interval '3 hours')
SELECT m.tok AS league_token, count(DISTINCT m.market_slug) AS venue_markets,
       count(DISTINCT m.market_slug) FILTER (WHERE v.us_market_slug IS NOT NULL) AS markets_valued_3h,
       count(DISTINCT m.market_slug) FILTER (WHERE v.probability IS NOT NULL) AS markets_with_probability_3h,
       count(DISTINCT m.market_slug) FILTER (WHERE v.record_purpose = 'ENTRY_DECISION') AS markets_entry_decision_3h
  FROM m LEFT JOIN v ON v.us_market_slug = m.market_slug
 GROUP BY 1 ORDER BY 2 DESC LIMIT 60;

\echo '== A13 . COLLECTOR heartbeat: selection, budget, dropped, rejected; funnel_by_provider_sport =='
SELECT to_timestamp((value->>'at')::float8) AS at,
       round(extract(epoch FROM now()) - (value->>'at')::float8) AS age_s,
       value->>'state' AS state, value->>'evaluated' AS evaluated, value->>'written' AS written,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS budget,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       left((value->'sports_selection'->'rejected')::text, 1500) AS rejected,
       left((value->'candidate_outcomes')::text, 1500) AS candidate_outcomes
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT k AS provider_sport, left(v::text, 900) AS funnel_step
  FROM ingestion_state, jsonb_each(coalesce(value->'funnel_by_provider_sport', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle' ORDER BY 1;
SELECT k AS refusal, v AS n
  FROM ingestion_state, jsonb_each_text(coalesce(value->'refusals', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle' ORDER BY 2 DESC LIMIT 60;

\echo '== A14 . PINNAPI feed heartbeat + coverage census (contracts per sport|family|phase|state) =='
SELECT (SELECT value->'sport_ids' FROM ingestion_state WHERE key = 'pinnapi_feed_scope') AS scope_sport_ids,
       value->>'state' AS feed_state,
       to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'coverage_census'->'total_contracts' AS total_contracts,
       value->'coverage_census'->'states' AS census_states,
       value->'coverage_census'->'events_by_state' AS events_by_state,
       value->'coverage_census'->'matched_events' AS matched_events,
       value->'coverage_census'->'unsupported_reasons' AS unsupported_reasons
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT k AS sport_family_phase_state, v AS contracts
  FROM ingestion_state,
       jsonb_each_text(coalesce(value->'coverage_census'->'by_sport_family_phase_state', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'pinnapi_feed_last' ORDER BY 1;
SELECT key, length(value::text) AS bytes, left(value::text, 300) AS head
  FROM ingestion_state WHERE key ILIKE 'pinnapi%' ORDER BY 1;

\echo '== A15 . EXISTING RECEIPT: coverage_funnel_snapshots (ET, today + yesterday) and alerts (2 days) =='
SELECT day, league, provider_events, normalized_events, venue_discovered, mapped_events,
       settlement_supported, evaluated_events, decided_events, entered_events, refused_events,
       ordered_events, filled_events, actual_intents, venue_catalogue_events,
       left(unavailable::text, 200) AS unavailable, final, computed_at
  FROM coverage_funnel_snapshots
 WHERE tz = 'America/New_York' AND day >= (now() AT TIME ZONE 'America/New_York')::date - 1
 ORDER BY day DESC, provider_events DESC NULLS LAST;
SELECT day, league, kind, stage_from, stage_to, severity, audrey_finding_id IS NOT NULL AS has_finding,
       audrey_refusal, left(detail->>'statement', 220) AS statement, detected_at
  FROM coverage_collapse_alerts WHERE day >= current_date - 2 ORDER BY detected_at DESC LIMIT 40;
