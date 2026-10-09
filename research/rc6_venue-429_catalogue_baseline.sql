-- READ-ONLY. RC6 lane P0-429 (venue rate-limit recovery), review round 2:
-- THE CATALOGUE SWEEP'S PRODUCTION BASELINE before the escalating 429
-- cooldown deploys, so the deployed lane can be compared against it.
--
-- The review's question: does making premap WAIT out a short 429 cooldown
-- (instead of stopping its pass) keep the full / calendar / fast receipts
-- COMPLETE, and how much wall-time headroom do the window walks have against
-- their 900 s bounds (premap.WINDOW_MAX_SECONDS / CALENDAR_MAX_SECONDS)?
--
--   B1 receipts by lane and outcome, last 24 h
--   B2 each lane's duration (finished_at - started_at) and requests:
--      p50 / p90 / max, last 24 h
--   B3 every pass stop by lane and pass, last 24 h (RATE_LIMITED_BY_VENUE
--      counted by name)
--   B4 receipts per lane per hour whose any pass stopped
--      RATE_LIMITED_BY_VENUE, last 24 h
--   B5 the slowest 10 full-lane receipts, last 24 h, with their window
--      pass's stop
--
-- Every statement is a SELECT. Counters, outcomes and stop names only.

\echo == B1. receipts by lane and outcome, last 24 h ==
SELECT lane, outcome, count(*) AS receipts, max(recorded_at) AS last_at
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY lane, outcome
 ORDER BY lane, outcome;

\echo == B2. duration and requests by lane, last 24 h ==
SELECT lane, count(*) AS receipts,
       round(percentile_cont(0.5) WITHIN GROUP (
           ORDER BY extract(epoch FROM finished_at - started_at))::numeric, 1)
           AS p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (
           ORDER BY extract(epoch FROM finished_at - started_at))::numeric, 1)
           AS p90_s,
       round(max(extract(epoch FROM finished_at - started_at))::numeric, 1)
           AS max_s,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY requests) AS p50_requests,
       max(requests) AS max_requests,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY pages_read) AS p50_pages
  FROM venue_catalogue_receipts
 WHERE recorded_at > now() - interval '24 hours'
 GROUP BY lane
 ORDER BY lane;

\echo == B3. pass stops by lane and pass, last 24 h ==
SELECT r.lane, p.key AS pass, p.value->>'stopped' AS stopped,
       count(*) AS receipts
  FROM venue_catalogue_receipts r,
       LATERAL jsonb_each(CASE WHEN jsonb_typeof(r.receipt->'passes') = 'object'
                               THEN r.receipt->'passes' ELSE '{}'::jsonb END) p
 WHERE r.recorded_at > now() - interval '24 hours'
 GROUP BY r.lane, p.key, p.value->>'stopped'
 ORDER BY r.lane, p.key, receipts DESC;

\echo == B4. receipts per lane per hour with a pass stopped RATE_LIMITED_BY_VENUE ==
SELECT r.lane, date_trunc('hour', r.recorded_at) AS hour,
       count(*) AS receipts,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(r.receipt->'passes')
                                              = 'object'
                                         THEN r.receipt->'passes'
                                         ELSE '{}'::jsonb END) p
            WHERE p.value->>'stopped' = 'RATE_LIMITED_BY_VENUE'))
           AS rate_limited_receipts,
       count(*) FILTER (WHERE r.outcome = 'COMPLETE') AS complete_receipts
  FROM venue_catalogue_receipts r
 WHERE r.recorded_at > now() - interval '24 hours'
 GROUP BY r.lane, date_trunc('hour', r.recorded_at)
 ORDER BY hour DESC, r.lane
 LIMIT 80;

\echo == B5. slowest 10 full-lane receipts, last 24 h ==
SELECT id, recorded_at, outcome, requests, pages_read,
       round(extract(epoch FROM finished_at - started_at)) AS seconds,
       receipt->'passes'->'WINDOW'->>'stopped' AS window_stopped,
       left(coalesce(receipt->'passes'->'WINDOW'->>'error', ''), 120)
           AS window_error
  FROM venue_catalogue_receipts
 WHERE lane = 'full' AND recorded_at > now() - interval '24 hours'
 ORDER BY finished_at - started_at DESC
 LIMIT 10;
