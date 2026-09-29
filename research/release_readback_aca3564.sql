-- READ-ONLY. RELEASE READ-BACK FOR THE API DEPLOY OF aca3564 (bc4bc21 plus one test-only commit).
--
-- WHAT IT ESTABLISHES, FROM PRODUCTION'S OWN ROWS:
--   1  which migrations the production runner has applied (131-141 ship with
--      this release) and whether any file's content drifted after applying;
--   2  the last scheduled cycle: when, which build wrote it, its label, and
--      whether its per-event rows reconciled to the funnel;
--   3  the per-event outcome table (migration 137);
--   4  the funded book, counted, and states within it -- nothing may have been
--      submitted by this release;
--   5  the account registry's state, and which activation records exist;
--   6  the odds-source calibration the entry path requires;
--   7  what 138-141 added: model states and provenance kinds, lost-ack
--      investigations and resolution attempts, non-funded pair observations
--      and their label states, and funded settlement re-reads.
--
-- WHAT IT DOES NOT READ: no balance, cash, buying power or equity, and no
-- credential. Tables that may be absent are counted through to_regclass +
-- query_to_xml so a missing table reports NULL instead of aborting.

\echo '== 1 · applied migrations from 128 up =='
SELECT version, applied_at, left(coalesce(content_sha, ''), 12) AS content_sha_12
  FROM schema_migrations
 WHERE version >= '128'
 ORDER BY version;

\echo '== 2 · the last scheduled cycle, by field =='
SELECT to_timestamp((value->>'at')::float8)                 AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1)
                                                             AS age_s,
       value->'writer'->>'build'                             AS writer_build,
       value->'writer'->>'source_sha256_12'                  AS writer_source_12,
       value->>'state'                                       AS state,
       value->>'evaluated'                                   AS evaluated,
       value->>'written'                                     AS written,
       value->>'cycle_label'                                 AS cycle_label,
       left(coalesce((value->'candidate_outcomes')::text, 'null'), 1500)
                                                             AS candidate_outcomes,
       left(coalesce((value->'funnel_by_provider_sport')::text, 'null'), 1500)
                                                             AS funnel,
       left(coalesce((value->'funded_servicing')::text, 'null'), 800)
                                                             AS funded_servicing
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== 2b · the entry-loop control row =='
SELECT key, left(value::text, 400) AS value
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_shadow';

\echo '== 3 · per-event outcome rows (NULL = table absent) =='
SELECT to_regclass('ext_candidate_outcomes') IS NOT NULL AS table_present,
       CASE WHEN to_regclass('ext_candidate_outcomes') IS NOT NULL THEN
         (xpath('/row/c/text()', query_to_xml(
           'select count(*) as c from ext_candidate_outcomes',
           false, true, '')))[1]::text::bigint END AS total_rows,
       CASE WHEN to_regclass('ext_candidate_outcomes') IS NOT NULL THEN
         (xpath('/row/c/text()', query_to_xml(
           'select count(distinct cycle_id) as c from ext_candidate_outcomes',
           false, true, '')))[1]::text::bigint END AS cycles;

\echo '== 3b · the last five cycles in that table =='
SELECT CASE WHEN to_regclass('ext_candidate_outcomes') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by cycle_at desc), 'none') as c
      from (select cycle_at,
                   cycle_id || ' ' || cycle_at::text || ' writer=' ||
                   coalesce(max(writer), '?') || ' rows=' || count(*) ||
                   ' ' || string_agg(distinct outcome, ',') as line
              from ext_candidate_outcomes
             group by cycle_id, cycle_at
             order by cycle_at desc limit 5) q
  $q$, false, true, '')))[1]::text END AS last_cycles;

\echo '== 3c · the most recent cycle: outcome x first refusal x sport =='
SELECT CASE WHEN to_regclass('ext_candidate_outcomes') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select sport_key || ' ' || outcome || ' ' ||
                   coalesce(first_refusal, '-') || ' ' || count(*) as line
              from ext_candidate_outcomes
             where cycle_id = (select cycle_id from ext_candidate_outcomes
                                order by cycle_at desc, id desc limit 1)
             group by sport_key, outcome, first_refusal) q
  $q$, false, true, '')))[1]::text END AS latest_cycle;

\echo '== 4 · the funded book, counted (NULL = table absent) =='
SELECT t AS table_name,
       CASE WHEN to_regclass(t) IS NOT NULL THEN
         (xpath('/row/c/text()',
                query_to_xml(format('select count(*) as c from %I', t),
                             false, true, '')))[1]::text::bigint
       END AS row_count
  FROM unnest(ARRAY[
        'bettor_funded_intents', 'bettor_funded_fills',
        'bettor_funded_economics', 'bettor_funded_portfolio_groups',
        'bettor_funded_leg_reservations', 'bettor_funded_operation_evidence',
        'bettor_funded_decisions', 'bettor_funded_decision_outcomes',
        'bettor_funded_group_results', 'bettor_funded_models',
        'bettor_funded_investigations', 'bettor_funded_resolution_audit',
        'bettor_pair_observations', 'bettor_pair_observation_labels',
        'bettor_funded_settlement_rechecks']) AS t;

