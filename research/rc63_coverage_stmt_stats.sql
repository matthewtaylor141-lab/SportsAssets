-- RC6.3 paper pass stall: which statement over ext_candidate_outcomes is slow, by its tail text (SELECT only).
\echo S1 statements whose text contains the league CTE: calls, mean, max, stddev, block reads, temp blocks, tail text
SELECT queryid, calls, round(mean_exec_time::numeric, 0) AS mean_ms, round(max_exec_time::numeric, 0) AS max_ms,
       round(stddev_exec_time::numeric, 0) AS sd_ms, rows, shared_blks_hit, shared_blks_read, temp_blks_written,
       right(regexp_replace(query, '\s+', ' ', 'g'), 330) AS tail
  FROM pg_stat_statements
 WHERE query LIKE '%DISTINCT ON (provider_event_id) provider_event_id%'
 ORDER BY max_exec_time DESC LIMIT 12;
\echo S2 pg_stat_statements reset time and server version
SELECT stats_reset FROM pg_stat_statements_info;
SHOW server_version;
\echo S3 the slowest statements overall by max time (top 12), tail text
SELECT calls, round(mean_exec_time::numeric, 0) AS mean_ms, round(max_exec_time::numeric, 0) AS max_ms,
       left(regexp_replace(query, '\s+', ' ', 'g'), 200) AS head
  FROM pg_stat_statements ORDER BY max_exec_time DESC LIMIT 12;
