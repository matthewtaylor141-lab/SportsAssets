-- READ-ONLY. PRODUCTION READINESS, FROM PRODUCTION'S OWN ROWS, FOR aca3564.
--
-- Five questions, each answered from current rows rather than a description:
--   E  entry: does any real candidate clear every admission check at once,
--      without a research waiver? If none, what fails first and what else;
--   M  management: what the funded servicing pass did on the last cycle and
--      what the funded book holds;
--   P  indirect pairing: which model is approved and on what records;
--   L  learning: non-funded observations, their labels, and how candidates
--      were scored -- generation is not approval is not improvement;
--   A  account: reconciliation, binding, limits and authorization records.
--
-- WHAT IT DOES NOT READ: no balance, cash, buying power or equity, and no
-- credential. Absent tables report NULL through to_regclass + query_to_xml.

\echo '== E1 · valuations in the last 24 h: total, admissible, admissible without any waiver =='
SELECT count(*)                                                AS valuations_24h,
       count(*) FILTER (WHERE admissible)                      AS admissible,
       count(*) FILTER (WHERE admissible AND coalesce(jsonb_array_length(
           risk_verdict -> 'research_waiver' -> 'waived'), 0) = 0)
                                                               AS admissible_no_waiver,
       count(*) FILTER (WHERE coalesce(jsonb_array_length(
           risk_verdict -> 'research_waiver' -> 'waived'), 0) > 0)
                                                               AS rested_on_a_waiver,
       max(decided_at)                                         AS newest
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours';

\echo '== E2 · the closest candidates (fewest refusals) in the last 24 h, every refusal named =='
SELECT decided_at, sport_family, market, left(coalesce(condition_id, ''), 60) AS contract,
       admissible, cardinality(refusals) AS n_refusals, refusals,
       risk_verdict -> 'research_waiver' -> 'waived'           AS waived,
       round(estimated_edge_per_contract::numeric, 4)          AS edge_per_contract,
       round(age_s::numeric, 1)                                AS quote_age_s
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 ORDER BY cardinality(refusals), decided_at DESC
 LIMIT 8;

\echo '== E3 · first refusal and every refusal, counted, last 24 h =='
SELECT 'first:' || coalesce(refusals[1], '(none)') AS refusal, count(*) AS n
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1
UNION ALL
SELECT 'any:' || r, count(*)
  FROM external_valuations, unnest(refusals) AS r
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1
 ORDER BY 1, 2 DESC;

\echo '== E4 · per-event outcomes of the most recent cycle, with every code =='
SELECT CASE WHEN to_regclass('ext_candidate_outcomes') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select outcome || ' first=' || coalesce(first_refusal, '-') ||
                   ' codes=' || codes::text || ' n=' || count(*) as line
              from ext_candidate_outcomes
             where cycle_id = (select cycle_id from ext_candidate_outcomes
                                order by cycle_at desc, id desc limit 1)
             group by outcome, first_refusal, codes) q
  $q$, false, true, '')))[1]::text END AS latest_cycle_codes;

\echo '== M1 · the last cycle: when, which build, and the funded servicing record =='
SELECT to_timestamp((value->>'at')::float8)                 AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1)
                                                             AS age_s,
       value->'writer'->>'build'                             AS writer_build,
       value->>'state'                                       AS state,
       left(coalesce((value->'funded_servicing')::text, 'null'), 1200)
                                                             AS funded_servicing,
       left(coalesce((value->'pair_observation')::text, 'null'), 1200)
                                                             AS pair_observation
  FROM ingestion_state
 WHERE key = 'ext_pinnacle_last_cycle';

\echo '== M2 · the funded book: intents by kind and state (NULL = absent) =='
SELECT CASE WHEN to_regclass('bettor_funded_intents') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(kind || '/' || state || ':' || n, ', '
                               order by kind, state), 'none') as c
      from (select kind::text as kind, state::text as state, count(*) as n
              from bettor_funded_intents group by 1, 2) q
  $q$, false, true, '')))[1]::text END AS funded_intents;

\echo '== P1 · pairing models: id, state, provenance kind and source, approval =='
SELECT CASE WHEN to_regclass('bettor_funded_models') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(line, E'\n' order by line), 'none') as c
      from (select model_id || ' ' || state || ' ' ||
                   coalesce(training_provenance ->> 'kind', '-') || ' ' ||
                   coalesce(training_provenance ->> 'source', '-') ||
                   ' events=' || coalesce(training_provenance ->> 'n_events', '-') ||
                   ' approved_by=' || coalesce(approved_by, '-') ||
                   ' evaluated_at=' || coalesce(evaluation ->> 'evaluated_at', '-') as line
              from bettor_funded_models) q
  $q$, false, true, '')))[1]::text END AS models;

\echo '== L1 · non-funded observations: by label status, span, and label history =='
SELECT CASE WHEN to_regclass('bettor_pair_observations') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(label_status || ':' || n || ' fixtures=' || f ||
                               ' first=' || a || ' last=' || b, E'\n'
                               order by label_status), 'none') as c
      from (select label_status, count(*) as n, count(distinct fixture) as f,
                   min(observed_at)::text as a, max(observed_at)::text as b
              from bettor_pair_observations group by label_status) q
  $q$, false, true, '')))[1]::text END AS observations,
       CASE WHEN to_regclass('bettor_pair_observation_labels') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select count(*) || ' versions over ' || count(distinct observation_id) ||
           ' observations; newest ' || coalesce(max(recorded_at)::text, 'none') as c
      from bettor_pair_observation_labels
  $q$, false, true, '')))[1]::text END AS label_history,
       CASE WHEN to_regclass('bettor_pair_observations') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(v || ':' || n, ', ' order by v), 'none') as c
      from (select coalesce(price_basis -> 'primary' ->> 'book_currency_verdict',
                            'null') as v, count(*) as n
              from bettor_pair_observations group by 1) q
  $q$, false, true, '')))[1]::text END AS primary_price_currency;

\echo '== L2 · the settlement re-reads of funded legs, by verdict =='
SELECT CASE WHEN to_regclass('bettor_funded_settlement_rechecks') IS NOT NULL THEN
  (xpath('/row/c/text()', query_to_xml($q$
    select coalesce(string_agg(verdict || ':' || n, ', ' order by verdict), 'none') as c
      from (select verdict, count(*) as n from bettor_funded_settlement_rechecks
             group by verdict) q
  $q$, false, true, '')))[1]::text END AS settlement_rechecks;

\echo '== A1 · the account registry: identity and state only =='
SELECT account_id, desk_id, status, paused,
       left(coalesce(pause_reason, ''), 120) AS pause_reason,
       accounting_status, last_verified_at
  FROM bettor_desk_accounts
 ORDER BY account_id;

\echo '== A2 · activation records: presence, size and top-level field names only =='
SELECT k AS key,
       (SELECT length(value::text) FROM ingestion_state WHERE key = k) AS bytes,
       (SELECT string_agg(f, ',' ORDER BY f)
          FROM ingestion_state s, jsonb_object_keys(
               CASE WHEN jsonb_typeof(s.value) = 'object' THEN s.value
                    ELSE '{}'::jsonb END) AS f
         WHERE s.key = k) AS fields
  FROM unnest(ARRAY['bettor_funded_account_binding',
                    'bettor_funded_account_reconciliation',
                    'bettor_funded_limits_approved',
                    'bettor_funded_authorization',
                    'bettor_funded_owner_authorization']) AS k;

\echo '== A3 · the odds source calibration the entry path requires =='
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
