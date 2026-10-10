-- RC6.3 paper pass stall: the cost of coverage_integrity's reads (SELECT only).
\echo C1 coverage_integrity watermark and the newest coverage snapshot written
SELECT value, to_timestamp((value->>'at')::float) AS last_at FROM ingestion_state WHERE key = 'coverage_integrity_last';
SELECT max(computed_at) AS newest_snapshot FROM coverage_funnel_snapshots;
\echo C2 ext_candidate_outcomes size, rows per day over 16 days
SELECT pg_size_pretty(pg_total_relation_size('ext_candidate_outcomes')) AS total_size,
       (SELECT reltuples::bigint FROM pg_class WHERE relname = 'ext_candidate_outcomes') AS est_rows;
SELECT date_trunc('day', cycle_at) AS day, count(*) AS rows,
       count(DISTINCT provider_event_id) AS events, count(DISTINCT us_market_slug) AS slugs
  FROM ext_candidate_outcomes WHERE cycle_at >= now() - interval '16 days' GROUP BY 1 ORDER BY 1;
\echo C3 indexes on ext_candidate_outcomes
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'ext_candidate_outcomes' ORDER BY 1;
\echo C4 pg_stat_statements for statements over ext_candidate_outcomes (calls, mean and max ms)
SELECT calls, round(mean_exec_time::numeric, 0) AS mean_ms, round(max_exec_time::numeric, 0) AS max_ms,
       round(total_exec_time::numeric / 1000, 0) AS total_s, rows,
       left(regexp_replace(query, '\s+', ' ', 'g'), 160) AS query
  FROM pg_stat_statements WHERE query ILIKE '%ext_candidate_outcomes%'
 ORDER BY total_exec_time DESC LIMIT 15;
\echo C5 one EVENT_LEAGUE CTE alone, timed (the m half, today window)
\timing on
SELECT count(*) FROM (SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key
  FROM ext_candidate_outcomes WHERE provider_event_id IS NOT NULL
   AND cycle_at >= date_trunc('day', now()) - interval '14 days' AND cycle_at < date_trunc('day', now()) + interval '2 days'
 ORDER BY provider_event_id, cycle_at DESC) m;
SELECT count(*) FROM (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
  FROM ext_candidate_outcomes WHERE us_market_slug IS NOT NULL
   AND cycle_at >= date_trunc('day', now()) - interval '14 days' AND cycle_at < date_trunc('day', now()) + interval '2 days'
 ORDER BY us_market_slug, cycle_at DESC) ms;
\timing off
\echo C6 plan of the m CTE (EXPLAIN without ANALYZE)
EXPLAIN SELECT DISTINCT ON (provider_event_id) provider_event_id, sport_key
  FROM ext_candidate_outcomes WHERE provider_event_id IS NOT NULL
   AND cycle_at >= now() - interval '15 days' AND cycle_at < now() + interval '1 day'
 ORDER BY provider_event_id, cycle_at DESC;
\echo C7 work_mem and the pass heartbeats
SHOW work_mem;
SELECT value->>'refusal' AS refusal, to_timestamp((value->>'written_at')::float) AS written_at FROM ingestion_state WHERE key = 'paper_session_last_pass';
