-- RC6.3 capability lane: why AGENT_CAPABILITY_REVIEW_V1 reviews fail in
-- execute()/finish(), whether the language-model endpoint the persona chat
-- calls is failing (three independent records), why no flow has been opened
-- since 2026-10-09 10:00Z, and the API-side waits. SELECT only.
\echo C1 capability review outcomes by day, kind, error, provider_mode, message, investigation
SELECT date_trunc('day', at) AS day, kind, coalesce(detail->>'error','-') AS error,
       coalesce(detail->>'provider_mode','-') AS pmode,
       (detail->>'message_id') IS NOT NULL AS has_msg,
       CASE WHEN jsonb_typeof(detail->'investigation')='array'
            THEN CASE WHEN jsonb_array_length(detail->'investigation')>0 THEN 'inv' ELSE 'no_inv' END
            ELSE 'no_inv' END AS inv,
       count(*) AS n, min(at) AS first_at, max(at) AS last_at
FROM agent_task_events
WHERE task_id LIKE 'capwork:%' AND kind IN ('REVIEW_INCOMPLETE','GENUINE_REVIEW')
GROUP BY 1,2,3,4,5,6 ORDER BY 1 DESC, 2, 7 DESC;

\echo C2 seconds from CLAIMED to the review outcome, by day, kind, error
SELECT date_trunc('day', x.at) AS day, x.kind, coalesce(x.detail->>'error','-') AS error, count(*) AS n,
       round(min(x.d)::numeric,1) AS min_s,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY x.d))::numeric,1) AS p50_s,
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY x.d))::numeric,1) AS p90_s,
       round(max(x.d)::numeric,1) AS max_s
FROM (SELECT e.at, e.kind, e.detail,
             extract(epoch FROM e.at - (SELECT max(c.at) FROM agent_task_events c
                                       WHERE c.task_id=e.task_id AND c.kind='CLAIMED' AND c.at<=e.at)) AS d
      FROM agent_task_events e
      WHERE e.task_id LIKE 'capwork:%' AND e.kind IN ('REVIEW_INCOMPLETE','GENUINE_REVIEW')) x
GROUP BY 1,2,3 ORDER BY 1 DESC, 2, 4 DESC;

\echo C3 persona replies to capability review requests by day, status, mode, failure, with seconds from the question row to the reply row
SELECT date_trunc('day', a.recorded_at) AS day, a.status, coalesce(a.provider->>'mode','-') AS mode,
       left(coalesce(a.provider->>'failure','-'),60) AS failure, count(*) AS n,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM a.recorded_at - u.recorded_at)))::numeric,1) AS p50_s,
       round(max(extract(epoch FROM a.recorded_at - u.recorded_at))::numeric,1) AS max_s,
       max(a.recorded_at) AS last_at
FROM agent_chat_messages a LEFT JOIN agent_chat_messages u ON u.message_id=a.in_reply_to
WHERE a.role='ASSISTANT' AND a.request_id LIKE 'capreview:%'
GROUP BY 1,2,3,4 ORDER BY 1 DESC, 5 DESC;

\echo C4 capability review questions and how many never got a reply row, by day
WITH r AS (SELECT DISTINCT in_reply_to FROM agent_chat_messages
           WHERE role='ASSISTANT' AND request_id LIKE 'capreview:%')
SELECT date_trunc('day', u.recorded_at) AS day, count(*) AS questions,
       count(*) FILTER (WHERE r.in_reply_to IS NULL) AS no_reply
FROM agent_chat_messages u LEFT JOIN r ON r.in_reply_to=u.message_id
WHERE u.role='USER' AND u.request_id LIKE 'capreview:%'
GROUP BY 1 ORDER BY 1 DESC;

\echo C5 idempotency rows for capability reviews by day, status, reply status, mode, failure
SELECT date_trunc('day', created_at) AS day, status, coalesce(response->>'status','-') AS reply_status,
       coalesce(response->'provider'->>'mode','-') AS mode,
       left(coalesce(response->'provider'->>'failure','-'),60) AS failure, count(*) AS n
