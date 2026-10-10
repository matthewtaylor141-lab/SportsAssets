-- READ ONLY. Frontend root-cause audit (group frontend), part C: the work
-- state the desks show, the management epoch against the ledger, and the
-- PinnAPI heartbeat age as the API would read it. SELECT statements only.
\echo == C1. open agent_tasks by assignee (what the desk HANDOFF PENDING counts) ==
SELECT assignee, count(*) AS n, min(created_at) AS oldest, max(created_at) AS newest
  FROM agent_tasks WHERE status = 'OPEN' GROUP BY assignee ORDER BY n DESC;

\echo == C2. karen_challenges by target and state ==
SELECT target_agent, state, count(*) AS n, min(challenged_at) AS oldest, max(challenged_at) AS newest
  FROM karen_challenges WHERE state IN ('OPEN', 'RESPONDED') GROUP BY target_agent, state ORDER BY n DESC;

\echo == C3. ledger at the management epoch (2026-10-05 00:00 New York = 04:00Z) and now ==
SELECT seq, kind, committed_at, cash_after_usd, reserved_after_usd
  FROM paper_ledger
 WHERE committed_at <= timestamptz '2026-10-05 04:00:00+00'
 ORDER BY seq DESC LIMIT 1;
SELECT count(*) AS fills_before_epoch, count(DISTINCT group_id) AS groups_before_epoch
  FROM paper_fills WHERE filled_at <= timestamptz '2026-10-05 04:00:00+00';
SELECT cash_after_usd AS ledger_cash_now, reserved_after_usd,
       cash_after_usd - 500000 AS ledger_result_since_funding
  FROM paper_ledger ORDER BY seq DESC LIMIT 1;

\echo == C4. PinnAPI heartbeat: beat_at against the database clock, five samples ==
SELECT extract(epoch FROM clock_timestamp()) - (value->>'beat_at')::float8 AS age_s,
       value->>'state' AS recorded_state, value->>'runtime_id' AS runtime
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT pg_sleep(7);
SELECT extract(epoch FROM clock_timestamp()) - (value->>'beat_at')::float8 AS age_s
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT pg_sleep(7);
SELECT extract(epoch FROM clock_timestamp()) - (value->>'beat_at')::float8 AS age_s
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT pg_sleep(7);
SELECT extract(epoch FROM clock_timestamp()) - (value->>'beat_at')::float8 AS age_s
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT pg_sleep(7);
SELECT extract(epoch FROM clock_timestamp()) - (value->>'beat_at')::float8 AS age_s
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == C5. Audrey and Derek recorded heartbeats ==
SELECT agent_id, state, left(activity, 80) AS activity, last_heartbeat_at,
       now() - last_heartbeat_at AS hb_age
  FROM agent_status ORDER BY agent_id;
