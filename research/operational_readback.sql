-- READ-ONLY. THE OPERATIONAL READ-BACK AFTER THE OBSERVER / ENTRY / MODEL RELEASE.
--
-- Answers, from production's own rows, whether the connected system is
-- producing observations and decisions -- and, where it is not, the exact
-- named reason for every attempt:
--   O  the non-funded pair observer: the latest cycle's digest, the attempt
--      ledger (every attempted candidate and its refusal), the observations,
--      their labels, and the registry candidates fit and scored on them;
--   E  the entry lane: per-event outcomes of the latest cycle, how each event
--      was mapped, the stale-on-arrival split, and valuations recorded;
--   C  calibration: the scheduled measurement and the cohort shortfall;
--   A  account and authorization: pause state, reconciliation reports, owner
--      authorization records, settlement corrections -- identity fields only.
--
-- No balance, cash, buying power, equity or credential is selected. Absent
-- tables report NULL through to_regclass + query_to_xml.

\echo '== O0 · migrations 142+ applied =='
SELECT version, applied_at FROM schema_migrations
 WHERE version >= '142' ORDER BY version;

\echo '== O1 · the latest cycle: build, state, why, and the observer digest =='
SELECT to_timestamp((value->>'at')::float8)                  AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1)
                                                              AS age_s,
       value->'writer'->>'build'                              AS writer_build,
       value->>'state'                                        AS state,
       value->>'why'                                          AS why,
       value->'pair_observation'->>'ran'                      AS obs_ran,
       value->'pair_observation'->>'ok'                       AS obs_ok,
       value->'pair_observation'->>'refusal'                  AS obs_refusal,
       value->'pair_observation'->>'why'                      AS obs_why,
       value->'pair_observation'->'candidates'                AS obs_candidates,
       value->'pair_observation'->'outcomes'                  AS obs_outcomes,
       value->'pair_observation'->'refusals'                  AS obs_refusals,
       value->'pair_observation'->>'observations_written'     AS obs_written
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== O2 · observer digest: catalogue, labels, generation, evaluation, venue usage =='
SELECT value->'pair_observation'->'catalogue'                AS catalogue,
       value->'pair_observation'->'labels'                   AS labels,
       value->'pair_observation'->'generate'                 AS generate,
       value->'pair_observation'->'evaluate'                 AS evaluate,
       value->'pair_observation'->'venue_usage'              AS venue_usage,
       value->'pair_observation'->'attempt_ledger'           AS attempt_ledger
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== O3 · attempt ledger, last 24 h: by source, outcome and refusal =='
SELECT CASE WHEN to_regclass('bettor_pair_observation_attempts') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select candidate_source || ' ' || outcome || ' ' ||
                   coalesce(refusal, '-') || ' n=' || count(*) ||
                   ' fixtures=' || count(distinct fixture) ||
                   ' written=' || sum(observations_written) as line
              from bettor_pair_observation_attempts
             where attempted_at > now() - interval '24 hours'
             group by candidate_source, outcome, refusal) q
  $q$, false, true, '')))[1]::text END AS attempts_24h;

\echo '== O4 · the 12 most recent attempts, with the refusal at every depth =='
SELECT CASE WHEN to_regclass('bettor_pair_observation_attempts') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by at desc), 'none') as c
      from (select attempted_at as at,
                   attempted_at::text || ' ' || candidate_source || ' ' ||
                   us_market_slug || ' ' || side || ' ' || outcome || ' ' ||
                   coalesce(refusal, '-') || ' quote=' ||
                   coalesce(detail->>'quote_refusal', '-') || ' held=' ||
                   coalesce(detail->>'held_refusal', '-') || ' discovery=' ||
                   coalesce(detail->>'discovery_refusal', '-') ||
                   ' examined=' || coalesce(detail->>'examined', '-') ||
                   ' admitted=' || coalesce(detail->>'admitted', '-') ||
                   ' reads=' || venue_reads::text as line
              from bettor_pair_observation_attempts
             order by attempted_at desc limit 12) q
  $q$, false, true, '')))[1]::text END AS recent_attempts;

\echo '== O5 · observations: by label status and taxonomy; fixtures; span =='
SELECT CASE WHEN to_regclass('bettor_pair_observations') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select label_status || ' ' || coalesce(taxonomy, '-') ||
                   ' n=' || count(*) || ' fixtures=' || count(distinct fixture) ||
                   ' first=' || min(observed_at)::text ||
                   ' last=' || max(observed_at)::text as line
              from bettor_pair_observations group by label_status, taxonomy) q
  $q$, false, true, '')))[1]::text AS observations,
  (xpath('/row/c/text()', query_to_xml($q$
    select count(*) || ' label versions; newest ' ||
           coalesce(max(recorded_at)::text, 'none') as c
      from bettor_pair_observation_labels
  $q$, false, true, '')))[1]::text END AS label_history;

\echo '== O6 · registry: models by key, source, state; evaluation summary =='
SELECT CASE WHEN to_regclass('bettor_funded_models') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select model_key || ' ' ||
                   coalesce(training_provenance ->> 'source', '-') || ' ' ||
                   state || ' ' || model_id ||
                   ' train_events=' || coalesce(training_provenance ->> 'n_events', '-') ||
                   ' prospective_events=' ||
                   coalesce(evaluation -> 'PROSPECTIVE' ->> 'n_events', '-') ||
                   ' evaluated_at=' || coalesce(evaluation ->> 'evaluated_at', '-') ||
                   ' approved_by=' || coalesce(approved_by, '-') as line
              from bettor_funded_models) q
  $q$, false, true, '')))[1]::text END AS models;

\echo '== E1 · entry: the latest cycle per-event outcomes =='
SELECT CASE WHEN to_regclass('ext_candidate_outcomes') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select sport_key || ' ' || outcome || ' first=' ||
                   coalesce(first_refusal, '-') || ' codes=' || codes::text ||
                   ' n=' || count(*) as line
              from ext_candidate_outcomes
             where cycle_id = (select cycle_id from ext_candidate_outcomes
                                order by cycle_at desc, id desc limit 1)
             group by sport_key, outcome, first_refusal, codes) q
  $q$, false, true, '')))[1]::text END AS latest_cycle;

\echo '== E2 · valuations recorded in the last 24 h: admissible or not, and why =='
SELECT count(*)                                      AS valuations_24h,
       count(*) FILTER (WHERE admissible)            AS admissible,
       count(*) FILTER (WHERE NOT admissible)        AS inadmissible,
       count(DISTINCT event_key)                     AS fixtures,
       max(decided_at)                               AS newest
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours';

SELECT 'first:' || coalesce(refusals[1], '(none)') AS refusal, count(*) AS n
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo '== C1 · calibration rows (newest per source version) =='
SELECT CASE WHEN to_regclass('external_source_calibration') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select distinct on (source_version)
                   source_version || ' n=' || coalesce(sample_size::text, '?') ||
                   ' within=' || coalesce(within_tolerance::text, '?') ||
                   ' evaluator=' || coalesce(provenance ->> 'evaluator', '?') ||
                   ' at=' || coalesce(measured_at::text, '?') as line
              from external_source_calibration
             order by source_version, measured_at desc) q
  $q$, false, true, '')))[1]::text END AS calibration;

\echo '== A1 · the account registry: identity and state only =='
SELECT account_id, desk_id, status, paused,
       left(coalesce(pause_reason, ''), 120) AS pause_reason,
       accounting_status, last_verified_at
  FROM bettor_desk_accounts
 ORDER BY account_id;
