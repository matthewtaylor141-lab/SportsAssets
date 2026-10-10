-- RC6.3c PROOF B5b: THE SHADOW SETTLEMENT STEP, OBSERVED ON PRODUCTION (SELECT only; pg_sleep between samples).
--
-- WHY. bettor_capital_authority.step runs settle_shadows at most once per RUN_EVERY_S = 600 s (:177, :1036-1037); the
-- nine passes in between return {ran: false, why: RAN_WITHIN_RUN_EVERY_S}. settle_shadows swallows a failing batched
-- read into its result (:969-979 why = SETTLEMENT_READ_FAILED:<Exc>, every shadow left pending) and a step-deadline
-- cut likewise (:956-963 cut = SHADOW_SETTLEMENT_CUT_AT_THE_STEP_DEADLINE), and paper_runtime counts a step's own
-- result as an error only for RESULT_ERROR_STEPS = {audrey_coverage} (paper_runtime.py:512): the pass stays ok = true,
-- errors stays 0 and nothing is written. The only record of the step's result is the digest of the LAST pass in the
-- heartbeat ingestion_state[paper_session_last_pass] (steps.shadow_settlement, step_elapsed_s.shadow_settlement),
-- overwritten by the next pass about 60 s later. So this file SAMPLES that heartbeat every 30 s, 24 times
-- (690 s > RUN_EVERY_S + one 60 s cadence = 660 s): every pass of the window is seen (a heartbeat lives about
-- 60 s), and at least one of them ran the step. Each sample is ONE statement whose snapshot is taken at its start
-- and which then sleeps 30 s (the pg_sleep sits in the same statement so the log stays compact; sampled_at is
-- statement_timestamp(), the snapshot instant). The run takes about 12.0 minutes of the research-sql
-- concurrency group (one run at a time, nothing cancelled); the statement_timeout (600 s) is per statement.
--
-- PASS CRITERIA. At least one sample with verdict RAN_CLEAN (the step ran: ran = true, no why beginning
-- SETTLEMENT_READ_FAILED, no cut, no refusal, errors = 0, shadow_settlement_s below 1.0 s); no sample with a FAIL
-- verdict; every sample ran = true (the pass itself) with no exceeded_step. examined = settled + pending + errors on
-- a RAN_CLEAN sample (a duplicate outcome, ON CONFLICT DO NOTHING, is the only other case and is counted in neither).
-- The S0 / S99 rows give the pass counters at the start and the end: passes_end - passes_start = the passes the
-- samples span (about 11 at the 60 s cadence); errors must not change.
-- When pending shadows are 0 (B4 pending_now, B4b pending_shadows), a RAN_CLEAN sample proves the step runs and
-- its reads succeed on the release, not that it settles anything: say so in the report.

\echo S0 start: the pass counters, the newest attempt, the workers boot commit
SELECT to_char(statement_timestamp(), 'YYYY-MM-DD HH24:MI:SS') AS started_at, h.passes AS passes_start, h.errors AS errors_start,
       to_char(h.heartbeat_at, 'HH24:MI:SS') AS heartbeat_at, (SELECT value->>'commit' FROM ingestion_state WHERE key = 'workers_boot') AS workers_commit,
       (SELECT value->>'at' FROM ingestion_state WHERE key = 'workers_boot') AS workers_boot_at
  FROM paper_session_health h ORDER BY h.heartbeat_at DESC NULLS LAST LIMIT 1;
