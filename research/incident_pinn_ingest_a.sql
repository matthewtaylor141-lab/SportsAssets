-- P0 INCIDENT (2026-10-04) segment PINNAPI COVERAGE + RAW INGESTION + FRESHNESS.
-- Read-only. Heartbeats in ingestion_state only (overwritten rows, small).
-- Never reads or prints any key. Bounded: single-row reads + jsonb expansion.
\echo '== A0 read instant =='
SELECT now() AS read_at;

\echo '== A1 PinnAPI arm + scope rows, and every pinnapi/ext_pinnacle heartbeat key with its size =='
SELECT key, octet_length(value::text) AS bytes,
       CASE WHEN key IN ('pinnapi_feed', 'pinnapi_feed_scope') THEN value::text END AS value
  FROM ingestion_state
 WHERE key LIKE 'pinnapi%' OR key LIKE 'ext_pinnacle%'
 ORDER BY key;

\echo '== A2 feed owner heartbeat: state, scope, authority, beat age =='
SELECT value->>'state' AS state, value->>'refused' AS refused,
       value->'sport_ids' AS sport_ids, value->'streams' AS streams,
       value->>'enabled_env' AS enabled_env,
       round((extract(epoch FROM now()) - (value->>'beat_at')::float8)::numeric, 1) AS beat_age_s,
       value->'cache'->'authority' AS authority,
       value->>'recent_unrequested_closes' AS unrequested_closes,
       value->>'heartbeat_truncated' AS truncated
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== A3 feed cache: events, markets, markets with NO observed change time, counters, latency rings =='
SELECT value->'cache'->>'parser' AS parser,
       (value->'cache'->>'events')::int AS events,
       (value->'cache'->>'markets')::int AS markets,
       (value->'cache'->>'markets_age_unknown')::int AS markets_age_unknown,
       round(100.0 * (value->'cache'->>'markets_age_unknown')::numeric
             / nullif((value->'cache'->>'markets')::numeric, 0), 1) AS pct_age_unknown,
       value->'cache'->'counts' AS counts,
       value->'cache'->'provider_stamp_to_receipt_ms' AS provider_stamp_to_receipt_ms,
       value->'cache'->'receipt_to_evaluation_ms' AS receipt_to_evaluation_ms,
       value->'cache'->'bounds' AS bounds
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== A4 feed cache markets by sport_id | market_type | stream (the provider-side current state) =='
SELECT split_part(e.k, '|', 1) AS sport_id, split_part(e.k, '|', 2) AS market_type,
       split_part(e.k, '|', 3) AS stream, e.v::text::int AS markets
  FROM ingestion_state,
       jsonb_each(CASE WHEN jsonb_typeof(value->'cache'->'markets_by_sport_type_phase') = 'object'
                       THEN value->'cache'->'markets_by_sport_type_phase' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE key = 'pinnapi_feed_last'
 ORDER BY 1, 2, 3;

\echo '== A5 owner transitions (last 10) =='
SELECT t->>'what' AS what, to_timestamp((t->>'at')::float8) AS at, t->>'epoch' AS epoch,
       t->>'error' AS error, t->>'recent' AS recent
  FROM ingestion_state,
       jsonb_array_elements(CASE WHEN jsonb_typeof(value->'transitions') = 'array'
                                 THEN value->'transitions' ELSE '[]'::jsonb END) AS t
 WHERE key = 'pinnapi_feed_last';

\echo '== A6 coverage census (venue catalogue vs the feed): totals, states, reasons =='
SELECT value->'coverage_census'->>'total_contracts' AS total_contracts,
       value->'coverage_census'->>'subscribed_rows' AS subscribed_rows,
       value->'coverage_census'->>'matched_events' AS matched_events,
       value->'coverage_census'->>'truncated_at' AS truncated_at,
       value->'coverage_census'->>'reconciled' AS reconciled,
       to_timestamp((value->'coverage_census'->>'computed_at')::float8) AS computed_at,
       value->'coverage_census'->'states' AS states,
       value->'coverage_census'->'events_by_state' AS events_by_state,
       value->'coverage_census'->'unsupported_reasons' AS unsupported_reasons,
       value->'coverage_census'->>'skipped' AS skipped,
       value->'coverage_census'->>'error' AS error
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== A7 coverage census by sport_id | family | phase | state =='
SELECT split_part(e.k, '|', 1) AS sport_id, split_part(e.k, '|', 2) AS family,
       split_part(e.k, '|', 3) AS phase, split_part(e.k, '|', 4) AS state,
       e.v::text::int AS contracts
  FROM ingestion_state,
       jsonb_each(CASE WHEN jsonb_typeof(value->'coverage_census'->'by_sport_family_phase_state') = 'object'
                       THEN value->'coverage_census'->'by_sport_family_phase_state' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE key = 'pinnapi_feed_last'
 ORDER BY 5 DESC
 LIMIT 120;

\echo '== A8 coverage census samples (unmatched venue events, feed events) =='
SELECT jsonb_pretty(jsonb_build_object(
         'unmatched_event_sample', value->'coverage_census'->'unmatched_event_sample',
         'feed_event_sample', value->'coverage_census'->'feed_event_sample',
         'held_priority_targets', value->'held_priority_targets'))
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== A9 collector heartbeat: state, when, counts, credits, competition selection =='
SELECT to_timestamp((value->>'at')::float8) AS at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 0) AS age_s,
       value->>'state' AS state, value->>'evaluated' AS evaluated,
       value->>'written' AS written, value->>'markets_considered' AS markets_considered,
       value->>'elapsed_s' AS elapsed_s, value->'credits' AS credits,
       value->>'cycle_label' AS cycle_label, value->>'why' AS why
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT jsonb_pretty(value->'sports_selection') AS sports_selection
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== A10 collector funnel per provider sport (last cycle) =='
SELECT e.k AS provider_sport, e.v->>'family' AS family,
       e.v->>'venue_markets_open_and_fresh' AS venue_mkts,
       e.v->>'provider_events' AS provider_events,
       e.v->>'with_pinnacle_h2h' AS with_pinnacle_h2h,
       e.v->>'mapped_to_a_venue_contract' AS mapped,
       e.v->>'mapped_by_venue_native' AS by_native,
       e.v->>'identity_resolved' AS identity,
       e.v->>'evaluated' AS evaluated, e.v->>'written' AS written,
       left((e.v->'refusals')::text, 700) AS refusals,
       left((e.v->'mapping_confirmation')::text, 300) AS mapping_confirmation
  FROM ingestion_state,
       jsonb_each(CASE WHEN jsonb_typeof(value->'funnel_by_provider_sport') = 'object'
                       THEN value->'funnel_by_provider_sport' ELSE '{}'::jsonb END) AS e(k, v)
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== A11 collector refusals tally + freshness digest + step timing + candidate outcome summary =='
SELECT left((value->'refusals')::text, 2500) AS refusals FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
SELECT jsonb_pretty(jsonb_build_object(
         'odds_freshness', value->'odds_freshness',
         'step_timing_s', value->'step_timing_s',
         'candidate_outcomes', value->'candidate_outcomes',
         'venue_universe_by_label', value->'venue_universe_by_label'))
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
