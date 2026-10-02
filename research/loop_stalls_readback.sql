-- Event-loop stall evidence (api.loop_stalls, written by loop_watchdog).
-- Read-only. Each stall: loop lag, watchdog overrun (GIL starvation when
-- large), full length, and every thread's innermost frames.
\echo '== S0 · stall ring =='
SELECT to_timestamp((s->>'at')::float) AS at,
       s->>'loop_lag_s' AS lag_s, s->>'ended_lag_s' AS full_s,
       s->>'watchdog_overrun_s' AS watchdog_overrun_s
  FROM ingestion_state, jsonb_array_elements(value->'stalls') s
 WHERE key = 'api.loop_stalls' ORDER BY 1 DESC LIMIT 20;
\echo '== S1 · stacks of the five longest stalls =='
SELECT to_timestamp((s->>'at')::float) AS at, s->>'ended_lag_s' AS full_s,
       t.key AS thread, array_to_string(ARRAY(SELECT jsonb_array_elements_text(t.value)), E'\n') AS frames
  FROM ingestion_state, jsonb_array_elements(value->'stalls') s, jsonb_each(s->'stacks') t
 WHERE key = 'api.loop_stalls'
   AND (s->>'at') IN (SELECT x->>'at' FROM ingestion_state i2, jsonb_array_elements(i2.value->'stalls') x
                       WHERE i2.key = 'api.loop_stalls'
                       ORDER BY coalesce((x->>'ended_lag_s')::float, (x->>'loop_lag_s')::float) DESC LIMIT 5)
   AND (t.key LIKE 'LOOP%' OR t.key NOT IN ('loop-watchdog'))
 ORDER BY 1 DESC, (t.key LIKE 'LOOP%') DESC;
