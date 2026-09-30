-- READ-ONLY. RELEASE RECEIPTS FOR THE SERVING BUILD.
--
-- R1  serving build as the cycle and servicing heartbeats record it, and the
--     migration ledger's newest entries
-- R2  pair collector: observed / admitted / labelled / training-eligible /
--     model-approved, kept apart; the unresolved cancellation facts listed
-- R3  the servicing task: its own heartbeat, recent pass starts, durations,
--     skipped-busy counts and errors; whether any position was serviced
-- R4  the market-data subscription digest (flag, connection state, evidence);
--     a connection is NOT book currency, so the M1 refusal counts are shown
-- R5  what funded activity exists (intents, Xavier decisions, events)
--
-- Training eligibility is the reader's own rule: label_status = 'LABELLED'
-- AND admission_status = 'ADMITTED_BY_DISCOVERY' (bettor_pair_observations
-- .labelled). Model approval is bettor_funded_models.state = 'APPROVED'.
-- No balance, credential or account secret is selected.

\echo '== R1a · cycle heartbeat: writer build, state, age =='
SELECT to_timestamp((value->>'at')::float8)                       AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1) AS age_s,
       value->'writer'->>'build'                                  AS writer_build,
       value->>'state'                                            AS state,
       value->>'elapsed_s'                                        AS cycle_elapsed_s
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== R1b · migration ledger: newest five =='
SELECT version, applied_at FROM schema_migrations ORDER BY version DESC LIMIT 5;

\echo '== R2a · observations: admission x label status =='
SELECT admission_status, label_status, count(*) AS observations,
       count(DISTINCT fixture) AS fixtures,
       min(observed_at) AS first_observed, max(observed_at) AS last_observed
  FROM bettor_pair_observations GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== R2b · the five-way split (fixtures count once each) =='
SELECT count(*)                                                    AS observed,
       count(*) FILTER (WHERE admission_status = 'ADMITTED_BY_DISCOVERY') AS admitted,
       count(*) FILTER (WHERE label_status = 'LABELLED')           AS labelled,
       count(*) FILTER (WHERE label_status = 'LABELLED'
                          AND admission_status = 'ADMITTED_BY_DISCOVERY') AS training_eligible,
       count(DISTINCT fixture)                                     AS observed_fixtures,
       count(DISTINCT fixture) FILTER (WHERE label_status = 'LABELLED'
                          AND admission_status = 'ADMITTED_BY_DISCOVERY') AS training_eligible_fixtures,
       (SELECT count(*) FROM bettor_funded_models WHERE state = 'APPROVED') AS models_approved,
       (SELECT count(*) FROM bettor_funded_models)                 AS models_any_state
  FROM bettor_pair_observations;

\echo '== R2c · unresolved facts carried by unadmitted observations =='
SELECT u.fact AS unresolved_fact, count(*) AS observations,
       count(DISTINCT o.fixture) AS fixtures
  FROM bettor_pair_observations o,
       LATERAL jsonb_array_elements_text(o.unresolved) AS u(fact)
 WHERE o.admission_status <> 'ADMITTED_BY_DISCOVERY'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== R2d · cancellation treatment per leg on unadmitted observations =='
SELECT o.cancellation_terms->'held'->>'treatment'  AS held_treatment,
       o.cancellation_terms->'hedge'->>'treatment' AS hedge_treatment,
       o.cancellation_terms->'pair'->>'verdict'    AS pair_verdict,
       count(*) AS observations, count(DISTINCT o.fixture) AS fixtures
  FROM bettor_pair_observations o
 WHERE o.admission_status <> 'ADMITTED_BY_DISCOVERY'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo '== R2e · newest observations =='
SELECT observed_at, fixture, primary_slug, hedge_slug, admission_status,
       label_status, unresolved
  FROM bettor_pair_observations ORDER BY observed_at DESC LIMIT 15;

\echo '== R2f · attempts since the newest deploy of this schema, by conclusion =='
SELECT coalesce(detail->>'conclusion', outcome) AS conclusion, count(*) AS attempts,
       count(DISTINCT fixture) AS fixtures, min(attempted_at) AS first, max(attempted_at) AS last
  FROM bettor_pair_observation_attempts
 WHERE attempted_at > (SELECT applied_at FROM schema_migrations
                        WHERE version LIKE '151%' LIMIT 1)
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== R3a · servicing task heartbeat (its own row) =='
SELECT to_timestamp((value->>'at')::float8)                       AS written_at,
       round((extract(epoch FROM now()) - (value->>'at')::float8)::numeric, 1) AS age_s,
       value->'writer'->>'build'                                  AS writer_build,
       value->>'state'                                            AS state,
       jsonb_pretty(value->'servicing_cadence')                   AS servicing_cadence
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';

\echo '== R3b · the last servicing pass as recorded (source, elapsed, what it serviced) =='
SELECT jsonb_pretty(value->'last') AS last_pass
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_servicing';

\echo '== R4 · market-data subscription digest from the cycle heartbeat =='
SELECT jsonb_pretty(value->'market_subscription') AS market_subscription
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== R5 · funded activity =='
SELECT (SELECT count(*) FROM bettor_funded_intents)          AS funded_intents,
       (SELECT count(*) FROM bettor_xavier_decisions)        AS xavier_decisions,
       (SELECT count(*) FROM bettor_xavier_execution_events) AS xavier_execution_events,
       (SELECT count(*) FROM bettor_funded_owner_authorization_audit) AS owner_authorization_rows,
       (SELECT count(*) FROM bettor_account_reconciliation_reports)   AS reconciliation_reports;
