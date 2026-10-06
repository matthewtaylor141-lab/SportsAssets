-- READ-ONLY. P0 first-loss plumbing, part 2: WHERE OUR PROCESSING TIME GOES
-- in one sport fetch, for QUOTE_STALE_ON_ARRIVAL.
--
-- Part 1 (p0_first_loss_plumbing.sql, research-sql run 37411912022) showed
-- 312 of 421 NCAAF QUOTE_STALE_ON_ARRIVAL rows had a provider lag inside
-- 30 s: our own processing between the sport fetch and the arrival check
-- took the quote past the limit. This asks, per queue position, how much of
-- the fetch's time each earlier event consumed, and which events took the
-- paced venue read, so the order / cost is fixed where it is spent.
--
-- No writes. Every statement is a SELECT.

\echo T1 the last 4 multi-event NCAAF fetches: every row in queue order
WITH c AS (
    SELECT cycle_id, max(cycle_at) AS at
      FROM ext_candidate_outcomes
     WHERE cycle_at >= now() - interval '25 hours'
       AND sport_key = 'americanfootball_ncaaf'
     GROUP BY cycle_id HAVING count(*) > 5
     ORDER BY at DESC LIMIT 4)
SELECT o.cycle_id, o.queue_position AS qp, o.recorded_at, o.outcome,
       o.first_refusal, o.stage, (o.us_market_slug IS NOT NULL) AS mapped,
       o.mapped_by, round(o.provider_lag_s::numeric, 1) AS lag,
       round(o.our_processing_s::numeric, 1) AS ours,
       round(o.quote_age_s::numeric, 1) AS age
  FROM ext_candidate_outcomes o JOIN c USING (cycle_id)
 WHERE o.sport_key = 'americanfootball_ncaaf'
 ORDER BY c.at DESC, o.queue_position;

\echo T2 per sport, per queue position decile: our processing at the arrival check
SELECT sport_key, width_bucket(queue_position, 0, 80, 8) AS qp_bucket,
       count(*) AS rows,
       round(avg(our_processing_s)::numeric, 1) AS ours_avg,
       round(max(our_processing_s)::numeric, 1) AS ours_max,
       count(*) FILTER (WHERE first_refusal = 'QUOTE_STALE_ON_ARRIVAL') AS stale,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS mapped
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '25 hours'
   AND sport_key IN ('americanfootball_ncaaf', 'americanfootball_nfl',
                     'baseball_mlb', 'soccer_brazil_serie_b')
   AND our_processing_s IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo T3 the stale rows: our processing vs provider lag (could any order have saved it?)
SELECT sport_key,
       count(*) AS stale_rows,
       count(*) FILTER (WHERE provider_lag_s <= 30) AS provider_inside_limit,
       count(*) FILTER (WHERE provider_lag_s <= 30
                        AND provider_lag_s + 5 <= 30) AS headroom_5s,
       count(*) FILTER (WHERE provider_lag_s <= 30
                        AND provider_lag_s + 10 <= 30) AS headroom_10s,
       round(avg(30 - provider_lag_s) FILTER (WHERE provider_lag_s <= 30)::numeric, 1)
           AS avg_headroom_s
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '25 hours'
   AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1 ORDER BY 2 DESC;
