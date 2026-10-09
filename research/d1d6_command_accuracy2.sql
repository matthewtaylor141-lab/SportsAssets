-- READ-ONLY. SMALL LIVE PILOT V1, deliverables 1 and 6, second pass: what the
-- floor would show for Adriana, Derek and Archer and why. Every statement is a
-- SELECT.

\echo E1 ADRIANA census passes (scan_id not adr-claims) in the last 2 h by status, books read and fresh
SELECT status, count(*) AS passes, sum(markets_read) AS markets_read, sum(books_fresh) AS books_fresh,
       sum(structures_considered) AS structures, sum(opportunities) AS opportunities,
       min(finished_at) AS first_finish, max(finished_at) AS newest_finish
  FROM adriana_arb_scans
 WHERE scan_id NOT LIKE 'adr-claims-%' AND finished_at >= now() - interval '2 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo E2 ADRIANA newest 4 census passes (the floor last pass) and the gap between passes
SELECT scan_id, started_at, finished_at, status, left(why, 100) AS why, markets_read, books_fresh,
       structures_considered, opportunities, refusals_total,
       round(extract(epoch FROM now() - finished_at)::numeric, 1) AS age_s
  FROM adriana_arb_scans WHERE scan_id NOT LIKE 'adr-claims-%' ORDER BY finished_at DESC LIMIT 4;

\echo E3 DEREK status row and the paper pass record (waiting_on, last pass steps)
SELECT agent_id, state, activity, waiting_on::text AS waiting_on, last_heartbeat_at, last_run_started_at, last_run_finished_at
  FROM agent_status WHERE agent_id IN ('DEREK', 'XAVIER', 'AUDREY');
SELECT key, left(value::text, 1800) AS value FROM ingestion_state WHERE key = 'paper_session_last_pass';

\echo E4 DECISIONS after 17:45 UTC per minute with verdicts and the top refusal
SELECT date_trunc('minute', decided_at) AS minute, count(*) AS decisions,
       count(*) FILTER (WHERE verdict = 'ENTER') AS enter,
       mode() WITHIN GROUP (ORDER BY coalesce(refusal, verdict)) AS top_reason
  FROM paper_decisions WHERE decided_at >= timestamptz '2026-10-09 17:45:00+00'
 GROUP BY 1 ORDER BY 1;

\echo E5 ARCHER estimates per hour over 8 h and the status row error counters
SELECT date_trunc('hour', estimated_at) AS hour, count(*) AS estimates, max(estimated_at) AS newest
  FROM eddie_execution_estimates WHERE estimated_at >= now() - interval '8 hours' GROUP BY 1 ORDER BY 1;
SELECT agent_id, runs, errors, last_error, last_run_started_at, last_run_finished_at, last_run_elapsed_s
  FROM agent_status WHERE agent_id IN ('ARCHER', 'KAREN', 'SCOUT', 'ADRIANA', 'EDDIE');

\echo E7 PINNAPI FEED heartbeat summary (connected, newest frame), first 600 chars
SELECT key, left(value::text, 600) AS value FROM ingestion_state WHERE key IN ('pinnapi_feed_last');

\echo E8 db now
SELECT now();
