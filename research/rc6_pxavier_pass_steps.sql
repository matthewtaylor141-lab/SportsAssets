-- READ-ONLY. RC6.2 lane p-xavier (fix stage, second read). Run 37945613145
-- (rc6_pxavier_pass_hold.sql) found the latest paper pass took 27.697 s
-- with its budget exhausted while it held nothing (Xavier: groups_held 0),
-- and that paper_session_health.recent_heartbeats keeps 19 heartbeats of
-- 2026-10-01 01:47Z-02:03Z beside only the newest one (the ring keeps the
-- wrong end). The pass records no per-step time. Here: every step of the
-- latest pass with the scalars it reported, and the session health row,
-- to see which steps ran and what each did. Every statement is a SELECT.

\echo S1 the latest pass attempt, every step with its reported scalars (ingestion_state paper_session_last_pass)
SELECT s.key AS step, left(s.value::text, 600) AS reported
  FROM ingestion_state i, jsonb_each(i.value->'steps') s
 WHERE i.key = 'paper_session_last_pass'
 ORDER BY 1;

\echo S2 the session health row: passes, errors, heartbeat, the last pass header
SELECT session_id, heartbeat_at, passes, errors,
       left(last_error, 300) AS last_error,
       last_pass->>'trigger' AS last_trigger,
       last_pass->>'elapsed_s' AS last_elapsed_s,
       last_pass->>'budget_exhausted' AS last_budget_exhausted,
       jsonb_array_length(recent_heartbeats) AS heartbeats_kept
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 5;
