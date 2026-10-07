-- Red team migration guard: production's recorded content hash for each
-- applied migration the guard reports as edited in place (read only).
SELECT version, content_sha, applied_at
  FROM schema_migrations
 WHERE version IN ('126_funded_inventory_exits_and_fees.sql',
                   '315_red_team_closeout.sql', '314_kalshi_canonical_venue.sql')
 ORDER BY version;
