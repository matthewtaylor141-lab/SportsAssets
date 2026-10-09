-- READ-ONLY. RC6 lane P0-429 (venue rate-limit recovery), review round 2:
-- THE POST-DEPLOY READBACK that the catalogue sweep WAITS a short 429
-- cooldown out (never stops its pass on one) and that its receipts stay
-- COMPLETE. Compare with rc6_venue-429_catalogue_baseline.sql (RC6 732cc0c6,
-- research-sql 37968228846: full 47/49 COMPLETE, p50 722 s / p90 854 s /
-- max 922 s; calendar 46/46; fast 228/228; zero RATE_LIMITED_BY_VENUE).
--
-- Before the lane deploys the two wait notes are absent (NULL / 0), which
-- is the point: absent is UNMEASURED, never PASS.
--
--   R1 the LATEST receipt per lane (catalogue_completeness reads full and
--      calendar; the plane reads fast): outcome, seconds, the window/pass
--      stops, the sweep's own cooldown waits and the reads our gate withheld
--   R2 per lane per hour since the deploy window: receipts, COMPLETE,
--      stopped RATE_LIMITED_BY_VENUE, receipts that waited a cooldown, the
--      waits and the milliseconds waited, the reads withheld
--   R3 duration p50 / p90 / max per lane, last 24 h, split by whether the
--      sweep waited a cooldown (the wall time WINDOW_MAX_SECONDS judges)
--   R4 every non-COMPLETE receipt in the last 24 h with its stops and notes
--
-- Every statement is a SELECT. Counters, outcomes and stop names only.

\echo == R1. latest receipt per lane ==
SELECT DISTINCT ON (lane) lane, id, recorded_at, outcome,
       round(extract(epoch FROM finished_at - started_at)) AS seconds,
       requests,
       (SELECT string_agg(p.key || '=' || coalesce(p.value->>'stopped', '?'),
                          ', ' ORDER BY p.key)
          FROM jsonb_each(CASE WHEN jsonb_typeof(receipt->'passes') = 'object'
                               THEN receipt->'passes' ELSE '{}'::jsonb END) p)
           AS stops,
       coalesce((receipt->'notes'->>'VENUE_429_COOLDOWN_WAITS')::int, 0)
           AS cooldown_waits,
       coalesce((receipt->'notes'->>'VENUE_429_COOLDOWN_WAITED_MS')::int, 0)
           AS cooldown_waited_ms,
       (SELECT coalesce(sum((n.value)::text::int), 0)
          FROM jsonb_each(CASE WHEN jsonb_typeof(receipt->'notes') = 'object'
                               THEN receipt->'notes' ELSE '{}'::jsonb END) n
         WHERE n.key LIKE 'REQUEST_NOT_SENT_BY_OUR_VENUE_GATE:%')
           AS reads_withheld
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '24 hours'
 ORDER BY lane, recorded_at DESC, id DESC;

\echo == R2. per lane per hour: complete, rate-limited stops, cooldown waits ==
SELECT r.lane, date_trunc('hour', r.recorded_at) AS hour,
       count(*) AS receipts,
       count(*) FILTER (WHERE r.outcome = 'COMPLETE') AS complete,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(r.receipt->'passes')
                                              = 'object'
                                         THEN r.receipt->'passes'
                                         ELSE '{}'::jsonb END) p
            WHERE p.value->>'stopped' = 'RATE_LIMITED_BY_VENUE'))
           AS stopped_rate_limited,
       count(*) FILTER (WHERE coalesce(
           (r.receipt->'notes'->>'VENUE_429_COOLDOWN_WAITS')::int, 0) > 0)
           AS receipts_that_waited,
       sum(coalesce((r.receipt->'notes'->>'VENUE_429_COOLDOWN_WAITS')::int, 0))
           AS cooldown_waits,
       sum(coalesce((r.receipt->'notes'->>'VENUE_429_COOLDOWN_WAITED_MS')::int,
                    0)) AS cooldown_waited_ms,
       sum((SELECT coalesce(sum((n.value)::text::int), 0)
              FROM jsonb_each(CASE WHEN jsonb_typeof(r.receipt->'notes')
                                        = 'object'
                                   THEN r.receipt->'notes'
                                   ELSE '{}'::jsonb END) n
             WHERE n.key LIKE 'REQUEST_NOT_SENT_BY_OUR_VENUE_GATE:%'))
           AS reads_withheld
  FROM venue_catalogue_receipts r
 WHERE r.recorded_at > now() - interval '24 hours'
 GROUP BY r.lane, date_trunc('hour', r.recorded_at)
 ORDER BY hour DESC, r.lane
 LIMIT 80;

\echo == R3. duration by lane and by whether the sweep waited, last 24 h ==
SELECT lane,
       coalesce((receipt->'notes'->>'VENUE_429_COOLDOWN_WAITS')::int, 0) > 0
           AS waited,
       count(*) AS receipts,
       count(*) FILTER (WHERE outcome = 'COMPLETE') AS complete,
       round(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM finished_at - started_at))::numeric, 1)
           AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (
           ORDER BY extract(epoch FROM finished_at - started_at))::numeric, 1)
           AS p90_s,
       round(max(extract(epoch FROM finished_at - started_at))::numeric, 1)
           AS max_s
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo == R4. non-COMPLETE receipts, last 24 h ==
SELECT id, recorded_at, lane, outcome, requests,
       round(extract(epoch FROM finished_at - started_at)) AS seconds,
       (SELECT string_agg(p.key || '=' || coalesce(p.value->>'stopped', '?'),
                          ', ' ORDER BY p.key)
          FROM jsonb_each(CASE WHEN jsonb_typeof(receipt->'passes') = 'object'
                               THEN receipt->'passes' ELSE '{}'::jsonb END) p)
           AS stops,
       left(coalesce(receipt->'passes'->'WINDOW'->>'error',
                     receipt->'passes'->'AHEAD'->>'error',
                     receipt->'passes'->'FAST'->>'error', ''), 120) AS error,
       coalesce((receipt->'notes'->>'VENUE_429_COOLDOWN_WAITS')::int, 0)
           AS cooldown_waits
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '24 hours' AND outcome <> 'COMPLETE'
 ORDER BY recorded_at DESC
 LIMIT 40;
