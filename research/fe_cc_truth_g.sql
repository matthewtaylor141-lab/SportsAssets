-- READ ONLY. Frontend audit part G: what the Karen -> Xavier challenge
-- activity on the Command tape is made of. SELECT statements only.
\echo == G1. challenges in the last 24 h by target, detector and record kind ==
SELECT target_agent, detector, target_kind, state,
       count(*) AS n, count(DISTINCT target_id) AS distinct_targets,
       min(record_at) AS oldest_record, max(record_at) AS newest_record,
       max(challenged_at) AS newest_challenge
  FROM karen_challenges
 WHERE challenged_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3, 4 ORDER BY n DESC LIMIT 15;

\echo == G2. newest three challenges ==
SELECT challenge_id, target_agent, target_kind, left(claim, 160) AS claim, severity, state,
       record_at, challenged_at
  FROM karen_challenges ORDER BY challenged_at DESC LIMIT 3;

\echo == G3. total challenges and how many target a record older than 24 h at challenge time ==
SELECT count(*) AS total_24h,
       count(*) FILTER (WHERE challenged_at - record_at > interval '24 hours') AS on_records_older_than_24h
  FROM karen_challenges WHERE challenged_at > now() - interval '24 hours';
