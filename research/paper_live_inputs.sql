-- READ-ONLY. IS LIVE MARKET DATA REACHING THE PAPER SESSION?
--
-- Derek decides once per entry-experiment valuation the collection cycle
-- writes (paper_runtime.decide_valuation). The pass itself reads books only
-- for open paper orders/positions, so books_read 0 with no positions is
-- expected. This asks whether valuations keep arriving, and what the latest
-- collection cycle says when none do. Nothing here writes.
--
-- L1 the latest collection-cycle heartbeat (label, markets, evaluated,
--    errors, elapsed) and the servicing heartbeat time
-- L2 entry-experiment valuations by 10-minute bucket since 01:00Z
-- L3 Derek's research observations by 10-minute bucket and cohort
-- L4 the latest 10 paper decisions: market, verdict, refusals, the
--    Pinnacle age and qualification gaps (truncated)

\echo '== L1 · collection cycle heartbeat =='
SELECT key,
       COALESCE(value->>'written_at', value->>'at') AS written_at,
       value->>'cycle_label' AS cycle_label,
       value->>'markets_considered' AS markets_considered,
       jsonb_array_length(COALESCE(value->'venue_errors', '[]'::jsonb)) AS venue_errors,
       value->>'elapsed_s' AS elapsed_s,
       left(COALESCE(value->'funnel_by_provider_sport', 'null'::jsonb)::text, 1200) AS funnel
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_cycle', 'ext_pinnacle_last_cycle_standby',
               'ext_pinnacle_last_servicing')
 ORDER BY key;

\echo '== L2 · entry-experiment valuations since 01:00Z =='
SELECT date_trunc('hour', decided_at)
         + floor(extract(minute FROM decided_at) / 10) * interval '10 min' AS bucket,
       count(*) AS valuations
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND decided_at > date_trunc('day', now()) + interval '1 hour'
 GROUP BY 1 ORDER BY 1;

\echo '== L3 · research observations since 01:00Z =='
SELECT date_trunc('hour', decided_at)
         + floor(extract(minute FROM decided_at) / 10) * interval '10 min' AS bucket,
       cohort, count(*) AS observations
  FROM derek_research_observations
 WHERE decided_at > date_trunc('day', now()) + interval '1 hour'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== L4 · latest paper decisions =='
SELECT decided_at, valuation_id, left(us_market_slug, 40) AS slug, verdict,
       refusal, refusals,
       left(pinnacle::text, 200) AS pinnacle,
       left(qualification_gaps::text, 200) AS gaps
  FROM paper_decisions ORDER BY decided_at DESC LIMIT 10;
