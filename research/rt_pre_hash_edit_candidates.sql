-- PRE-HASH-ERA MIGRATION EDITS: DID THE EDITED VERSION RUN? (read only)
--
-- Content hashes were first recorded by 7483f6e6 (2026-09-24 22:51 UTC).
-- A version applied before that got content_sha NULL, and the first boot
-- after it ADOPTED the then-current file as the baseline (scripts/migrate.py,
-- the `seen is None` branch). So for files edited in git after their first
-- commit and before hashing, production's recorded hash equalling the file
-- proves nothing about which version executed. The catalogue does: each row
-- below is an object that exists ONLY in the edited (final) version, or
-- (expect_present = false) ONLY in the earlier one.
\echo == applied_at of the candidates (compare with the edit commits) ==
SELECT version, applied_at, content_sha
  FROM schema_migrations
 WHERE version IN ('001_init.sql',
                   '062_rn1_observability.sql',
                   '093_bettor_live_observation.sql',
                   '094_bettor_desk.sql',
                   '116_one_entry_position_per_exposure.sql',
                   '117_entry_lane_evidence_and_calibration.sql')
 ORDER BY version;
\echo == columns only the final version creates (expect_present) ==
SELECT w.version, w.t AS table_name, w.c AS column_name, w.expect_present,
       EXISTS (SELECT 1 FROM information_schema.columns ic
                WHERE ic.table_schema = 'public'
                  AND ic.table_name = w.t AND ic.column_name = w.c) AS present
  FROM (VALUES
    ('001', 'whale_sport_stats',   'time_window',              true),
    ('062', 'rn1_obs_snapshots',   'observation_channel',      true),
    ('062', 'rn1_obs_snapshots',   'transport',                true),
    ('062', 'rn1_obs_snapshots',   'feed_identity',            true),
    ('062', 'rn1_obs_snapshots',   'stream_receive_wall',      true),
    ('062', 'rn1_obs_snapshots',   'stream_receive_monotonic', true),
    ('093', 'bettor_live_journal', 'record_key',               true),
    ('093', 'bettor_live_journal', 'boot_id',                  true),
    ('093', 'bettor_live_cursor',  'event_at',                 true),
    ('093', 'bettor_live_cursor',  'settle_failures',          true),
    ('093', 'bettor_live_ledger',  'boot_id',                  true),
    ('094', 'bettor_desk_state',   'cursor_event_id',          true),
    ('094', 'bettor_desk_state',   'cursor_decision_ts',       false),
    ('094', 'bettor_desk_state',   'cursor_decision_id',       false),
    ('117', 'external_source_calibration', 'within_tolerance', true)
  ) AS w(version, t, c, expect_present)
 ORDER BY 1, 2, 3;
\echo == constraints and the unique key only the final versions create ==
SELECT conrelid::regclass AS table_name, conname
  FROM pg_constraint
 WHERE conname IN ('rn1_obs_snapshots_channel_ck',
                   'rn1_obs_snapshots_transport_ck')
 ORDER BY 2;
SELECT tablename, indexdef
  FROM pg_indexes
 WHERE schemaname = 'public' AND tablename = 'bettor_live_journal'
 ORDER BY 1, 2;
