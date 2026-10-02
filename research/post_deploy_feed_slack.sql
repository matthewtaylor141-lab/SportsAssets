-- After the candidate-11/12 deploys (read-only): feed scope, census, the
-- fresh share of Xavier's held-position reviews WITH its denominator, the
-- feed provenance of fresh ones, and the newest Slack deliveries.
\echo '== P0 · feed scope row and heartbeat census (truncated) =='
SELECT key, left(value::text, 1600) AS value FROM ingestion_state
 WHERE key IN ('pinnapi_feed_scope', 'pinnapi_feed_last');
\echo '== P1 · Xavier reviews by measure source, last 20 min =='
SELECT measure->>'source' AS source, (measure->>'stale')::boolean AS stale,
       coalesce(measure->>'feed_refusal', '') AS feed_refusal,
       count(*) AS reviews, count(DISTINCT group_id) AS positions,
       min(reviewed_at) AS first, max(reviewed_at) AS last
  FROM paper_xavier_reviews WHERE reviewed_at > now() - interval '20 minutes'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;
\echo '== P2 · fresh feed-backed reviews: provenance sample =='
SELECT review_id, group_id, reviewed_at,
       measure->'feed'->>'feed_event_id' AS feed_event_id,
       measure->'feed'->>'designation' AS designation,
       measure->'feed'->>'quote_age_s' AS quote_age_s,
       measure->'feed'->>'epoch' AS epoch, measure->>'p' AS p
  FROM paper_xavier_reviews
 WHERE reviewed_at > now() - interval '20 minutes'
   AND measure->>'source' = 'PINNAPI_FEED_CURRENT'
 ORDER BY reviewed_at DESC LIMIT 10;
\echo '== P3 · newest Slack deliveries =='
SELECT agent, state, error_code, left(source_key, 44) AS source, requested_by,
       thread_ts, slack_ts, created_at,
       left(regexp_replace(coalesce(question, ''), '\s+', ' ', 'g'), 90) AS question,
       left(regexp_replace(coalesce(answer, ''), '\s+', ' ', 'g'), 160) AS answer
  FROM agent_slack_delivery WHERE created_at > now() - interval '40 minutes'
 ORDER BY created_at DESC LIMIT 20;