FROM audrey_requests WHERE request_id LIKE 'capreview:%'
GROUP BY 1,2,3,4,5 ORDER BY 1 DESC, 6 DESC;

\echo C6 capability replies by day, mode and whether a cited fact is a paper record or an investigation item
SELECT date_trunc('day', a.recorded_at) AS day, coalesce(a.provider->>'mode','-') AS mode,
       EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(a.facts)='array' THEN a.facts ELSE '[]'::jsonb END) f
               WHERE left(coalesce(f->>'source',''),6)='paper_'
                  OR (f->>'source'='agent_tasks' AND left(coalesce(f->>'field',''),14)='investigation_')) AS grounded,
       count(*) AS n
FROM agent_chat_messages a
WHERE a.role='ASSISTANT' AND a.request_id LIKE 'capreview:%'
GROUP BY 1,2,3 ORDER BY 1 DESC, 4 DESC;

\echo C7 newest capability replies with the provider record (disclosure removed)
SELECT a.recorded_at, a.status, a.outcome, (a.provider - 'disclosure') AS provider,
       CASE WHEN jsonb_typeof(a.facts)='array' THEN jsonb_array_length(a.facts) END AS cited
FROM agent_chat_messages a
WHERE a.role='ASSISTANT' AND a.request_id LIKE 'capreview:%'
ORDER BY a.recorded_at DESC LIMIT 8;

\echo C8 second record: persona replies outside the capability loop (pages and Slack), last 12 days
SELECT date_trunc('day', recorded_at) AS day, coalesce(provider->>'mode','-') AS mode,
       left(coalesce(provider->>'failure','-'),60) AS failure, count(*) AS n, max(recorded_at) AS last_at
FROM agent_chat_messages
WHERE role='ASSISTANT' AND coalesce(request_id,'') NOT LIKE 'capreview:%'
  AND recorded_at > now() - interval '12 days'
GROUP BY 1,2,3 ORDER BY 1 DESC, 4 DESC;

\echo C9 third record: Audrey management chat answers, last 12 days
SELECT date_trunc('day', recorded_at) AS day, coalesce(outcome,'-') AS outcome,
       coalesce(provider->>'mode','-') AS mode, left(coalesce(provider->>'failure','-'),60) AS failure,
       count(*) AS n, max(recorded_at) AS last_at
FROM audrey_messages
WHERE role='AUDREY' AND recorded_at > now() - interval '12 days'
GROUP BY 1,2,3,4 ORDER BY 1 DESC, 5 DESC;

\echo C10 fourth record: Slack persona deliveries, last 12 days
SELECT date_trunc('day', created_at) AS day, state, coalesce(error_code,'-') AS error, count(*) AS n
FROM agent_slack_delivery WHERE created_at > now() - interval '12 days'
GROUP BY 1,2,3 ORDER BY 1 DESC, 4 DESC;

\echo C11 newest model-composed answer in each record
SELECT 'agent_chat_messages' AS source,
       max(recorded_at) FILTER (WHERE provider->>'mode'='LLM') AS newest_llm,
       max(recorded_at) AS newest_any
FROM agent_chat_messages WHERE role='ASSISTANT'
UNION ALL
SELECT 'audrey_messages', max(recorded_at) FILTER (WHERE provider->>'mode'='LLM'), max(recorded_at)
FROM audrey_messages WHERE role='AUDREY';

\echo C12 flow sources: rows, newest, rows since 2026-10-09 10:00Z, rows without a capability flow
WITH k AS (SELECT DISTINCT spec->>'source_key' AS sk FROM agent_tasks
           WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'account_id'='paper_acct_main')
