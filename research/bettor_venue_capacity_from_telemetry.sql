-- WHAT WILL THE GATEWAY ACTUALLY SERVE? From data already collected.
--
-- read_more is recommended contingent on the venue serving more reads
-- than the collector currently issues. That has to be measured, and the
-- obvious way -- burst the gateway and find the knee -- is the one way
-- I must not use: the Actions concurrency group
-- (pmus-public-read-global) governs WORKFLOWS, and the collector runs
-- continuously on Render, outside it. A probe would contend with the
-- live collector, whose pacing is adaptive, so it would answer the
-- question by degrading the thing the answer is for.
--
-- It is already answered. The collector has been running for days at a
-- pacing that BACKS OFF on 429 and recovers on success, so its own
-- telemetry contains the relationship between request rate and refusal
-- rate across a range of rates it has already visited. Every tick
-- records reads issued, reads rate-limited, and the pacing in force.
--
-- ZERO ADDITIONAL VENUE LOAD. Read only.
\echo == 1. THE REFUSAL RATE AT EACH PACING THE COLLECTOR HAS RUN ==
-- pacing is seconds between requests, so 1/pacing is requests/second.
-- If 429s climb as pacing falls, the knee is visible without provoking
-- one.
SELECT round(nullif(pacing_s, '')::numeric, 1) AS pacing_s,
       round((1.0 / nullif(nullif(pacing_s, '')::numeric, 0))::numeric, 3)
           AS req_per_s,
       count(*) AS ticks,
       sum(obs_attempted + fu_attempted) AS reads_issued,
       sum(obs_rate_limited) AS rate_limited,
       round((100.0 * sum(obs_rate_limited)
              / nullif(sum(obs_attempted + fu_attempted), 0))::numeric, 2)
           AS pct_429,
       sum(obs_unreadable_other) AS other_errors
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '7 days'
   AND pacing_s IS NOT NULL AND pacing_s <> ''
 GROUP BY 1, 2
 ORDER BY 1;

\echo
\echo == 2. THE SAME, BY READS ISSUED IN A SINGLE TICK ==
-- The per-tick burst size is the other axis: pacing spaces requests,
-- but a tick issuing 9 reads asks more of the gateway in that window
-- than one issuing 2.
SELECT (obs_attempted + fu_attempted) AS reads_in_tick,
       count(*) AS ticks,
       sum(obs_rate_limited) AS rate_limited,
       round((100.0 * sum(obs_rate_limited)
              / nullif(sum(obs_attempted + fu_attempted), 0))::numeric, 2)
           AS pct_429
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '7 days'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 3. HAS THE COLLECTOR EVER SUSTAINED A HIGHER RATE CLEANLY? ==
-- The question read_more turns on: is there a rate ABOVE today's that
-- the gateway served without refusing? If the best clean rate is the
-- one already in use, read_more has no headroom to claim.
SELECT round(nullif(pacing_s, '')::numeric, 1) AS pacing_s,
       count(*) AS ticks,
       round(avg(obs_attempted + fu_attempted)::numeric, 2) AS avg_reads,
       max(obs_attempted + fu_attempted) AS max_reads,
       sum(obs_rate_limited) AS rate_limited
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '7 days'
   AND pacing_s IS NOT NULL AND pacing_s <> ''
 GROUP BY 1
HAVING sum(obs_rate_limited) = 0
 ORDER BY 1
 LIMIT 20;

\echo
\echo == 4. WHEN DID THE 429s HAPPEN? ==
-- Clustered in a few bad minutes or spread through the week? A cluster
-- is a venue incident; a spread is a standing limit.
SELECT date_trunc('hour', tick_at) AS hour,
       sum(obs_attempted + fu_attempted) AS reads,
       sum(obs_rate_limited) AS rate_limited
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '7 days'
 GROUP BY 1
HAVING sum(obs_rate_limited) > 0
 ORDER BY 1 DESC
 LIMIT 20;

\echo
\echo == 5. THE COLLECTOR IS NOT THE ONLY CONSUMER ==
-- The mirror lane and the desk share this gateway. Any headroom the
-- collector finds is headroom it shares, so the figure above is an
-- upper bound on what the collector may safely take, not an allowance.
SELECT count(*) AS live_orders_last_7d,
       count(*) FILTER (WHERE status = 'filled') AS filled,
       max(placed_at) AS last_order
  FROM live_orders
 WHERE placed_at > now() - interval '7 days';
