-- READ-ONLY. WHAT FILLED THE 25 GB DISK (outage of 2026-09-30).
--
-- After recovery (storage raised to 50 GB, 01:23:46Z on Oct 1) the database
-- reports 23 GB. The largest relations were notification_outbox 8.8 GB,
-- trades 6.7 GB, pmus_activity_archive 2.6 GB. This file asks, per large
-- table: how many live and dead rows, how big a row is, how fast it grew per
-- day, and how much is index or TOAST rather than heap. It writes nothing
-- and proposes nothing for deletion; retention is an owner decision.
--
-- D1 relation sizes split into heap / indexes / TOAST, with live and dead
--    tuple counts and the last vacuum/autovacuum
-- D2 notification_outbox: rows by kind and sent state, payload size, daily
--    growth over the last 30 days
-- D3 trades: daily row growth over the last 30 days, and extent
-- D4 the whole database size

\echo '== D1 · relation sizes (top 15), live/dead tuples, vacuum =='
SELECT s.relname,
       pg_size_pretty(pg_total_relation_size(s.relid)) AS total,
       pg_size_pretty(pg_relation_size(s.relid)) AS heap,
       pg_size_pretty(pg_indexes_size(s.relid)) AS indexes,
       pg_size_pretty(GREATEST(pg_total_relation_size(s.relid) - pg_relation_size(s.relid)
                               - pg_indexes_size(s.relid), 0)) AS toast_and_other,
       s.n_live_tup, s.n_dead_tup,
       s.last_vacuum, s.last_autovacuum
  FROM pg_stat_user_tables s
 ORDER BY pg_total_relation_size(s.relid) DESC
 LIMIT 15;

\echo '== D2a · notification_outbox by kind / sent / collapsed =='
SELECT kind, sent, collapsed, count(*) AS rows,
       pg_size_pretty(sum(pg_column_size(payload))::bigint) AS payload_bytes,
       round(avg(pg_column_size(payload))) AS avg_payload_bytes,
       min(created_at) AS oldest, max(created_at) AS newest
  FROM notification_outbox
 GROUP BY 1, 2, 3
 ORDER BY rows DESC;

\echo '== D2b · notification_outbox daily growth (last 30 days) =='
SELECT date_trunc('day', created_at)::date AS day, count(*) AS rows,
       pg_size_pretty(sum(pg_column_size(payload))::bigint) AS payload_bytes
  FROM notification_outbox
 WHERE created_at > now() - interval '30 days'
 GROUP BY 1 ORDER BY 1 DESC;

\echo '== D3a · trades daily growth (last 30 days) =='
SELECT date_trunc('day', ts)::date AS day, count(*) AS rows
  FROM trades
 WHERE ts > now() - interval '30 days'
 GROUP BY 1 ORDER BY 1 DESC;

\echo '== D3b · trades extent =='
SELECT count(*) AS rows, min(ts) AS oldest, max(ts) AS newest FROM trades;

\echo '== D4 · database =='
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
