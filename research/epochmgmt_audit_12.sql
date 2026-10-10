-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 12:
-- is the paper pass recording books and valuations now (the Adriana census and Xavier packets read them).
\echo == A newest recorded paper books and how many in the last windows
SELECT max(observed_at) AS newest_observation,
       count(*) FILTER (WHERE observed_at >= now() - interval '15 minutes') AS last_15m,
       count(*) FILTER (WHERE observed_at >= now() - interval '60 minutes') AS last_60m,
       count(*) FILTER (WHERE observed_at >= now() - interval '60 minutes' AND error IS NULL) AS last_60m_error_free,
       count(*) FILTER (WHERE observed_at >= now() - interval '24 hours') AS last_24h
  FROM paper_book_observations;

\echo == B paper session health: last pass, passes and errors
SELECT session_id, heartbeat_at, passes, errors, left((last_pass)::text, 300) AS last_pass
  FROM paper_session_health ORDER BY heartbeat_at DESC LIMIT 3;

\echo == C newest paper decisions by verdict, last 24 hours
SELECT verdict, count(*) AS decisions, max(decided_at) AS newest
  FROM paper_decisions WHERE account_id = 'paper_acct_main' AND decided_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo == D newest recorded PMUS book per hour for the last 12 hours
SELECT date_trunc('hour', observed_at) AS hour_utc, count(*) AS reads, count(*) FILTER (WHERE error IS NULL) AS error_free
  FROM paper_book_observations WHERE observed_at >= now() - interval '12 hours'
 GROUP BY 1 ORDER BY 1;
