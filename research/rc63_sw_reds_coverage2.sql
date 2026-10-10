-- READ-ONLY. RC6.3b software-reds audit part 5: what is inside the two big
-- buckets of active_contracts_priceable -- the Polymarket US SPORT_NOT_NORMALIZED
-- gap by league code and market type, the settlement bucket by market class and
-- horizon, and how many contracts have a fair value source at all. SELECT only.

\echo == D1 PMUS ONTOLOGY_GAPS:SPORT_NOT_NORMALIZED rows by competition (league code), top 30
SELECT coalesce(competition, '(null)') AS competition, count(*) AS n,
       count(*) FILTER (WHERE event_start < now() - interval '24 hours') AS started_over_24h_ago,
       count(*) FILTER (WHERE event_start IS NULL) AS no_start,
       count(*) FILTER (WHERE event_start >= now() - interval '24 hours') AS started_within_24h_or_future,
       min(market_type) AS sample_market_type
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND coverage_why LIKE 'ONTOLOGY_GAPS:SPORT_NOT_NORMALIZED%'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

\echo == D2 PMUS ONTOLOGY_GAPS:SPORT_NOT_NORMALIZED rows by market_type, top 30
SELECT coalesce(market_type, '(null)') AS market_type, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND coverage_why LIKE 'ONTOLOGY_GAPS:SPORT_NOT_NORMALIZED%'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;

\echo == D3 PMUS CODE_CONTROLLED_GAP rows by start bucket and reason class
SELECT CASE WHEN event_start IS NULL THEN 'a_no_start'
            WHEN event_start < now() - interval '24 hours' THEN 'b_started_over_24h_ago'
            WHEN event_start < now() THEN 'c_started_0_24h_ago' ELSE 'd_future' END AS start_bucket,
       left(coalesce(coverage_why, '(null)'), 60) AS why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND coverage_state = 'CODE_CONTROLLED_GAP'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == D4 PMUS settlement-not-proven rows by market class (market_type regex of the waterfall) and period
SELECT CASE WHEN market_type ~ '(_winner(_[0-9]+)?$)' THEN 'MONEYLINE'
            WHEN market_type ~ '(_spread|_handicap(_[0-9]+)?)$' THEN 'SPREAD'
            WHEN market_type ~ '(_total|_total_games|_total_sets|_total_maps|_total_rounds(_[0-9]+)?|_total_goals|_total_runs)$' THEN 'TOTAL'
            WHEN market_type ~ '(team_total|_tt)' THEN 'TEAM_TOTAL'
            WHEN market_type ~ '(player|_prop|_props)' THEN 'PLAYER_PROP'
            WHEN market_type IS NULL THEN 'NO_MARKET_TYPE'
            ELSE 'OTHER' END AS market_class,
       CASE WHEN period = 'FULL_EVENT' THEN 'FULL' ELSE 'PERIOD_OR_OTHER' END AS span,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;

\echo == D5 PMUS settlement-not-proven rows by native market_type, top 40
SELECT coalesce(market_type, '(null)') AS market_type, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;

\echo == D6 PMUS settlement-not-proven rows by event start bucket (can the contract still trade)
SELECT CASE WHEN event_start IS NULL THEN 'a_no_start'
            WHEN event_start < now() - interval '24 hours' THEN 'b_started_over_24h_ago'
            WHEN event_start < now() THEN 'c_started_0_24h_ago'
            WHEN event_start < now() + interval '48 hours' THEN 'd_next_48h'
            WHEN event_start < now() + interval '7 days' THEN 'e_2_7_days' ELSE 'f_over_7_days' END AS start_bucket,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN'
 GROUP BY 1 ORDER BY 1 LIMIT 10;

\echo == D7 distinct Pinnacle-valued contracts per day, last 7 days (the fair value source universe)
SELECT date_trunc('day', decided_at) AS day, count(DISTINCT us_market_slug) AS valued_contracts,
       count(*) AS valuations
  FROM external_valuations
 WHERE decided_at > now() - interval '7 days' AND probability IS NOT NULL AND us_market_slug IS NOT NULL
 GROUP BY 1 ORDER BY 1 LIMIT 10;

\echo == D8 the settlement bucket for valued contracts: the settlement verdict a valuation recorded, last 24 h
SELECT coalesce(settlement_comparison->>'verdict', settlement_comparison->>'status', settlement_comparison->>'compatibility', '(null)') AS verdict,
       record_purpose, count(DISTINCT us_market_slug) AS contracts
  FROM external_valuations
 WHERE decided_at > now() - interval '24 hours' AND probability IS NOT NULL AND us_market_slug IS NOT NULL
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == D9 plane service identity: newest plane heartbeat (which commit writes the coverage state)
SELECT key, left(value::text, 600) AS value_head
  FROM ingestion_state WHERE key LIKE '%market_plane%' OR key LIKE '%universal_market%' ORDER BY key LIMIT 6;

\echo == D10 Kalshi book freshness in the registry rows is not an input of the coverage pass: Kalshi WS books held by the plane
SELECT payload->'kalshi_ws' AS kalshi_ws_head
  FROM market_plane_events WHERE kind = 'SNAPSHOT' ORDER BY at DESC LIMIT 1;
