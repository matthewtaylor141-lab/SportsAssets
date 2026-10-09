-- READ-ONLY. RC6 lane xavier-records: was the CSC held read's NO_FEED_EVENT
-- (1,939 reviews, 2026-10-05 00:56Z .. 2026-10-08 19:00Z) IDENTITY (PinnAPI
-- held the fixture under other names) or COVERAGE (PinnAPI held no such
-- fixture)? Evidence: every valuation of the CSC contracts and of the other
-- Brazil Serie B contracts by provider and event key -- a valuation keyed
-- pinnapi:<id> was written from the PinnAPI cache by the discovery matcher,
-- so it proves the provider fixture existed in the feed; plus the venue's own
-- structured team names the held read compared exactly. Every statement is
-- a SELECT.

\echo C1 the CSC event: venue catalogue rows (structured names the held read compares exactly)
SELECT market_slug, to_jsonb(p)->>'team_name' AS team_name,
       to_jsonb(p)->>'team_safe_name' AS team_safe_name,
       to_jsonb(p)->>'team_abbr' AS team_abbr,
       to_jsonb(p)->>'team_league' AS league,
       to_jsonb(p)->>'sports_type' AS sports_type,
       to_jsonb(p)->>'game_start' AS game_start, p.event_title
  FROM us_premap p
 WHERE p.event_slug = 'brb-csc-cri-2026-10-08'
    OR p.market_slug LIKE 'atc-brb-csc-cri-2026-10-08%'
 ORDER BY 1 LIMIT 20;

\echo C2 every valuation of the CSC event contracts by provider, book, event key and version
SELECT e.us_market_slug, e.provider, e.book, e.version, e.event_key,
       e.contract_selection, count(*), min(e.decided_at), max(e.decided_at),
       max(e.observed_at) AS newest_observed
  FROM external_valuations e
 WHERE e.us_market_slug LIKE 'atc-brb-csc-cri-2026-10-08%'
 GROUP BY 1,2,3,4,5,6 ORDER BY 1, 7 DESC LIMIT 40;

\echo C3 Brazil Serie B contracts (us_premap league brb), last 10 days: valuations by provider and whether keyed to a PinnAPI fixture
SELECT e.provider, e.book, (e.event_key LIKE 'pinnapi:%') AS pinnapi_fixture_key,
       count(DISTINCT e.us_market_slug) AS contracts,
       count(DISTINCT e.event_key) AS event_keys, count(*) AS valuations,
       max(e.decided_at) AS newest
  FROM external_valuations e
 WHERE e.decided_at > now() - interval '10 days'
   AND e.us_market_slug IN (SELECT p.market_slug FROM us_premap p
                             WHERE to_jsonb(p)->>'team_league' = 'brb')
 GROUP BY 1,2,3 ORDER BY 6 DESC LIMIT 20;

\echo C4 Brazil Serie B events with a PinnAPI-keyed valuation (the discovery matcher found the fixture), last 10 days
SELECT p.event_slug, min(e.event_key) AS pinnapi_key, count(*) AS valuations,
       max(e.decided_at) AS newest
  FROM external_valuations e JOIN us_premap p ON p.market_slug = e.us_market_slug
 WHERE e.decided_at > now() - interval '10 days'
   AND e.event_key LIKE 'pinnapi:%'
   AND to_jsonb(p)->>'team_league' = 'brb'
 GROUP BY 1 ORDER BY 1 LIMIT 40;

\echo C5 the CSC entry valuation (by id from its ENTRY decision): provider, fixture key, outcome names
SELECT v.id, v.provider, v.book, v.version, v.event_key, v.market, v.period,
       v.contract_selection, v.mapped_outcome,
       to_jsonb(v)->>'payout_event' AS payout_event,
       left(v.raw_odds::text, 400) AS raw_odds, v.observed_at, v.received_at
  FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
  JOIN external_valuations v ON v.id = d.valuation_id
 WHERE o.group_id = 'paperexpgrp:73296cf5e61c08e0a67e450f' AND o.role = 'ENTRY';

\echo C6 the PinnAPI coverage census and discovery as last recorded in the feed heartbeat (soccer, Brazil)
SELECT left(coalesce((value->'coverage_census')::text, ''), 1200) AS coverage_census
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left(coalesce((value->'native_discovery')::text, ''), 1500) AS native_discovery
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
