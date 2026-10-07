-- Applied-migration immutability manifest: every applied version and the
-- content hash recorded when it ran (read only).
SELECT version || ' ' || coalesce(content_sha, 'NULL') AS manifest_line
  FROM schema_migrations
 ORDER BY version;
