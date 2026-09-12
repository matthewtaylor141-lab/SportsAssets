-- RUN 83 ACTIVATION SAFETY GATE + FIRST DATA-QUALITY GATE -- read-only.
--
-- The collector went live at 15:56:25Z and is writing hard. Before anything is
-- said about the data, this asks the two questions that decide whether the
-- instrument is sound: is the read rate what it was paced to be, and is the
-- curve actually being captured or merely scheduled?
--
-- A SCHEDULED SNAPSHOT IS NOT AN OBSERVATION. The schema deliberately writes a
-- row for a missed offset so that absence dates itself. That makes the row count
-- a count of INTENTIONS, not of measurements, and the two must never be read as
-- the same number.

\echo == 1. VOLUME AND RATE, since the collector booted ==
SELECT
    (SELECT count(*) FROM rn1_obs_events)                       AS events,
    (SELECT count(*) FROM rn1_obs_snapshots)                    AS snapshots,
    (SELECT count(*) FROM rn1_obs_transitions)                  AS transitions,
    (SELECT min(receipt_wall) FROM rn1_obs_events)::text        AS first_event,
    (SELECT max(receipt_wall) FROM rn1_obs_events)::text        AS last_event,
    round(EXTRACT(epoch FROM
        (SELECT max(receipt_wall) - min(receipt_wall) FROM rn1_obs_events))::numeric, 1)
                                                                AS span_s;

\echo
\echo == 2. THE DECIDING SPLIT: captured vs missed, with the miss reasons ==
--
-- GROUPING ON THE RAW miss_reason DESTROYED THIS QUERY ON ITS FIRST RUN
-- (2026-09-12 16:04Z). The reason string ends ", now +72.727s" -- an elapsed
-- measurement, distinct per row -- so `GROUP BY miss_reason` made one group per
-- row: ~125,000 one-row groups, 3,000 of which printed and consumed the whole
-- head -3000 budget of the runner. Statements 3 through 13 never ran into the
-- log at all, and the seven gate items they answer went unverified.
--
-- The elapsed tail is the very thing being measured, so it cannot stay in a
-- grouping key. split_part cuts at ", now " and keeps the invariant half --
-- "window closed unread: due at +0s, window 0.05s" -- which is bounded by the
-- ten-offset ladder. The LIMIT is a second line of defence: a grouping key that
-- explodes again truncates ITSELF rather than the statements below it.
SELECT status,
       split_part(COALESCE(miss_reason, '(none)'), ', now ', 1) AS miss_reason,
       count(*)                                   AS rows,
       round(100.0 * count(*) / SUM(count(*)) OVER (), 2) AS pct
FROM rn1_obs_snapshots
GROUP BY 1, 2
ORDER BY rows DESC
LIMIT 40;

\echo
\echo == 3. ACTUAL VENUE READS: rows that carry a response instant ==
SELECT count(*) AS reads_with_response,
       round(EXTRACT(epoch FROM (max(response_wall) - min(response_wall)))::numeric, 1) AS span_s,
       round((count(*) / NULLIF(EXTRACT(epoch FROM
             (max(response_wall) - min(response_wall))), 0))::numeric, 3) AS reads_per_second
FROM rn1_obs_snapshots
WHERE response_wall IS NOT NULL;

\echo
\echo == 4. WHOSE EVENTS ARE THESE? the hook fires for every ingested fill ==
SELECT source_lane, source_venue, count(*) AS events,
       count(DISTINCT source_market_id) AS markets,
       count(DISTINCT source_token_id)  AS tokens
FROM rn1_obs_events
GROUP BY source_lane, source_venue
ORDER BY events DESC;