\echo S1 sample 1 of 24 (then 30 s)
SELECT 1 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S2 sample 2 of 24 (then 30 s)
SELECT 2 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S3 sample 3 of 24 (then 30 s)
SELECT 3 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S4 sample 4 of 24 (then 30 s)
SELECT 4 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S5 sample 5 of 24 (then 30 s)
SELECT 5 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S6 sample 6 of 24 (then 30 s)
SELECT 6 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S7 sample 7 of 24 (then 30 s)
SELECT 7 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S8 sample 8 of 24 (then 30 s)
SELECT 8 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S9 sample 9 of 24 (then 30 s)
SELECT 9 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S10 sample 10 of 24 (then 30 s)
SELECT 10 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S11 sample 11 of 24 (then 30 s)
SELECT 11 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S12 sample 12 of 24 (then 30 s)
SELECT 12 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S13 sample 13 of 24 (then 30 s)
SELECT 13 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S14 sample 14 of 24 (then 30 s)
SELECT 14 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S15 sample 15 of 24 (then 30 s)
SELECT 15 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S16 sample 16 of 24 (then 30 s)
SELECT 16 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S17 sample 17 of 24 (then 30 s)
SELECT 17 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S18 sample 18 of 24 (then 30 s)
SELECT 18 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S19 sample 19 of 24 (then 30 s)
SELECT 19 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S20 sample 20 of 24 (then 30 s)
SELECT 20 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S21 sample 21 of 24 (then 30 s)
SELECT 21 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S22 sample 22 of 24 (then 30 s)
SELECT 22 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S23 sample 23 of 24 (then 30 s)
SELECT 23 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict,
       pg_sleep(30) IS NULL AS slept_30s
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S24 sample 24 of 24
SELECT 24 AS sample, to_char(statement_timestamp(), 'HH24:MI:SS') AS sampled_at,
       to_char(to_timestamp((value->>'written_at')::float8), 'HH24:MI:SS') AS hb_written, to_char(to_timestamp((value->>'at')::float8), 'HH24:MI:SS') AS pass_at,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'elapsed_s' AS pass_s, value->>'exceeded_step' AS exceeded_step,
       value->'step_elapsed_s'->>'settle' AS settle_s, value->'step_elapsed_s'->>'shadow_settlement' AS shadow_s,
       sd->>'ran' AS shadow_ran, left(sd->>'why', 60) AS why, sd->>'cut' AS cut, sd->>'not_examined' AS not_examined, sd->>'refusal' AS refusal,
       sd->>'examined' AS examined, sd->>'settled' AS settled, sd->>'pending' AS pending, sd->>'errors' AS errors,
       CASE WHEN value->>'ran' IS DISTINCT FROM 'true' THEN 'FAIL_PASS_RAN_FALSE:' || coalesce(value->>'refusal', '?')
            WHEN value->>'exceeded_step' IS NOT NULL THEN 'FAIL_PASS_CUT_STEP:' || (value->>'exceeded_step')
            WHEN sd IS NULL THEN 'FAIL_NO_SHADOW_SETTLEMENT_STEP_IN_THE_DIGEST'
            WHEN sd->>'ran' = 'true' AND coalesce(sd->>'why', '') LIKE 'SETTLEMENT_READ_FAILED%' THEN 'FAIL_SETTLEMENT_READ_FAILED'
            WHEN sd->>'ran' = 'true' AND sd->>'cut' IS NOT NULL THEN 'FAIL_CUT_AT_STEP_DEADLINE'
            WHEN sd->>'ran' = 'true' AND sd->>'refusal' IS NOT NULL THEN 'FAIL_REFUSED:' || (sd->>'refusal')
            WHEN sd->>'ran' = 'true' AND coalesce((sd->>'errors')::int, 0) > 0 THEN 'FAIL_ERRORS'
            WHEN sd->>'ran' = 'true' AND coalesce((value->'step_elapsed_s'->>'shadow_settlement')::float8, 0) >= 1.0 THEN 'FAIL_SHADOW_SETTLEMENT_OVER_1S'
            WHEN sd->>'ran' = 'true' THEN 'RAN_CLEAN'
            WHEN sd->>'why' = 'RAN_WITHIN_RUN_EVERY_S' THEN 'NOT_DUE'
            ELSE 'UNEXPECTED_SHAPE' END AS verdict
  FROM (SELECT value, value->'steps'->'shadow_settlement' AS sd FROM ingestion_state WHERE key = 'paper_session_last_pass') q;
\echo S99 end: the pass counters again (passes_end - passes_start = passes spanned; errors must be unchanged)
SELECT to_char(statement_timestamp(), 'YYYY-MM-DD HH24:MI:SS') AS ended_at, h.passes AS passes_end, h.errors AS errors_end,
       to_char(h.heartbeat_at, 'HH24:MI:SS') AS heartbeat_at
  FROM paper_session_health h ORDER BY h.heartbeat_at DESC NULLS LAST LIMIT 1;
