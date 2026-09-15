-- RUN 83 DEPLOYMENT GATE -- passive, read-only, no order path touched.
--
-- Answers checks 2, 4 and 5 of the owner's post-restart list from the database
-- itself rather than from the deploy's exit status:
--
--   2. mirror_live is false
--   4. migration 062 applied successfully
--   5. all four observability tables exist and hold zero run 83 observations
--
-- A ZERO ROW COUNT IS ONLY EVIDENCE BESIDE AN EXISTS. "no rows" from a table
-- that is not there and "no rows" from a table that is there and empty read the
-- same in a count, and only one of them means the migration landed. So the
-- table census is printed first, from the catalogue, and the counts second.

\echo == 1. THE MIGRATION LEDGER: the newest ten, 062 among them ==
SELECT version, applied_at
FROM schema_migrations
ORDER BY version DESC
LIMIT 10;

\echo
\echo == 2. IS 062 THERE AT ALL (an explicit yes/no, not an absence) ==
SELECT
    EXISTS (SELECT 1 FROM schema_migrations WHERE version = '062_rn1_observability.sql')
        AS migration_062_applied,
    (SELECT applied_at FROM schema_migrations WHERE version = '062_rn1_observability.sql')
        AS applied_at;

\echo
\echo == 3. THE FOUR TABLES, FROM THE CATALOGUE, WITH THEIR COLUMN COUNTS ==
SELECT t.table_name,
       (SELECT count(*) FROM information_schema.columns c
         WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name) AS columns
FROM information_schema.tables t
WHERE t.table_schema = 'public'
  AND t.table_name IN ('rn1_obs_events', 'rn1_obs_snapshots',
                       'rn1_obs_transitions', 'rn1_obs_clock_sync')
ORDER BY t.table_name;

\echo
\echo == 4. THE APPEND-ONLY TRIGGER, named on each of the four ==
SELECT event_object_table AS on_table, trigger_name, action_timing,
       string_agg(event_manipulation, '/' ORDER BY event_manipulation) AS on_event
FROM information_schema.triggers
WHERE event_object_schema = 'public'
  AND event_object_table IN ('rn1_obs_events', 'rn1_obs_snapshots',
                             'rn1_obs_transitions', 'rn1_obs_clock_sync')
GROUP BY event_object_table, trigger_name, action_timing
ORDER BY on_table, trigger_name;

\echo
\echo == 5. ZERO OBSERVATIONS: the row count of each, beside its newest row ==
SELECT 'rn1_obs_events' AS tbl, count(*) AS rows, max(receipt_wall)::text AS newest
  FROM rn1_obs_events
UNION ALL
SELECT 'rn1_obs_snapshots', count(*), max(response_wall)::text FROM rn1_obs_snapshots
UNION ALL
SELECT 'rn1_obs_transitions', count(*), max(at_wall)::text FROM rn1_obs_transitions
UNION ALL
SELECT 'rn1_obs_clock_sync', count(*), max(observed_wall)::text FROM rn1_obs_clock_sync
ORDER BY tbl;

\echo
\echo == 6. THE PAUSE, AND WHICH COMMIT THE WORKERS BOOTED ON ==
SELECT key, left(value::text, 200) AS value
FROM ingestion_state
WHERE key IN ('mirror_live', 'premap_live', 'workers_boot', 'mirror_loss_stop',
              'live_trading_paused', 'mapping_quarantine')
ORDER BY key;

\echo
\echo == 7. THE BOOK STATE ACROSS THE RESTART (before: 7 live, 1 frozen id 1200) ==
SELECT state, count(*) AS books, max(updated_at)::text AS last_read
FROM mirror_books
WHERE state <> 'closed'
GROUP BY state
ORDER BY state;

\echo
\echo == 8. ANY ORDER AT ALL SINCE THE DEPLOY BEGAN (must be none) ==
SELECT count(*) AS mirror_orders_since_deploy,
       COALESCE(max(placed_at)::text, 'none') AS newest
FROM mirror_orders
WHERE placed_at > TIMESTAMPTZ '2026-09-12 14:18:00+00';

\echo
\echo == 9. AND ON THE LIVE-ORDER SIDE TOO ==
SELECT count(*) AS live_orders_since_deploy,
       COALESCE(max(placed_at)::text, 'none') AS newest
FROM live_orders
WHERE placed_at > TIMESTAMPTZ '2026-09-12 14:18:00+00';
