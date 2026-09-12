-- RUN 83.1b -- WERE THESE FILLS NEW? plus the cohort seal. Read-only.
--
-- The per-wallet counts are too uniform to be live flow: nineteen of twenty
-- wallets landed within 371-684 events of each other in 12.4 minutes, and
-- zxgngl produced 682 events across ONE market and ONE token. Live trading does
-- not arrive in equal portions per wallet. A historical walk does.
--
-- The ledger already implies it: only 766 fills across all wallets carry a
-- detected_at inside the collector's window, against 12,535 observations. So
-- most of what the instrument observed was not detected during the window at
-- all -- it was re-presented to ingest_trade_result by a sweep.
--
-- THIS IS THE DEDUPLICATION BOUNDARY, AND IT SITS IN THE WRONG PLACE. The hook
-- is the FIRST statement of ingest_trade_result; the ON CONFLICT (dedupe_key)
-- that makes a re-ingested fill a no-op is in the statement AFTER it. So at the
-- trades level a re-swept fill costs one refused insert, and at the instrument
-- it costs a full observation with ten scheduled venue reads -- because the
-- observation table has never seen that key before, even though the ledger has
-- held the row for days.
--
-- The measurement below is the age of each observed fill at the instant it was
-- observed. A live fill is seconds old. A re-swept one is hours or days old, and
-- its price curve "after BETTOR first receives the event" is meaningless because
-- BETTOR first received it long ago.

\echo == 1. AGE AT OBSERVATION: how old was each fill when the collector saw it ==
WITH aged AS (
    SELECT EXTRACT(epoch FROM (e.receipt_wall - t.detected_at)) AS detect_age_s,
           EXTRACT(epoch FROM (e.receipt_wall - t.ts))          AS fill_age_s
    FROM rn1_obs_events e JOIN trades t ON t.dedupe_key = e.source_event_id
)
SELECT CASE
         WHEN detect_age_s <     60 THEN 'a. under 1 min   (plausibly live)'
         WHEN detect_age_s <    600 THEN 'b. 1-10 min'
         WHEN detect_age_s <   3600 THEN 'c. 10-60 min'
         WHEN detect_age_s <  86400 THEN 'd. 1-24 hours'
         ELSE                            'e. over 24 hours'
       END                                        AS age_at_observation,
       count(*)                                   AS events,
       round(100.0 * count(*) / SUM(count(*)) OVER (), 3) AS pct
FROM aged
GROUP BY 1
ORDER BY 1;

\echo
\echo == 2. THE SAME SPLIT, BY WALLET, FOR RN1 AND THE REST ==
WITH aged AS (
    SELECT CASE WHEN w.username = 'RN1' THEN 'RN1' ELSE 'other tracked wallets' END AS who,
           EXTRACT(epoch FROM (e.receipt_wall - t.detected_at)) AS detect_age_s
    FROM rn1_obs_events e
    JOIN trades t ON t.dedupe_key = e.source_event_id
    JOIN whales w ON w.id = t.whale_id
)
SELECT who,
       count(*)                                          AS events,
       count(*) FILTER (WHERE detect_age_s < 60)         AS live_under_1min,
       count(*) FILTER (WHERE detect_age_s >= 60)        AS re_swept,
       round(avg(detect_age_s)::numeric, 1)              AS mean_age_s,
       round(percentile_disc(0.50) WITHIN GROUP (ORDER BY detect_age_s)::numeric, 1) AS p50_age_s,
       round(max(detect_age_s)::numeric, 1)              AS max_age_s
FROM aged
GROUP BY who
ORDER BY events DESC;

\echo
\echo == 3. THE PROSPECTIVE DENOMINATOR: RN1 fills that were genuinely NEW ==
-- This is the number an RN1-only forward experiment would actually observe:
-- RN1's fills, counted once, at the moment BETTOR first detected them.
SELECT count(*)                                        AS rn1_live_observations,
       round((count(*) / 744.5)::numeric, 4)           AS per_second,
       round((count(*) / 744.5 * 86400)::numeric)      AS implied_per_day
FROM rn1_obs_events e
JOIN trades t ON t.dedupe_key = e.source_event_id
JOIN whales w ON w.id = t.whale_id
WHERE w.username = 'RN1'
  AND EXTRACT(epoch FROM (e.receipt_wall - t.detected_at)) < 60;

\echo
\echo == 4. WHICH LANE CARRIED THE RE-SWEPT ROWS ==
SELECT e.source_lane,
       count(*) FILTER (WHERE EXTRACT(epoch FROM (e.receipt_wall - t.detected_at)) <  60) AS live,
       count(*) FILTER (WHERE EXTRACT(epoch FROM (e.receipt_wall - t.detected_at)) >= 60) AS re_swept,
       count(*) AS total
FROM rn1_obs_events e JOIN trades t ON t.dedupe_key = e.source_event_id
GROUP BY e.source_lane
ORDER BY total DESC;

\echo
\echo == 5. COHORT SEAL -- interval, boot id, counts (uuid cast: min(uuid) does not exist) ==
SELECT 'RUN83_ACTIVATION_FAILED_V1'                                  AS cohort_id,
       (SELECT min(receipt_wall) FROM rn1_obs_events)::text          AS first_receipt_wall,
       (SELECT max(receipt_wall) FROM rn1_obs_events)::text          AS last_receipt_wall,
       (SELECT max(row_written_at) FROM rn1_obs_snapshots)::text     AS last_row_written,
       (SELECT count(DISTINCT process_boot_id) FROM rn1_obs_events)  AS distinct_boot_ids,
       (SELECT min(process_boot_id::text) FROM rn1_obs_events)       AS process_boot_id;

\echo
\echo == 6. COHORT SEAL -- row counts ==
SELECT 'rn1_obs_events' AS tbl, count(*) AS rows FROM rn1_obs_events
UNION ALL SELECT 'rn1_obs_snapshots',   count(*) FROM rn1_obs_snapshots
UNION ALL SELECT 'rn1_obs_transitions', count(*) FROM rn1_obs_transitions
UNION ALL SELECT 'rn1_obs_clock_sync',  count(*) FROM rn1_obs_clock_sync
ORDER BY tbl;

\echo
\echo == 7. COHORT SEAL -- canonical identity digests ==
SELECT
  encode(sha256(convert_to(
    (SELECT string_agg(source_event_id, E'\n' ORDER BY source_event_id)
       FROM rn1_obs_events), 'UTF8')), 'hex')            AS event_identity_digest,
  encode(sha256(convert_to(
    (SELECT string_agg(obs_event_id::text || '|' || offset_label || '|' || status,
                       E'\n' ORDER BY obs_event_id::text, offset_label)
       FROM rn1_obs_snapshots), 'UTF8')), 'hex')         AS snapshot_identity_digest;

\echo
\echo == 8. COHORT SEAL -- transitions and clock sync digests ==
SELECT
  encode(sha256(convert_to(
    (SELECT string_agg(obs_event_id::text || '|' || stage || '|' || state,
                       E'\n' ORDER BY obs_event_id::text, stage, state)
       FROM rn1_obs_transitions), 'UTF8')), 'hex')       AS transition_identity_digest,
  encode(sha256(convert_to(
    (SELECT string_agg(sync_id::text || '|' || COALESCE(host_sync_status, 'NULL'),
                       E'\n' ORDER BY sync_id)
       FROM rn1_obs_clock_sync), 'UTF8')), 'hex')        AS clock_sync_identity_digest;
