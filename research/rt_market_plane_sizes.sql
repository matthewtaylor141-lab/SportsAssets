-- Market plane memory census (read only): the populations the dedicated plane
-- loads (registry, rules, Kalshi books/fixtures, refdata receipts) and their
-- payload sizes, to attribute its 1.4-2.0 GB resident set.
\echo === table sizes ===
SELECT relname, n_live_tup AS est_rows, pg_size_pretty(pg_total_relation_size(relid)) AS total
  FROM pg_stat_user_tables
 WHERE relname LIKE 'market_plane%' OR relname LIKE 'kalshi_%' OR relname LIKE 'ump_%'
    OR relname LIKE 'pmx_%' OR relname LIKE 'institutional_%'
 ORDER BY pg_total_relation_size(relid) DESC LIMIT 40;
\echo === registry by venue / status ===
SELECT venue, count(*) AS rows FROM market_plane_registry GROUP BY 1 ORDER BY 2 DESC;
\echo === registry average row width (bytes) ===
SELECT avg(pg_column_size(r.*))::int AS avg_row_bytes, max(pg_column_size(r.*)) AS max_row_bytes,
       pg_size_pretty(sum(pg_column_size(r.*))::bigint) AS sum_bytes
  FROM market_plane_registry r;
\echo === rules average width ===
SELECT count(*) AS rows, avg(pg_column_size(r.*))::int AS avg_row_bytes,
       pg_size_pretty(sum(pg_column_size(r.*))::bigint) AS sum_bytes FROM market_plane_rules r;
\echo === kalshi books / fixtures ===
SELECT 'kalshi_books_current' AS t, count(*) AS rows, pg_size_pretty(sum(pg_column_size(b.*))::bigint) AS sum_bytes FROM kalshi_books_current b
UNION ALL SELECT 'kalshi_fixtures_current', count(*), pg_size_pretty(sum(pg_column_size(f.*))::bigint) FROM kalshi_fixtures_current f;
\echo === plane heartbeats (detail sizes only) ===
SELECT service, status, beat_at, length(detail::text) AS detail_chars
  FROM service_heartbeats WHERE service IN ('market_plane','universal_market_plane','kalshi_ws_market_data','kalshi_market_data');
\echo === refdata / catalogue ingestion_state receipt sizes ===
SELECT key, length(value::text) AS chars FROM ingestion_state
 WHERE key ILIKE '%refdata%' OR key ILIKE '%market_plane%' OR key ILIKE '%ump%' OR key ILIKE '%kalshi%' OR key ILIKE '%catalogue%'
 ORDER BY 2 DESC LIMIT 30;
