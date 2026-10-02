-- Event-loop stalls, compact (read-only): every ring entry with the loop
-- thread's three innermost frames, newest first; and the ring's span.
\echo '== C0 · ring span and count =='
SELECT count(*) AS stalls,
       min(to_timestamp((s->>'at')::float)) AS oldest,
       max(to_timestamp((s->>'at')::float)) AS newest,
       max(coalesce((s->>'ended_lag_s')::float, (s->>'loop_lag_s')::float)) AS worst_s
  FROM ingestion_state i, jsonb_array_elements(i.value->'stalls') s
 WHERE i.key = 'api.loop_stalls';
\echo '== C1 · each stall: length and the loop thread innermost frames =='
SELECT to_timestamp((s->>'at')::float) AS at,
       s->>'loop_lag_s' AS lag_s, s->>'ended_lag_s' AS full_s,
       s->>'watchdog_overrun_s' AS overrun_s,
       (SELECT array_to_string(ARRAY(SELECT x FROM jsonb_array_elements_text(t.value) WITH ORDINALITY AS a(x, n)
                                      ORDER BY n DESC LIMIT 3), ' <- ')
          FROM jsonb_each(s->'stacks') t WHERE t.key LIKE 'LOOP%' LIMIT 1) AS loop_frames
  FROM ingestion_state i, jsonb_array_elements(i.value->'stalls') s
 WHERE i.key = 'api.loop_stalls'
 ORDER BY 1 DESC LIMIT 30;
