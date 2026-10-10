-- READ-ONLY. RC6.3c lane pass-hardening, round 3: the N2 design answer ("the
-- decision steps still run every pass") needs MEASURED timings for every step
-- that runs BEFORE derek, not only derek's and settle's. The heartbeat keeps
-- per-step timings for the LAST pass only, and several pre-derek steps are
-- cadenced (turnaround and shadow_settlement at most every 600 s, the
-- profitability fit / quarantine on their own intervals), so one sample sees
-- them as RAN_WITHIN_*. This samples the heartbeat every 30 s for 10 minutes
-- (passes run every 60 s) and prints, per distinct pass, the elapsed seconds
-- of every pre-derek step and whether each cadenced one actually ran, plus
-- derek's own start-relevant figures. SELECT only.

\echo == S0 the pre-derek step order as deployed (from the last pass digest keys that precede derek)
SELECT value->>'elapsed_s' AS elapsed_s, value->'pass_time' AS pass_time
  FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo == S1..S21 one row per sample: pass written_at, elapsed, pre-derek step seconds (books, maker_maintain, simulate, turnaround, shadow_settlement, profitability_fit, profitability_quarantine, counterfactual_settlement), their sum, derek, and which cadenced steps ran
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
SELECT pg_sleep(30);
SELECT to_char(clock_timestamp(), 'HH24:MI:SS') AS sampled, to_char(to_timestamp((v->>'written_at')::float), 'HH24:MI:SS') AS pass_at, v->>'elapsed_s' AS elapsed,
       v->'step_elapsed_s'->>'books' AS books, v->'step_elapsed_s'->>'maker_maintain' AS maker_m, v->'step_elapsed_s'->>'simulate' AS simulate,
       v->'step_elapsed_s'->>'turnaround' AS turnaround, v->'step_elapsed_s'->>'shadow_settlement' AS shadow_s, v->'step_elapsed_s'->>'profitability_fit' AS prof_fit,
       v->'step_elapsed_s'->>'profitability_quarantine' AS prof_q, v->'step_elapsed_s'->>'counterfactual_settlement' AS cf_settle,
       round((coalesce((v->'step_elapsed_s'->>'books')::numeric,0) + coalesce((v->'step_elapsed_s'->>'maker_maintain')::numeric,0) + coalesce((v->'step_elapsed_s'->>'simulate')::numeric,0) + coalesce((v->'step_elapsed_s'->>'turnaround')::numeric,0) + coalesce((v->'step_elapsed_s'->>'shadow_settlement')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_fit')::numeric,0) + coalesce((v->'step_elapsed_s'->>'profitability_quarantine')::numeric,0) + coalesce((v->'step_elapsed_s'->>'counterfactual_settlement')::numeric,0)), 3) AS pre_derek_sum,
       v->'step_elapsed_s'->>'derek' AS derek, v->'step_elapsed_s'->>'settle' AS settle,
       coalesce(v->'steps'->'turnaround'->>'ran', '-') AS turn_ran, coalesce(v->'steps'->'shadow_settlement'->>'ran', '-') AS shadow_ran, left(coalesce(v->'steps'->'shadow_settlement'->>'why', ''), 24) AS shadow_why,
       coalesce(v->'steps'->'profitability_fit'->>'ran', '-') AS fit_ran, coalesce(v->'steps'->'profitability_quarantine'->>'ran', '-') AS q_ran,
       (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(v->'skipped_steps', '{}'::jsonb)) k) AS skipped, v->>'exceeded_step' AS exceeded
  FROM ingestion_state, LATERAL (SELECT value AS v) t WHERE key = 'paper_session_last_pass';