\echo
\echo == 5. SOURCE IDENTITY AND PROVENANCE: nulls are the thing to count ==
SELECT count(*)                                          AS events,
       count(*) FILTER (WHERE source_event_id IS NULL)   AS null_source_event_id,
       count(DISTINCT source_event_id)                   AS distinct_source_event_id,
       count(*) FILTER (WHERE source_ts_provenance IS NULL) AS null_provenance,
       count(*) FILTER (WHERE source_ts_clock_domain IS NULL) AS null_clock_domain,
       count(*) FILTER (WHERE process_boot_id IS NULL)   AS null_boot_id,
       count(*) FILTER (WHERE receipt_monotonic IS NULL) AS null_receipt_monotonic,
       count(DISTINCT process_boot_id)                   AS distinct_boot_ids
FROM rn1_obs_events;

\echo
\echo == 6. FALLBACK STATUS, STATED NOT INFERRED ==
SELECT source_ts_status, source_ts_fallback, source_ts_provenance,
       source_ts_clock_domain, count(*) AS events
FROM rn1_obs_events
GROUP BY 1, 2, 3, 4
ORDER BY events DESC;

\echo
\echo == 7. THE OFFSET LADDER: every requested offset, captured or explicitly failed ==
SELECT offset_label, offset_target_s,
       count(*)                                        AS scheduled,
       count(*) FILTER (WHERE status = 'CAPTURED')     AS captured,
       count(*) FILTER (WHERE status <> 'CAPTURED')    AS not_captured,
       round(avg(actual_offset_s)::numeric, 3)         AS mean_actual_offset_s,
       round(min(actual_offset_s)::numeric, 3)         AS min_actual,
       round(max(actual_offset_s)::numeric, 3)         AS max_actual
FROM rn1_obs_snapshots
GROUP BY offset_label, offset_target_s
ORDER BY offset_target_s;

\echo
\echo == 8. LATENESS / JITTER: measured against the target, never assumed ==
SELECT offset_label,
       count(*) FILTER (WHERE actual_offset_s IS NOT NULL) AS measured,
       round(avg(actual_offset_s - offset_target_s)::numeric, 4) AS mean_lateness_s,
       round(min(actual_offset_s - offset_target_s)::numeric, 4) AS min_lateness_s,
       round(max(actual_offset_s - offset_target_s)::numeric, 4) AS max_lateness_s
FROM rn1_obs_snapshots
WHERE actual_offset_s IS NOT NULL
GROUP BY offset_label
ORDER BY offset_label;

\echo
\echo == 9. CHANNEL LABELLING: nothing may claim to be the fastest path ==
SELECT observation_channel, transport, count(*) AS rows,
       count(DISTINCT feed_identity) AS feeds
FROM rn1_obs_snapshots
GROUP BY observation_channel, transport
ORDER BY rows DESC;

\echo
\echo == 10. THE CAUSAL CHAIN: transitions per event, and orphans ==
SELECT t.stage, t.state, count(*) AS rows
FROM rn1_obs_transitions t
GROUP BY t.stage, t.state
ORDER BY rows DESC
LIMIT 20;

\echo
\echo == 11. ORPHANS: a snapshot or transition whose parent event is absent ==
SELECT
    (SELECT count(*) FROM rn1_obs_snapshots s
      WHERE NOT EXISTS (SELECT 1 FROM rn1_obs_events e
                         WHERE e.obs_event_id = s.obs_event_id)) AS orphan_snapshots,
    (SELECT count(*) FROM rn1_obs_transitions t
      WHERE NOT EXISTS (SELECT 1 FROM rn1_obs_events e
                         WHERE e.obs_event_id = t.obs_event_id)) AS orphan_transitions;

\echo
\echo == 12. STORAGE: what this is costing, against the 124 MB/day estimate ==
SELECT relname AS tbl,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
       pg_total_relation_size(c.oid)                 AS bytes
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND relname LIKE 'rn1\_obs\_%'
ORDER BY bytes DESC;

\echo
\echo == 13. THE PAUSE HOLDS ==
SELECT key, left(value::text, 80) AS value
FROM ingestion_state
WHERE key IN ('mirror_live', 'premap_live', 'workers_boot')
ORDER BY key;
