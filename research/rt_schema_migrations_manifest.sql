-- APPLIED-MIGRATION IMMUTABILITY MANIFEST (read only).
--
-- One line per migration production has applied, in `sha256sum` format
-- ("<content_sha>  <version>"), exactly as scripts/migrate.py recorded it
-- when the file ran (migrate.py: INSERT ... content_sha at apply time; a
-- recorded hash is never rewritten). backend/migrations/
-- APPLIED_MIGRATIONS.sha256 is these lines verbatim, so
--   cd backend/migrations && grep -v '^#' APPLIED_MIGRATIONS.sha256 | sha256sum -c
-- verifies the build against production's own record.
--
-- Refresh after a release applies new migrations: dispatch research-sql
-- with this file, take the lines above the manifest's last version, append
-- them. Never edit an existing line. A NULL hash would be a version applied
-- before hashing that no boot has re-hashed since -- it is reported, never
-- adopted into the manifest.
\echo == manifest lines (sha256sum format) ==
SELECT coalesce(content_sha, 'NULL') || '  ' || version AS manifest_line
  FROM schema_migrations
 ORDER BY version;
\echo == totals ==
SELECT count(*)            AS applied,
       count(content_sha)  AS hashed,
       min(version)        AS first_version,
       max(version)        AS frontier,
       max(applied_at)     AS last_applied_at
  FROM schema_migrations;