\echo '== 4b · intent states (NULL = table absent) =='
SELECT CASE WHEN to_regclass('bettor_funded_intents') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml(
    'select coalesce(string_agg(s || '':'' || n, '', '' order by s), ''none'') as c '
    'from (select state::text as s, count(*) as n from bettor_funded_intents group by state) q',
    false, true, '')))[1]::text END AS intent_states;

\echo '== 5 · the account registry: identity and state only =='
SELECT account_id, status, paused,
       left(coalesce(pause_reason, ''), 120) AS pause_reason,
       accounting_status, last_verified_at
  FROM bettor_desk_accounts
 ORDER BY account_id;

\echo '== 5b · which activation records exist (presence and size only) =='
SELECT k AS key,
       (SELECT length(value::text) FROM ingestion_state WHERE key = k) AS bytes
  FROM unnest(ARRAY['bettor_funded_account_binding',
                    'bettor_funded_limits_approved',
                    'bettor_funded_authorization']) AS k;

\echo '== 6 · the odds source calibration the entry path requires (latest row per version) =='
SELECT CASE WHEN to_regclass('external_source_calibration') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select distinct on (source_version)
                   source_version || ' n=' || coalesce(sample_size::text, '?') ||
                   ' ' || coalesce(metric, '?') || '=' || coalesce(score::text, '?') ||
                   ' tol=' || coalesce(tolerance::text, '?') ||
                   ' within=' || coalesce(within_tolerance::text, '?') ||
                   ' by=' || coalesce(measured_by, '?') ||
                   ' at=' || coalesce(measured_at::text, '?') as line
              from external_source_calibration
             order by source_version, measured_at desc) q
  $q$, false, true, '')))[1]::text END AS calibration;

\echo '== 7a · models: state x training provenance kind x source (NULL = absent) =='
SELECT CASE WHEN to_regclass('bettor_funded_models') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select state || ' ' ||
                   coalesce(training_provenance ->> 'kind', '-') || ' ' ||
                   coalesce(training_provenance ->> 'source', '-') || ' ' ||
                   count(*) as line
              from bettor_funded_models
             group by state, training_provenance ->> 'kind',
                      training_provenance ->> 'source') q
  $q$, false, true, '')))[1]::text END AS models;

\echo '== 7b · evidence kinds on reservations (NULL = absent) =='
SELECT CASE WHEN to_regclass('bettor_funded_operation_evidence') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(kind || ':' || n, ', ' order by kind), 'none') as c
      from (select kind, count(*) as n from bettor_funded_operation_evidence
             group by kind) q
  $q$, false, true, '')))[1]::text END AS evidence_kinds;

\echo '== 7c · lost-ack investigations and resolution attempts (NULL = absent) =='
SELECT CASE WHEN to_regclass('bettor_funded_investigations') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(state || ':' || n, ', ' order by state), 'none') as c
      from (select state, count(*) as n from bettor_funded_investigations
             group by state) q
  $q$, false, true, '')))[1]::text END AS investigations,
       CASE WHEN to_regclass('bettor_funded_resolution_audit') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(outcome || ':' || n, ', ' order by outcome), 'none') as c
      from (select outcome, count(*) as n from bettor_funded_resolution_audit
             group by outcome) q
  $q$, false, true, '')))[1]::text END AS resolution_attempts;

\echo '== 7d · non-funded pair observations: label states and freshest (NULL = absent) =='
SELECT CASE WHEN to_regclass('bettor_pair_observations') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(label_status || ':' || n, ', '
                               order by label_status), 'none') as c
      from (select label_status, count(*) as n from bettor_pair_observations
             group by label_status) q
  $q$, false, true, '')))[1]::text END AS observation_labels,
       CASE WHEN to_regclass('bettor_pair_observations') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(max(observed_at)::text, 'none') || ' fixtures=' ||
           count(distinct fixture) as c from bettor_pair_observations
  $q$, false, true, '')))[1]::text END AS observation_span;

\echo '== 7e · funded settlement re-reads by verdict (NULL = absent) =='
SELECT CASE WHEN to_regclass('bettor_funded_settlement_rechecks') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(verdict || ':' || n, ', ' order by verdict), 'none') as c
      from (select verdict, count(*) as n from bettor_funded_settlement_rechecks
             group by verdict) q
  $q$, false, true, '')))[1]::text END AS settlement_rechecks;
