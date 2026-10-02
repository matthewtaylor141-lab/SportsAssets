-- Event-loop stall evidence (api.loop_stalls, written by loop_watchdog).
-- Read-only. Each stall: loop lag, watchdog overrun (GIL starvation when
-- large), full length, and every thread's innermost frames.
\echo '== S0 · stall ring =='
SELECT to_timestamp((s->>'at')::float) AS at,
       s->>'loop_lag_s' AS lag_s, s->>'ended_lag_s' AS full_s,
       s->>'watchdog_overrun_s' AS watchdog_overrun_s
  FROM ingestion_state i, jsonb_array_elements(i.value->'stalls') s
 WHERE i.key = 'api.loop_stalls' ORDER BY 1 DESC LIMIT 20;
\echo '== S1 · thread stacks of the five longest stalls (loop thread first) =='
WITH st AS (
  SELECT s FROM ingestion_state i, jsonb_array_elements(i.value->'stalls') s
   WHERE i.key = 'api.loop_stalls'
   ORDER BY coalesce((s->>'ended_lag_s')::float, (s->>'loop_lag_s')::float) DESC LIMIT 5)
SELECT to_timestamp((st.s->>'at')::float) AS at, st.s->>'ended_lag_s' AS full_s,
       t.key AS thread,
       array_to_string(ARRAY(SELECT jsonb_array_elements_text(t.value)), E'\n') AS frames
  FROM st, jsonb_each(st.s->'stacks') t
 WHERE t.key <> 'loop-watchdog'
 ORDER BY 1 DESC, (t.key LIKE 'LOOP%') DESC, t.key;
