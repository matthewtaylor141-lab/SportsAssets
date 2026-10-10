-- RC6.3c pass ring: the newest 20 PAPER pass heartbeats (paper_session_health.recent_heartbeats) with each
-- pass's elapsed time and slowest step, the pass / error counters, and the last pass's cut and skipped steps.
-- Read before and after the RC6.3c deploy. SELECT only.
\echo R1 counters
SELECT session_id, passes, errors, mutation_attempts, heartbeat_at, now() AS read_at FROM paper_session_health ORDER BY heartbeat_at DESC LIMIT 3;
\echo R2 ring: every recorded pass, oldest first
SELECT to_char(to_timestamp((b->>'at')::float), 'MM-DD HH24:MI:SS') AS at, b->>'ok' AS ok, round((b->>'elapsed_s')::numeric, 2) AS elapsed_s, b->>'slowest_step' AS slowest_step, round((b->>'slowest_step_s')::numeric, 2) AS slowest_s, b->'summary'->>'decisions_recorded' AS decisions, b->'summary'->>'orders_submitted' AS orders, b->'summary'->>'reviews' AS reviews FROM paper_session_health h CROSS JOIN LATERAL jsonb_array_elements(h.recent_heartbeats) b WHERE h.heartbeat_at = (SELECT max(heartbeat_at) FROM paper_session_health) ORDER BY (b->>'at')::float;
\echo R3 ring summary
SELECT count(*) AS beats, count(*) FILTER (WHERE b->>'ok' = 'true') AS ok_beats, round(max((b->>'elapsed_s')::numeric), 2) AS max_elapsed_s, round(avg((b->>'elapsed_s')::numeric), 2) AS avg_elapsed_s, count(*) FILTER (WHERE (b->>'slowest_step_s')::numeric >= 20) AS beats_with_a_step_over_20s, to_char(to_timestamp(min((b->>'at')::float)), 'MM-DD HH24:MI:SS') AS first_at, to_char(to_timestamp(max((b->>'at')::float)), 'MM-DD HH24:MI:SS') AS last_at FROM paper_session_health h CROSS JOIN LATERAL jsonb_array_elements(h.recent_heartbeats) b WHERE h.heartbeat_at = (SELECT max(heartbeat_at) FROM paper_session_health);
\echo R4 last pass: ran, elapsed, cut step, skipped steps, error keys
SELECT to_char(to_timestamp((value->>'written_at')::float), 'MM-DD HH24:MI:SS') AS written, value->>'ran' AS ran, value->>'elapsed_s' AS elapsed_s, value->>'exceeded_step' AS exceeded_step, (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(value->'skipped_steps', '{}'::jsonb)) k) AS skipped, (SELECT string_agg(k, ',') FROM jsonb_object_keys(coalesce(value->'errors', '{}'::jsonb)) k) AS error_keys FROM ingestion_state WHERE key = 'paper_session_last_pass';
\echo R5 last pass per-step elapsed, slowest first
SELECT key, value FROM jsonb_each(coalesce((SELECT value->'step_elapsed_s' FROM ingestion_state WHERE key = 'paper_session_last_pass'), '{}'::jsonb)) ORDER BY (value::text)::float DESC NULLS LAST LIMIT 12;
