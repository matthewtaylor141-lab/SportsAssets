\echo == SHADOW RUNTIME PROBE ==
\echo
\echo Owner directive 2026-09-19 section 1: "Return observed -- not
\echo source-code inferred". This file is how the observation is made.
\echo It is a READ. It writes nothing, and the workflow that runs it
\echo refuses any file containing a mutating keyword.
\echo
\echo EVERY SHADOW TABLE IS REACHED THROUGH to_regclass, never named
\echo directly in a FROM. A migration that has not applied yet is the
\echo single most likely observation this probe will make, and a probe
\echo that aborted on that fact would report nothing at all.
\echo

\echo -- 1. SHADOW_MIGRATIONS_APPLIED ------------------------------------
SELECT 'migration', version, applied_at::text
  FROM schema_migrations
 WHERE version LIKE '068%' OR version LIKE '069%' OR version LIKE '070%'
 ORDER BY version;

\echo -- 1b. the newest migration of any kind, for context ---------------
SELECT 'newest_migration', version, applied_at::text
  FROM schema_migrations
 ORDER BY applied_at DESC
 LIMIT 3;

\echo -- 2. DO THE SHADOW RELATIONS EXIST -------------------------------
SELECT 'relation', t.name,
       COALESCE(to_regclass(t.name)::text, 'ABSENT')
  FROM (VALUES
          ('shadow_decisions'), ('shadow_executions'),
          ('shadow_positions'), ('shadow_position_events'),
          ('shadow_scores'), ('shadow_specialists'),
          ('shadow_disagreements'), ('rn1_observations'),
          ('shadow_market_states'), ('shadow_policy_versions')
       ) AS t(name)
 ORDER BY t.name;

\echo -- 3. THE APPEND-ONLY TRIGGERS, from the catalog -------------------
SELECT 'trigger', c.relname, t.tgname
  FROM pg_trigger t
  JOIN pg_class c ON c.oid = t.tgrelid
 WHERE NOT t.tgisinternal
   AND t.tgname LIKE '%immutable%'
 ORDER BY c.relname, t.tgname;

\echo -- 4. THE CONSTRAINTS THE STORE PREFLIGHT REQUIRES -----------------
SELECT 'constraint', conname
  FROM pg_constraint
 WHERE conname IN ('shadow_decisions_shadow_only', 'shadow_decisions_lane',
                   'shadow_decisions_lineage', 'shadow_decisions_rn1_no_belief',
                   'shadow_decisions_rn1_observed',
                   'shadow_decisions_policy_frozen',
                   'rn1_obs_correction_references')
 ORDER BY conname;

\echo -- 5. THE RN1 DEDUPE INDEX ----------------------------------------
SELECT 'index', indexname, indexdef
  FROM pg_indexes
 WHERE indexname = 'rn1_observations_idem_idx';

\echo -- 6. lane NOT NULL WITH NO DEFAULT -------------------------------
SELECT 'lane_column', is_nullable,
       COALESCE(column_default, 'NO_DEFAULT')
  FROM information_schema.columns
 WHERE table_name = 'shadow_decisions' AND column_name = 'lane';

\echo -- 7. RUNTIME: the worker heartbeats ------------------------------
\echo A heartbeat is the service saying it ran, with its own clock.
\echo Absent means the loop has never beaten on this deployment.
SELECT 'heartbeat', service, status,
       beat_at::text,
       round(EXTRACT(EPOCH FROM (now() - beat_at)))::text AS age_s,
       left(detail::text, 400)
  FROM service_heartbeats
 WHERE service IN ('chain_listener', 'poller', 'shadow_rn1', 'reconciler',
                   'mirror_live', 'rn1_obs')
 ORDER BY service;

\echo -- 8. LAST_RN1_SOURCE_EVENT_AT ------------------------------------
\echo RN1's own most recent fill as the ledger holds it. This is the
\echo clock that says whether there has been anything to observe.
SELECT 'rn1_last_fill', max(t.ts)::text, count(*)::text
  FROM trades t
  JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
   AND t.ts > now() - interval '24 hours';

\echo -- 8b. RN1 fills since the shadow hook could have seen them --------
SELECT 'rn1_recent', t.ts::text, t.source, t.side,
       t.size::text, t.price::text, left(COALESCE(t.market_slug,'UNMAPPED'),48)
  FROM trades t
  JOIN whales w ON w.id = t.whale_id
 WHERE lower(w.username) = 'rn1'
 ORDER BY t.ts DESC
 LIMIT 10;

\echo == END OF PROBE ==
