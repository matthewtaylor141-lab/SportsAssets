-- Analytics OOM census (read only): how much the per-cycle settle pass
-- materialises from pmus_activity_archive, and how many slugs it needs.
\echo === resolution rows in pmus_activity_archive ===
SELECT count(*) AS rows,
       pg_size_pretty(sum(pg_column_size(payload->'positionResolution'))::bigint) AS pr_bytes,
       pg_size_pretty(sum(pg_column_size(payload))::bigint) AS payload_bytes,
       count(DISTINCT lower(payload->'positionResolution'->>'marketSlug')) AS slugs
  FROM pmus_activity_archive
 WHERE payload->>'type' = 'ACTIVITY_TYPE_POSITION_RESOLUTION';
\echo === archive total ===
SELECT count(*) AS rows, pg_size_pretty(pg_total_relation_size('pmus_activity_archive')) AS table_size
  FROM pmus_activity_archive;
\echo === live_orders the settle pass groups (status filled, us_market_slug) ===
SELECT count(*) AS rows, count(DISTINCT lower(us_market_slug)) AS slugs
  FROM live_orders WHERE us_market_slug IS NOT NULL AND status = 'filled';
