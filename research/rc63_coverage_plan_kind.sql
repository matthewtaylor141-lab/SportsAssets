-- RC6.3 paper pass stall: generic vs custom plan executions of the coverage statements (PostgreSQL 18
-- pg_stat_statements generic_plan_calls / custom_plan_calls), SELECT only.
\echo K1 the league-CTE statements: calls, generic plan calls, custom plan calls, mean and max ms
SELECT queryid, calls, round(mean_exec_time::numeric, 0) AS mean_ms,
       round(max_exec_time::numeric, 0) AS max_ms, round(total_exec_time::numeric / 1000, 0) AS total_s,
       right(regexp_replace(query, '\s+', ' ', 'g'), 120) AS tail
  FROM pg_stat_statements
 WHERE query LIKE '%DISTINCT ON (provider_event_id) provider_event_id%'
 ORDER BY total_exec_time DESC LIMIT 10;
\echo K2 last ANALYZE / autoanalyze of the tables in those statements
SELECT relname, n_live_tup, last_analyze, last_autoanalyze, n_mod_since_analyze
  FROM pg_stat_user_tables
 WHERE relname IN ('ext_candidate_outcomes', 'external_valuations', 'execution_intents', 'paper_decisions',
                   'paper_orders', 'paper_fills', 'execmirror_orders')
 ORDER BY relname;
