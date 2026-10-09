-- READ-ONLY. RC6 lane api-responsive: the API loop watchdog's persisted ring.
--
-- The RC5 watchdog (sportsassets/loop_watchdog.py at 1c874c1f) captures every
-- thread's stack the moment the API event loop has been held >= 2 s and
-- persists the last 20 such records to ingestion_state['api.loop_stalls'].
-- Its log line prints only the capture lag; the stacks (code locations only:
-- file:line function -- no locals, arguments, request data or environment)
-- and the full stall length (ended_lag_s) live in this row, which no research
-- file read. This one does, so the remaining stalls are named from evidence.

\echo S1 the ring: when it was last written and how many records it holds
SELECT to_timestamp((value->>'written_at')::float) AS written_at,
       value->>'stall_s' AS stall_s,
       jsonb_array_length(value->'stalls') AS records
  FROM ingestion_state WHERE key = 'api.loop_stalls';

\echo S2 one row per stall: capture lag, watchdog overrun (GIL held off-Python), full length
SELECT s.ord,
       to_timestamp((s.e->>'at')::float) AS captured_at,
       s.e->>'loop_lag_s' AS lag_s,
       s.e->>'watchdog_overrun_s' AS overrun_s,
       s.e->>'ended_lag_s' AS ended_lag_s,
       (SELECT count(*) FROM jsonb_object_keys(s.e->'stacks')) AS threads
  FROM ingestion_state i,
       jsonb_array_elements(i.value->'stalls') WITH ORDINALITY AS s(e, ord)
 WHERE i.key = 'api.loop_stalls'
 ORDER BY s.ord;

\echo S3 the LOOP thread frames per stall (outermost first, innermost last)
SELECT s.ord, f.n, f.line
  FROM ingestion_state i,
       jsonb_array_elements(i.value->'stalls') WITH ORDINALITY AS s(e, ord),
       jsonb_each(s.e->'stacks') AS t(label, frames),
       jsonb_array_elements_text(t.frames) WITH ORDINALITY AS f(line, n)
 WHERE i.key = 'api.loop_stalls' AND t.label LIKE 'LOOP%'
 ORDER BY s.ord, f.n;

\echo S4 every other thread that was not parked: its innermost 5 frames
SELECT s.ord, t.label AS thread, f.n, f.line
  FROM ingestion_state i,
       jsonb_array_elements(i.value->'stalls') WITH ORDINALITY AS s(e, ord),
       jsonb_each(s.e->'stacks') AS t(label, frames),
       jsonb_array_elements_text(t.frames) WITH ORDINALITY AS f(line, n)
 WHERE i.key = 'api.loop_stalls' AND t.label NOT LIKE 'LOOP%'
   AND f.n > jsonb_array_length(t.frames) - 5
   AND NOT ((t.frames->>(jsonb_array_length(t.frames) - 1))
            ~ '(thread\.py:[0-9]+ _worker|threading\.py:[0-9]+ wait|queue\.py:[0-9]+ get|selectors\.py:[0-9]+ select)$')
 ORDER BY s.ord, t.label, f.n;

\echo S5 innermost frame of every thread, counted across the ring (parked threads included)
SELECT t.frames->>(jsonb_array_length(t.frames) - 1) AS innermost,
       count(*) AS threads_x_stalls,
       count(*) FILTER (WHERE t.label LIKE 'LOOP%') AS on_loop
  FROM ingestion_state i,
       jsonb_array_elements(i.value->'stalls') AS s(e),
       jsonb_each(s.e->'stacks') AS t(label, frames)
 WHERE i.key = 'api.loop_stalls' AND jsonb_array_length(t.frames) > 0
 GROUP BY 1 ORDER BY 2 DESC LIMIT 60;
