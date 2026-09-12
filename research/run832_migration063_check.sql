-- MIGRATION 063 -- the one-time verification. Read-only.
--
-- 063 applied unintentionally: it rode the accidental deploy of 14172bc, which
-- went live on sportsassets-api at 17:23:55Z. The owner accepted the schema in
-- place and asked for a single check, not a standing one, so this file exists to
-- be run ONCE and then left alone unless evidence changes.
--
-- Schema presence is not experiment activation. These tables existing changes
-- nothing about whether anything is collected: that is governed by
-- RN1_OBSERVABILITY_SHADOW (false) and RN1_OBSERVABILITY_SUBJECT_WHALE_ID
-- (unset), and the worker parks unless BOTH are set.

\echo == 1. THE NEW TABLES EXIST AND ARE EMPTY ==
SELECT 'rn1_obs_plan'        AS tbl, count(*) AS rows FROM rn1_obs_plan
UNION ALL SELECT 'rn1_obs_samples',     count(*) FROM rn1_obs_samples
UNION ALL SELECT 'rn1_obs_feed_events', count(*) FROM rn1_obs_feed_events
ORDER BY tbl;

\echo
\echo == 2. THE V1 TABLES ARE UNCHANGED (counts as sealed) ==
SELECT 'rn1_obs_events' AS tbl, count(*) AS rows, 12535 AS sealed FROM rn1_obs_events
UNION ALL SELECT 'rn1_obs_snapshots',   count(*), 125270 FROM rn1_obs_snapshots
UNION ALL SELECT 'rn1_obs_transitions', count(*),  25062 FROM rn1_obs_transitions
UNION ALL SELECT 'rn1_obs_clock_sync',  count(*),      3 FROM rn1_obs_clock_sync
ORDER BY tbl;

\echo
\echo == 3. THE 063 COLUMNS ARE PRESENT AND NULL ON EVERY V1 ROW ==
-- NULL is the correct value: RUN83_ACTIVATION_FAILED_V1 had no subject filter,
-- so there is no subject to record. A non-NULL here would mean something wrote
-- to the sealed cohort.
SELECT count(*)                                                AS v1_rows,
       count(subject_whale_id)                                 AS non_null_subject,
       count(canonical_was_insert)                             AS non_null_was_insert,
       count(admission_monotonic)                              AS non_null_admission,
       count(preregistration_version)                          AS non_null_prereg
FROM rn1_obs_events;

\echo
\echo == 4. APPEND-ONLY TRIGGERS ON ALL SEVEN TABLES ==
SELECT event_object_table AS on_table, trigger_name,
       string_agg(event_manipulation, '/' ORDER BY event_manipulation) AS on_event
FROM information_schema.triggers
WHERE event_object_schema = 'public' AND event_object_table LIKE 'rn1\_obs\_%'
GROUP BY 1, 2 ORDER BY 1;

\echo
\echo == 5. THE MIGRATION LEDGER: 062 unchanged, 063 dated ==
SELECT version, applied_at
FROM schema_migrations
WHERE version IN ('062_rn1_observability.sql', '063_rn1_observability_v2.sql')
ORDER BY version;

\echo
\echo == 6. THE PAUSE ==
SELECT key, left(value::text, 80) AS value
FROM ingestion_state
WHERE key IN ('mirror_live', 'premap_live', 'workers_boot')
ORDER BY key;
