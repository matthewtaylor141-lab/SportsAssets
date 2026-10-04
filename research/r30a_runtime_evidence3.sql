-- R30A RUNTIME STREAM, third read (READ ONLY). Why the API's 2-3 s bounded
-- steps time out in episodes: the event-loop stalls the API's own watchdog
-- records (with the loop thread's stack), how long the reactive audit waited
-- before its row reached the server, which decisions carry no Pinnacle age,
-- and the research worker's task events around the timeout minutes.

\echo '== H1 · api.loop_stalls: each recorded stall, its lag, and the LOOP thread stack (innermost first) =='
SELECT to_timestamp((s->>'at')::float8) AS at,
       s->>'loop_lag_s' AS lag_s, s->>'watchdog_overrun_s' AS overrun_s,
       s->>'ended_lag_s' AS ended_lag_s,
       (SELECT string_agg(fr.f, ' <- ' ORDER BY fr.ord DESC)
          FROM jsonb_each(s->'stacks') st(k, v),
               jsonb_array_elements_text(st.v) WITH ORDINALITY AS fr(f, ord)
         WHERE st.k LIKE 'LOOP%') AS loop_stack
  FROM ingestion_state, jsonb_array_elements(value->'stalls') s
 WHERE key = 'api.loop_stalls'
 ORDER BY 1;

\echo '== H1b · api.loop_stalls: every other thread, its three innermost frames =='
SELECT to_timestamp((s->>'at')::float8) AS at, st.k AS thread,
       (SELECT string_agg(fr.f, ' <- ' ORDER BY fr.ord DESC)
          FROM jsonb_array_elements_text(st.v) WITH ORDINALITY AS fr(f, ord)
         WHERE fr.ord > jsonb_array_length(st.v) - 3) AS innermost
  FROM ingestion_state, jsonb_array_elements(value->'stalls') s,
       jsonb_each(s->'stacks') st(k, v)
 WHERE key = 'api.loop_stalls' AND st.k NOT LIKE 'LOOP%'
 ORDER BY 1, 2;

\echo '== H2 · reactive STARTED audit: server row time minus the job start (acquire + send), per 10 minutes =='
SELECT date_trunc('hour', created_at)
         + floor(extract(minute FROM created_at) / 10) * interval '10 minutes' AS bucket,
       count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY lag)::numeric, 3) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY lag)::numeric, 3) AS p90_s,
       round(max(lag)::numeric, 3) AS max_s,
       count(*) FILTER (WHERE lag > 1.0) AS over_1s
  FROM (SELECT created_at,
               extract(epoch FROM created_at)
                 - (detail->>'evaluation_started_at')::float8 AS lag
          FROM pinnapi_reactive_attempts
         WHERE created_at >= timestamptz '2026-10-04 15:30:00+00'
           AND jsonb_typeof(detail->'evaluation_started_at') = 'number') q
 GROUP BY 1 ORDER BY 1;

\echo '== H3 · reactive completion audit: server row time minus finished_at, per 10 minutes =='
SELECT date_trunc('hour', updated_at)
         + floor(extract(minute FROM updated_at) / 10) * interval '10 minutes' AS bucket,
       count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY lag)::numeric, 3) AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY lag)::numeric, 3) AS p90_s,
       round(max(lag)::numeric, 3) AS max_s
  FROM (SELECT updated_at,
               extract(epoch FROM updated_at)
                 - (detail->>'finished_at')::float8 AS lag
          FROM pinnapi_reactive_attempts
         WHERE updated_at >= timestamptz '2026-10-04 15:30:00+00'
           AND jsonb_typeof(detail->'finished_at') = 'number') q
 GROUP BY 1 ORDER BY 1;

\echo '== H4 · decisions in the last 24 h with NO recorded Pinnacle age, by policy / verdict / refusal =='
SELECT policy_version, verdict, coalesce(refusal, '-') AS refusal, count(*) AS n
  FROM paper_decisions
 WHERE decided_at > now() - interval '24 hours'
   AND jsonb_typeof(pinnacle->'age_s') IS DISTINCT FROM 'number'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 30;

\echo '== H4b · the key sets of those decisions pinnacle objects =='
SELECT (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(pinnacle) k) AS keys,
       count(*) AS n
  FROM paper_decisions
 WHERE decided_at > now() - interval '24 hours'
   AND jsonb_typeof(pinnacle) = 'object'
   AND jsonb_typeof(pinnacle->'age_s') IS DISTINCT FROM 'number'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo '== H4c · decisions in the last 24 h, all, by policy and whether an age is recorded =='
SELECT policy_version, verdict,
       count(*) AS n,
       count(*) FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number') AS with_age,
       count(*) FILTER (WHERE jsonb_typeof(pinnacle->'age_s') = 'number'
                         AND (pinnacle->>'age_s')::float8 > 30) AS age_over_30s
  FROM paper_decisions
 WHERE decided_at > now() - interval '24 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC;

\echo '== H5 · research worker task events per minute around the timeout episodes =='
SELECT date_trunc('minute', at) AS minute, kind, count(*) AS n
  FROM agent_task_events
 WHERE at BETWEEN timestamptz '2026-10-04 15:45:00+00' AND timestamptz '2026-10-04 18:50:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== H6 · open research tasks the claim scans (kind, status) =='
SELECT status, count(*) AS n
  FROM agent_tasks
 WHERE kind = 'AGENT_CAPABILITY_REVIEW_V1'
 GROUP BY 1 ORDER BY 1;

\echo '== H7 · installed extensions =='
SELECT extname, extversion FROM pg_extension ORDER BY 1;