SELECT 'handoff' AS src, count(*) AS rows_all, max(h.created_at) AS newest,
       count(*) FILTER (WHERE h.created_at > timestamptz '2026-10-09 10:00+00') AS since_10z,
       count(*) FILTER (WHERE k.sk IS NULL) AS without_flow
FROM paper_handoffs h LEFT JOIN k ON k.sk='handoff:'||h.handoff_id
WHERE h.account_id='paper_acct_main'
UNION ALL
SELECT 'recommendation_open', count(*), max(r.created_at),
       count(*) FILTER (WHERE r.created_at > timestamptz '2026-10-09 10:00+00'),
       count(*) FILTER (WHERE k.sk IS NULL)
FROM paper_recommendations r LEFT JOIN k ON k.sk='recommendation:'||r.recommendation_id
WHERE r.account_id='paper_acct_main' AND r.status NOT IN ('CLOSED','IMPROVED','NOT_IMPROVED')
UNION ALL
SELECT 'settlement', count(*), max(s.recorded_at),
       count(*) FILTER (WHERE s.recorded_at > timestamptz '2026-10-09 10:00+00'),
       count(*) FILTER (WHERE k.sk IS NULL)
FROM paper_settlements s LEFT JOIN k ON k.sk='settlement:'||s.settlement_id
WHERE s.account_id='paper_acct_main';

\echo C13 capability flows opened by day and source kind
SELECT date_trunc('day', created_at) AS day, split_part(spec->>'source_key',':',1) AS src,
       count(*) FILTER (WHERE spec->'dependencies' = '[]'::jsonb) AS flows, count(*) AS tasks,
       max(created_at) AS newest
FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1'
GROUP BY 1,2 ORDER BY 1 DESC, 2;

\echo C14 recommendations by status
SELECT status, count(*) AS n, max(created_at) AS newest
FROM paper_recommendations WHERE account_id='paper_acct_main' GROUP BY 1 ORDER BY 2 DESC;

\echo C15 API loop health rows, newest error first
SELECT loop_name, starts, successes, errors, last_success_at, last_error_at,
       left(coalesce(last_error,'-'),70) AS last_error, left(coalesce(commit_sha,'-'),8) AS sha
FROM runtime_loop_health WHERE process='api' ORDER BY last_error_at DESC NULLS LAST;

\echo C16 API event loop stalls persisted by the watchdog: summary
SELECT to_timestamp((value->>'written_at')::float8) AS written_at, value->'lag' AS lag, value->'gc' AS gc
FROM ingestion_state WHERE key='api.loop_stalls';

\echo C17 API event loop stalls persisted by the watchdog: ring
SELECT to_timestamp((e->>'at')::float8) AS at, e->>'loop_lag_s' AS lag_s, e->>'ended_lag_s' AS ended_s,
       e->>'watchdog_overrun_s' AS overrun_s, left(coalesce(e->>'task','-'),40) AS task,
       left(coalesce(e->>'coro','-'),60) AS coro, left(coalesce(e->'culprit'->>0,'-'),90) AS culprit
FROM ingestion_state s CROSS JOIN LATERAL jsonb_array_elements(s.value->'stalls') e
WHERE s.key='api.loop_stalls' ORDER BY 1 DESC;

\echo C18 server-side time of statements on ingestion_state
SELECT calls, round(mean_exec_time::numeric,2) AS mean_ms, round(max_exec_time::numeric,2) AS max_ms, rows,
       left(regexp_replace(query,'\s+',' ','g'),130) AS q
FROM pg_stat_statements WHERE query ILIKE '%ingestion_state%'
ORDER BY max_exec_time DESC LIMIT 15;

\echo C20 current backends by application, state and wait type
SELECT coalesce(nullif(application_name,''),'-') AS app, coalesce(state,'-') AS state,
       coalesce(wait_event_type,'-') AS wtype, count(*) AS n
FROM pg_stat_activity WHERE datname=current_database()
GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 30;

\echo C19 pg_stat_statements collected since
SELECT stats_reset FROM pg_stat_statements_info;
