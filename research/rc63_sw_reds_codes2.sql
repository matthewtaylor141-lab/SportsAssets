-- READ-ONLY. RC6.3b software-reds audit part 6: (a) when PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED
-- ever fires, against the API deploy instants (the cumulative events_evicted counter
-- pinnapi_names.absence reads makes it impossible once the feed cache has evicted), (b) the 38
-- QUOTE_STALE_ON_ARRIVAL events of the packet window at their last cycle by lag class, (c) the
-- ledger columns of the NO_EXACT events. SELECT only.

\echo == G1 NOT_YET_POSTED and NO_EXACT events per hour, last 7 days
SELECT date_trunc('hour', cycle_at) AS hr,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED') AS not_yet_posted_events,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS no_exact_events,
       count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED' OR first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE') AS either
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '7 days'
   AND first_refusal IN ('PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED', 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE')
 GROUP BY 1 HAVING count(DISTINCT provider_event_id) FILTER (WHERE first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED') > 0
 ORDER BY 1 LIMIT 80;

\echo == G2 NOT_YET_POSTED by day and the first/last row of each day
SELECT date_trunc('day', cycle_at) AS day, count(*) AS rows, count(DISTINCT provider_event_id) AS events,
       min(cycle_at) AS first_row, max(cycle_at) AS last_row
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '14 days' AND first_refusal = 'PINNAPI_PRIMARY_FIXTURE_NOT_YET_POSTED'
 GROUP BY 1 ORDER BY 1 LIMIT 20;

\echo == G3 the 38 QUOTE_STALE_ON_ARRIVAL events at the last packet-window cycle, by lag class and the beside code
SELECT CASE WHEN provider_lag_s IS NULL THEN 'LAG_UNMEASURED' WHEN provider_lag_s > 30 THEN 'PROVIDER_LAG_GT_30' ELSE 'DELIVERED_INSIDE_30' END AS lag_class,
       codes ->> 1 AS ws_refusal_beside, count(DISTINCT provider_event_id) AS events,
       round(avg(provider_lag_s)::numeric, 1) AS avg_provider_lag_s, round(avg(our_processing_s)::numeric, 1) AS avg_our_processing_s,
       min(queue_position) AS min_q, max(queue_position) AS max_q
  FROM ext_candidate_outcomes
 WHERE cycle_at >= to_timestamp(1791625400) AND cycle_at < to_timestamp(1791625440) AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == G4 how often a stale-on-arrival quote of DELIVERED_INSIDE_30 class is the pacing of OUR venue reads: queue position against our processing, last 6 h
SELECT CASE WHEN queue_position < 5 THEN 'a_q0_4' WHEN queue_position < 15 THEN 'b_q5_14' WHEN queue_position < 30 THEN 'c_q15_29' ELSE 'd_q30_plus' END AS queue_band,
       count(*) AS rows, round(avg(our_processing_s)::numeric, 1) AS avg_our_processing_s,
       count(*) FILTER (WHERE provider_lag_s <= 30) AS delivered_inside_30
  FROM ext_candidate_outcomes
 WHERE cycle_at > now() - interval '6 hours' AND first_refusal = 'QUOTE_STALE_ON_ARRIVAL'
 GROUP BY 1 ORDER BY 1 LIMIT 10;

\echo == G5 PAPER_PASS_BACKSTOP decisions: how old the Pinnacle reading already was, last 6 h, by refusal
SELECT pinnacle->>'decided_via' AS via, refusal, count(*) AS decisions,
       count(*) FILTER (WHERE 'PROBABILITY_EVIDENCE_STALE' = ANY(refusals)) AS stale_decisions
  FROM paper_decisions
 WHERE decided_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 16;

\echo == G6 per event: stale decisions that sit beside a decision that reached EV (packet window, the ledger link by valuation event_key)
WITH d AS (
  SELECT v.event_key, d.strategy, d.refusal, d.pinnacle->>'decided_via' AS via
    FROM paper_decisions d JOIN external_valuations v ON v.id = d.valuation_id
   WHERE d.decided_at >= to_timestamp(1791622474.8284767) AND d.decided_at < to_timestamp(1791626075.8284767)),
e AS (SELECT event_key,
             bool_or(refusal = 'PROBABILITY_EVIDENCE_STALE') AS has_stale,
             bool_or(refusal IN ('BELOW_MIN_GROSS_EDGE', 'NET_EV_NOT_POSITIVE_AFTER_FEES', 'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT')) AS has_ev
        FROM d GROUP BY 1)
SELECT has_stale, has_ev, count(*) AS events FROM e GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 8;
