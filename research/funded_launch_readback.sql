-- READ-ONLY. EVERY FUNDED-LAUNCH PREREQUISITE THAT LIVES IN PRODUCTION DATA.
-- (Code-level switches are read from the serving build by the Command Centre
-- overview and the admin prerequisites route; they are not in the database.)
-- Only presence, state and timestamps of the authorization records are read;
-- no key material, balance or credential field is selected.

\echo '== L1 · the venue account: status, pause and accounting =='
SELECT account_id, status, paused, left(coalesce(pause_reason, ''), 200) AS pause_reason,
       accounting_status, last_verified_at
  FROM bettor_desk_accounts ORDER BY account_id;

\echo '== L2 · account binding, approved limits, authorization, owner authorization, reconciliation =='
SELECT k.key,
       (s.key IS NOT NULL) AS present,
       CASE WHEN s.value IS NULL THEN NULL
            ELSE (SELECT string_agg(j.key, ',' ORDER BY j.key)
                    FROM jsonb_object_keys(s.value) AS j(key)) END AS fields_present,
       s.value->>'account_id' AS account_id,
       coalesce(s.value->>'expires_at', s.value->>'valid_until') AS expires,
       coalesce(s.value->>'recorded_at', s.value->>'at', s.value->>'approved_at') AS recorded
  FROM (VALUES ('bettor_funded_account_binding'), ('bettor_funded_limits_approved'),
               ('bettor_funded_authorization'), ('bettor_funded_owner_authorization'),
               ('bettor_funded_account_reconciliation'),
               ('bettor_funded_authorization_renewals')) AS k(key)
  LEFT JOIN ingestion_state s ON s.key = k.key
 ORDER BY k.key;

\echo '== L3 · owner-authorization audit rows and reconciliation reports =='
SELECT (SELECT count(*) FROM bettor_funded_owner_authorization_audit) AS owner_authorization_audit_rows,
       (SELECT count(*) FROM bettor_account_reconciliation_reports) AS reconciliation_reports;

\echo '== L4 · evidence gates: calibration, void rate, entry currency, admitted entries =='
SELECT (value->'source_calibration_measurement'->>'status') AS calibration_status,
       (value->'source_calibration_measurement'->>'resolved_fixtures') AS resolved_fixtures,
       (value->'source_calibration_measurement'->'shortfall'->>'shortfall') AS calibration_shortfall,
       (value->'pair_observation'->>'observations_written') AS last_pass_observations,
       to_timestamp((value->>'at')::float8) AS last_cycle_at,
       value->'writer'->>'build' AS cycle_build
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

SELECT count(*) FILTER (WHERE admissible) AS admissible_valuations_7d,
       count(*) AS valuations_7d,
       count(*) FILTER (WHERE refusals[1] = 'VENUE_BOOK_CURRENCY_NOT_ESTABLISHED') AS first_refusal_book_currency,
       max(decided_at) AS newest
  FROM external_valuations WHERE decided_at > now() - interval '7 days';

\echo '== L5 · settled observation fixtures for the void rate (needs 40) =='
SELECT count(DISTINCT fixture) FILTER (WHERE label_status = 'LABELLED'
                                        OR label_why LIKE 'the venue declared a void%') AS settled_fixtures,
       count(DISTINCT fixture) FILTER (WHERE label_why LIKE 'the venue declared a void%') AS void_fixtures,
       count(*) AS observations
  FROM bettor_pair_observations;

\echo '== L6 · funded orders ever recorded =='
SELECT state, count(*) AS n, max(created_at) AS newest FROM bettor_funded_intents GROUP BY 1;
