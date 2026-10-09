-- READ-ONLY. RC6 lane identity-debt: why reconciler run 5698 (03:26:45 -
-- 03:35:42Z, 2026-10-09) ingested ten of whale 40's fills late (fills
-- 03:24:27-03:25:11Z, inserted 03:35:34Z). The workers restarted for the
-- RC6 deploy at 03:26:40Z (poller "starting" line). This reads whale 40's
-- rows around the restart by source and minute, the poller's last detection
-- before the restart and its first after. Whale id only; SELECT only.
\echo == R1 whale 40 rows by fill minute and source, 03:20-03:32Z ==
SELECT date_trunc('minute', t.ts) AS fill_minute, t.source, count(*) AS n,
       min(t.detected_at) AS first_detected, max(t.detected_at) AS last_detected
  FROM trades t
 WHERE t.whale_id = 40
   AND t.ts >= timestamptz '2026-10-09 03:20:00+00'
   AND t.ts <  timestamptz '2026-10-09 03:32:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == R2 poller detections of whale 40 around the restart ==
SELECT 'last_before_restart' AS k, max(t.detected_at) AS detected_at,
       (SELECT max(t2.ts) FROM trades t2 WHERE t2.whale_id = 40 AND t2.source = 'poll'
         AND t2.detected_at < timestamptz '2026-10-09 03:26:40+00'
         AND t2.detected_at >= timestamptz '2026-10-09 03:00:00+00') AS newest_fill_then
  FROM trades t
 WHERE t.whale_id = 40 AND t.source = 'poll'
   AND t.detected_at < timestamptz '2026-10-09 03:26:40+00'
   AND t.detected_at >= timestamptz '2026-10-09 03:00:00+00'
UNION ALL
SELECT 'first_after_restart', min(t.detected_at),
       (SELECT min(t2.ts) FROM trades t2 WHERE t2.whale_id = 40 AND t2.source = 'poll'
         AND t2.detected_at >= timestamptz '2026-10-09 03:26:40+00'
         AND t2.detected_at <  timestamptz '2026-10-09 03:30:00+00')
  FROM trades t
 WHERE t.whale_id = 40 AND t.source = 'poll'
   AND t.detected_at >= timestamptz '2026-10-09 03:26:40+00'
   AND t.detected_at <  timestamptz '2026-10-09 03:35:00+00';

\echo == R3 whale 40 detection batches 03:20-03:36Z (by detected second, source) ==
SELECT date_trunc('second', t.detected_at) AS detected_s, t.source, count(*) AS n,
       min(t.ts) AS oldest_fill, max(t.ts) AS newest_fill
  FROM trades t
 WHERE t.whale_id = 40
   AND t.detected_at >= timestamptz '2026-10-09 03:20:00+00'
   AND t.detected_at <  timestamptz '2026-10-09 03:36:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;
