-- READ-ONLY. The NCAAF funnel per VENUE EVENT (us_premap team_league cfb,
-- game in the next 7 days or started < 6 h ago): contracts by type, how far
-- each event got through the collector (external_valuations) and the paper
-- decision lane (paper_decisions), and the exact codes where it stopped.
-- Every statement is a SELECT.
WITH ev AS (
  SELECT event_slug, min(game_start) AS game_start,
         count(DISTINCT market_slug) AS contracts,
         count(DISTINCT market_slug) FILTER (WHERE sports_type LIKE '%winner') AS winner,
         count(DISTINCT market_slug) FILTER (WHERE sports_type LIKE '%spread') AS spread,
         count(DISTINCT market_slug) FILTER (WHERE sports_type LIKE '%total%') AS totals,
         array_agg(DISTINCT sports_type) AS types,
         array_agg(DISTINCT market_slug) AS slugs
    FROM us_premap
   WHERE team_league = 'cfb'
     AND game_start > now() - interval '6 hours'
     AND game_start < now() + interval '7 days'
   GROUP BY event_slug),
val AS (
  SELECT e.event_slug,
         count(v.id) AS valuations,
         count(v.id) FILTER (WHERE v.probability IS NOT NULL) AS with_p,
         count(v.id) FILTER (WHERE v.probability IS NOT NULL AND v.age_s <= 30) AS fresh_p,
         count(v.id) FILTER (WHERE v.executable_price IS NOT NULL) AS with_book,
         count(v.id) FILTER (WHERE v.admissible) AS admissible,
         max(v.decided_at) AS last_val
    FROM ev e LEFT JOIN external_valuations v
      ON v.us_market_slug = ANY(e.slugs) AND v.decided_at > now() - interval '24 hours'
   GROUP BY 1),
dec AS (
  SELECT e.event_slug, count(d.decision_id) AS decisions,
         count(d.decision_id) FILTER (WHERE d.verdict = 'ENTER') AS entered,
         max(d.decided_at) AS last_dec
    FROM ev e LEFT JOIN paper_decisions d
      ON d.us_market_slug = ANY(e.slugs) AND d.decided_at > now() - interval '24 hours'
   GROUP BY 1)
SELECT count(*) AS venue_events, sum(contracts) AS contracts,
       sum(winner) AS winner, sum(spread) AS spread, sum(totals) AS totals,
       count(*) FILTER (WHERE valuations > 0) AS reached_collector,
       count(*) FILTER (WHERE with_p > 0) AS fair_value,
       count(*) FILTER (WHERE with_book > 0) AS current_book,
       count(*) FILTER (WHERE fresh_p > 0) AS prob_fresh,
       count(*) FILTER (WHERE decisions > 0) AS evaluated,
       count(*) FILTER (WHERE admissible > 0) AS approved_by_collector,
       count(*) FILTER (WHERE entered > 0) AS entered
  FROM ev JOIN val USING (event_slug) JOIN dec USING (event_slug);
-- the collector's refusal codes on cfb contracts, last 24 h
SELECT r AS code, count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts
  FROM external_valuations v, unnest(coalesce(v.refusals, ARRAY[]::text[])) r
 WHERE v.decided_at > now() - interval '24 hours'
   AND v.us_market_slug IN (SELECT market_slug FROM us_premap WHERE team_league = 'cfb')
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;
-- the paper decision lane's refusals on cfb contracts, last 24 h
SELECT d.strategy, coalesce(d.refusal, d.verdict) AS code, count(*),
       count(DISTINCT d.us_market_slug) AS contracts
  FROM paper_decisions d
 WHERE d.decided_at > now() - interval '24 hours'
   AND d.us_market_slug IN (SELECT market_slug FROM us_premap WHERE team_league = 'cfb')
 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
-- events the collector never reached: their contract types and start
WITH ev AS (
  SELECT event_slug, min(game_start) gs, array_agg(DISTINCT sports_type) types,
         array_agg(DISTINCT market_slug) slugs
    FROM us_premap WHERE team_league = 'cfb'
     AND game_start > now() - interval '6 hours' AND game_start < now() + interval '7 days'
   GROUP BY 1)
SELECT CASE WHEN gs < now() + interval '96 hours' THEN 'lt96h' ELSE 'gt96h' END AS horizon,
       count(*) AS events
  FROM ev
 WHERE NOT EXISTS (SELECT 1 FROM external_valuations v
                    WHERE v.us_market_slug = ANY(ev.slugs)
                      AND v.decided_at > now() - interval '24 hours')
 GROUP BY 1;
-- the collector's per-competition coverage receipts for NCAAF (latest)
SELECT key, left(value::text, 600) FROM ingestion_state
 WHERE key ILIKE '%ncaaf%' OR key ILIKE '%cfb%' LIMIT 10;
SELECT competition, max(at), left(max(receipt::text), 600)
  FROM collector_coverage_receipts
 WHERE competition ILIKE '%ncaaf%' AND at > now() - interval '6 hours'
 GROUP BY 1;
