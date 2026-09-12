-- RUN83_ACTIVATION_FAILED_V1 -- SEAL VERIFICATION. Read-only.
--
-- Re-derives the four identity digests in research/RUN83_ACTIVATION_FAILED_V1_
-- MANIFEST.md by an INDEPENDENTLY FORMULATED expression: same specification,
-- different construction. The manifest's digests came from string_agg over a
-- concatenation; these come from an ordered subquery feeding string_agg with an
-- explicitly materialised separator, so a mistake in one construction does not
-- reproduce itself in the other.
--
-- A DISAGREEMENT MEANS THE PRODUCTION ROWS HAVE CHANGED SINCE SEALING. The
-- append-only triggers on all four tables should make that impossible, so a
-- mismatch is evidence about the triggers, not about the hash.
--
-- Statement 5 is the real test: it prints the expected value beside the derived
-- one and a boolean, so the answer does not depend on a human comparing two
-- 64-character strings by eye.

\echo == 1. THE INTERVAL AND THE PROCESS, as sealed ==
SELECT min(receipt_wall)::text          AS first_receipt_wall,
       max(receipt_wall)::text          AS last_receipt_wall,
       count(DISTINCT process_boot_id)  AS boot_ids,
       min(process_boot_id::text)       AS process_boot_id
FROM rn1_obs_events;

\echo
\echo == 2. ROW COUNTS, as sealed ==
SELECT 'rn1_obs_events' AS tbl, count(*) AS rows, 12535 AS sealed FROM rn1_obs_events
UNION ALL SELECT 'rn1_obs_snapshots',   count(*), 125270 FROM rn1_obs_snapshots
UNION ALL SELECT 'rn1_obs_transitions', count(*),  25062 FROM rn1_obs_transitions
UNION ALL SELECT 'rn1_obs_clock_sync',  count(*),      3 FROM rn1_obs_clock_sync
ORDER BY tbl;

\echo
\echo == 3. THE APPEND-ONLY TRIGGERS ARE STILL THERE (why a match is expected) ==
SELECT event_object_table AS on_table, trigger_name,
       string_agg(event_manipulation, '/' ORDER BY event_manipulation) AS on_event
FROM information_schema.triggers
WHERE event_object_schema = 'public'
  AND event_object_table LIKE 'rn1\_obs\_%'
GROUP BY 1, 2
ORDER BY 1, 2;

\echo
\echo == 4. INDEPENDENT RE-DERIVATION of the four digests ==
WITH ev AS (
    SELECT source_event_id AS rec FROM rn1_obs_events ORDER BY source_event_id
), sn AS (
    SELECT concat_ws('|', obs_event_id::text, offset_label, status) AS rec
    FROM rn1_obs_snapshots ORDER BY obs_event_id::text, offset_label
), tr AS (
    SELECT concat_ws('|', obs_event_id::text, stage, state) AS rec
    FROM rn1_obs_transitions ORDER BY obs_event_id::text, stage, state
), cs AS (
    SELECT concat_ws('|', sync_id::text, COALESCE(host_sync_status, 'NULL')) AS rec
    FROM rn1_obs_clock_sync ORDER BY sync_id
)
SELECT
  encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM ev), 'UTF8')), 'hex') AS event_digest,
  encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM sn), 'UTF8')), 'hex') AS snapshot_digest,
  encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM tr), 'UTF8')), 'hex') AS transition_digest,
  encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM cs), 'UTF8')), 'hex') AS clock_digest;

\echo
\echo == 5. THE VERDICT: each derived digest against its sealed value ==
WITH ev AS (
    SELECT source_event_id AS rec FROM rn1_obs_events ORDER BY source_event_id
), sn AS (
    SELECT concat_ws('|', obs_event_id::text, offset_label, status) AS rec
    FROM rn1_obs_snapshots ORDER BY obs_event_id::text, offset_label
), tr AS (
    SELECT concat_ws('|', obs_event_id::text, stage, state) AS rec
    FROM rn1_obs_transitions ORDER BY obs_event_id::text, stage, state
), cs AS (
    SELECT concat_ws('|', sync_id::text, COALESCE(host_sync_status, 'NULL')) AS rec
    FROM rn1_obs_clock_sync ORDER BY sync_id
), derived(name, got, sealed) AS (
    VALUES
      ('EVENT_IDENTITY_DIGEST',
       encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM ev), 'UTF8')), 'hex'),
       '131ad1ef28271df7083ca2eff8f3ca1724a34929051689a9ca7e99c4b5b7358a'),
      ('SNAPSHOT_IDENTITY_DIGEST',
       encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM sn), 'UTF8')), 'hex'),
       '41b2a3fb5c878493f0f080a1138dd6c2341f0d0f95f0d593dfd4f7e498c22331'),
      ('TRANSITION_IDENTITY_DIGEST',
       encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM tr), 'UTF8')), 'hex'),
       '4609219ab124cd5ea952b017f698b5137cef59900a294d2712fc84f3e3aea9fb'),
      ('CLOCK_SYNC_IDENTITY_DIGEST',
       encode(sha256(convert_to((SELECT string_agg(rec, chr(10)) FROM cs), 'UTF8')), 'hex'),
       '5758e485863d8b5caf44891827d998ad65934bcaad2fb2c186ff15a9dfa31e40')
)
SELECT name, (got = sealed) AS matches, left(got, 16) AS got_head,
       left(sealed, 16) AS sealed_head
FROM derived
ORDER BY name;

\echo
\echo == 6. AND THE PAUSE STILL HOLDS ==
SELECT key, left(value::text, 80) AS value
FROM ingestion_state
WHERE key IN ('mirror_live', 'premap_live', 'workers_boot')
ORDER BY key;
