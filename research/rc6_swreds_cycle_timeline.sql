-- READ-ONLY. RC6 lane C (software reds): three metered deciding cycles row by
-- row in queue order (the first full cycle of the 17:00Z hour with the PinnAPI
-- feed up, the first after the API restart at 18:39Z, and the first of the
-- 20:00Z hour with the feed refused), so the share of each candidate's 30 s
-- window spent before its turn is read off the rows themselves
-- (provider_lag_s, our_processing_s, quote_age_s). SELECT only.

\echo T cycles chosen
WITH c AS (
    SELECT cycle_id, min(cycle_at) AS cycle_at, count(*) AS n
      FROM ext_candidate_outcomes
     WHERE cycle_at >= timestamptz '2026-10-08 17:00:00+00'
       AND cycle_at < timestamptz '2026-10-08 21:00:00+00'
     GROUP BY cycle_id HAVING count(*) > 50),
pick AS (
    (SELECT * FROM c WHERE cycle_at >= timestamptz '2026-10-08 17:00:00+00' ORDER BY cycle_at LIMIT 1)
    UNION ALL
    (SELECT * FROM c WHERE cycle_at >= timestamptz '2026-10-08 18:39:00+00' ORDER BY cycle_at LIMIT 1)
    UNION ALL
    (SELECT * FROM c WHERE cycle_at >= timestamptz '2026-10-08 20:00:00+00' ORDER BY cycle_at LIMIT 1))
SELECT * FROM pick ORDER BY cycle_at;

\echo T1 their rows in queue order
WITH c AS (
    SELECT cycle_id, min(cycle_at) AS cycle_at, count(*) AS n
      FROM ext_candidate_outcomes
     WHERE cycle_at >= timestamptz '2026-10-08 17:00:00+00'
       AND cycle_at < timestamptz '2026-10-08 21:00:00+00'
     GROUP BY cycle_id HAVING count(*) > 50),
pick AS (
    (SELECT * FROM c WHERE cycle_at >= timestamptz '2026-10-08 17:00:00+00' ORDER BY cycle_at LIMIT 1)
    UNION ALL
    (SELECT * FROM c WHERE cycle_at >= timestamptz '2026-10-08 18:39:00+00' ORDER BY cycle_at LIMIT 1)
    UNION ALL
    (SELECT * FROM c WHERE cycle_at >= timestamptz '2026-10-08 20:00:00+00' ORDER BY cycle_at LIMIT 1))
SELECT to_char(o.cycle_at, 'HH24:MI') AS cyc, left(o.sport_key, 18) AS sport,
       o.queue_position AS qp, left(o.provider_event_id, 10) AS ev,
       o.outcome, left(o.first_refusal, 44) AS first_refusal,
       round(o.provider_lag_s::numeric, 1) AS lag,
       round(o.our_processing_s::numeric, 1) AS ours,
       round(o.quote_age_s::numeric, 1) AS age,
       (o.us_market_slug IS NOT NULL) AS mapped, left(o.mapped_by, 6) AS mby,
       left(o.codes::text, 110) AS codes
  FROM ext_candidate_outcomes o JOIN pick p USING (cycle_id)
 ORDER BY o.cycle_at, o.sport_key, o.queue_position;
