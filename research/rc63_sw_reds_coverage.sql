-- READ-ONLY. RC6.3b root-cause audit, group software-reds, part 4: the market
-- plane registry (market_plane_registry, active rows) behind the packet unit
-- active_contracts_priceable (PRICEABLE 106, CODE_CONTROLLED_GAP 70360,
-- MAPPED_BUT_SETTLEMENT_NOT_PROVEN 88958, market_plane.json snapshot.coverage
-- computed_at 1791626416.73), by the named reason the plane wrote. SELECT only.

\echo == C0 plane snapshots around the packet read (which code wrote the coverage)
SELECT at, payload->>'version' AS version, payload->'coverage'->>'active' AS active,
       payload->'coverage'->'by_state' AS by_state,
       (payload->'coverage') ? 'waterfall' AS has_waterfall,
       payload->'runtime'->>'commit' AS runtime_commit
  FROM market_plane_events
 WHERE kind = 'SNAPSHOT' AND at BETWEEN timestamptz '2026-10-10 09:50:00+00' AND timestamptz '2026-10-10 10:10:00+00'
 ORDER BY at LIMIT 6;

\echo == C0b newest plane snapshot now
SELECT at, payload->>'version' AS version, payload->'coverage'->>'active' AS active,
       payload->'coverage'->'by_state' AS by_state,
       (payload->'coverage') ? 'waterfall' AS has_waterfall
  FROM market_plane_events WHERE kind = 'SNAPSHOT' ORDER BY at DESC LIMIT 2;

\echo == C1 active registry by venue and coverage_state (now)
SELECT venue, coalesce(coverage_state, 'NOT_YET_CLASSIFIED') AS coverage_state, count(*) AS n
  FROM market_plane_registry WHERE active GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo == C2 CODE_CONTROLLED_GAP and PRICEABLE by named coverage_why (now), all reasons
SELECT venue, coverage_state, coverage_why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND coverage_state IN ('CODE_CONTROLLED_GAP', 'PRICEABLE', 'MAPPED_BUT_NO_FAIR_VALUE_SOURCE', 'EXTERNAL_DATA_UNAVAILABLE')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 70;

\echo == C3 MAPPED_BUT_SETTLEMENT_NOT_PROVEN by venue, settlement_state and prefix of the settlement reason (now)
SELECT venue, settlement_state,
       CASE WHEN settlement_why LIKE 'BOOKMAKER_TERMS_NOT_HELD:%' AND settlement_why LIKE '%/%/%' THEN 'BOOKMAKER_TERMS_NOT_HELD:<sport>/<family>/<period>'
            ELSE left(coalesce(settlement_why, '(null)'), 150) END AS reason_class,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo == C3b the BOOKMAKER_TERMS_NOT_HELD families (sport/family/period) in the settlement bucket (now)
SELECT venue, substring(settlement_why FROM 'BOOKMAKER_TERMS_NOT_HELD:(.*)$') AS sport_family_period, count(*) AS n
  FROM market_plane_registry
 WHERE active AND coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN' AND settlement_why LIKE 'BOOKMAKER_TERMS_NOT_HELD:%'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 70;

\echo == C3c the settlement bucket by the other named reasons (not BOOKMAKER_TERMS_NOT_HELD) (now)
SELECT venue, left(coalesce(settlement_why, '(null)'), 170) AS why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND coverage_state = 'MAPPED_BUT_SETTLEMENT_NOT_PROVEN'
   AND (settlement_why IS NULL OR settlement_why NOT LIKE 'BOOKMAKER_TERMS_NOT_HELD:%')
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;

\echo == C4 the Kalshi rows by coverage_state, sport and family (now)
SELECT coverage_state, coalesce(sport, 'NULL') AS sport, coalesce(family, 'NULL') AS family, count(*) AS n
  FROM market_plane_registry WHERE active AND venue = 'KALSHI'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;

\echo == C5 event start buckets by coverage_state (now): is the bucket contracts that can ever trade
SELECT coverage_state,
       CASE WHEN event_start IS NULL THEN 'a_no_start'
            WHEN event_start < now() - interval '24 hours' THEN 'b_started_over_24h_ago'
            WHEN event_start < now() THEN 'c_started_0_24h_ago'
            WHEN event_start < now() + interval '48 hours' THEN 'd_next_48h'
            WHEN event_start < now() + interval '7 days' THEN 'e_2_7_days'
            ELSE 'f_over_7_days' END AS start_bucket,
       venue, count(*) AS n
  FROM market_plane_registry WHERE active
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3 LIMIT 60;

\echo == C6 required_reason (priority tier) by coverage_state (now)
SELECT coalesce(required_reason, '(null)') AS required_reason, coverage_state, count(*) AS n
  FROM market_plane_registry WHERE active
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo == C7 contracts with a Pinnacle probability in the last 24 h (the fair value source universe) by registry state
WITH v AS (SELECT DISTINCT us_market_slug FROM external_valuations
            WHERE decided_at > now() - interval '24 hours' AND probability IS NOT NULL AND us_market_slug IS NOT NULL)
SELECT r.venue, r.coverage_state, r.settlement_state, count(*) AS n
  FROM market_plane_registry r JOIN v ON v.us_market_slug = r.contract_id
 WHERE r.active GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;

\echo == C8 the proven-settlement contracts (116 in the packet) by coverage_state and why (now)
SELECT settlement_state, coverage_state, coverage_why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND settlement_state IN ('SETTLEMENT_PROVEN_COMPATIBLE', 'SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 12;

\echo == C9 Polymarket US active rows with a known sport and a money-line family: settlement and coverage reason (now)
SELECT sport, family, period, coverage_state, left(coalesce(coverage_why, settlement_why, '(null)'), 120) AS why, count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND family = 'WINNER' AND period = 'FULL_EVENT'
 GROUP BY 1, 2, 3, 4, 5 ORDER BY 6 DESC LIMIT 40;
