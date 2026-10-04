-- P0 INCIDENT (coverage -> trade starvation), FULL FUNNEL RECEIPT, PART D (read-only).
-- Precise causes behind three losses parts B/C named generically: Derek's would-be
-- entries refused by its entry switch, paper ENTRY orders expired on an "unreadable"
-- book, and NCAAF venue events with no provider event (ET 2026-10-03, the Saturday slate).
\echo '== D1 . paper_control rows (strategy switches) =='
SELECT control_key, enabled, left(coalesce(why,''), 160) AS why, updated_by
  FROM paper_control ORDER BY control_key;

\echo '== D2 . Derek 24 h: decisions refused ONLY at its entry switch (STRATEGY_ENTRIES_DISABLED), per league =='
SELECT split_part(us_market_slug,'-',2) AS league, count(*) AS rows,
       count(DISTINCT (us_market_slug, holding_side)) AS opportunities,
       count(*) FILTER (WHERE refusals = ARRAY['STRATEGY_ENTRIES_DISABLED']::text[]) AS rows_switch_only,
       count(DISTINCT (us_market_slug, holding_side)) FILTER (WHERE refusals = ARRAY['STRATEGY_ENTRIES_DISABLED']::text[]) AS opps_switch_only,
       left(max(economics->>'headline'), 160) AS a_headline
  FROM paper_decisions
 WHERE decided_at > now() - interval '24 hours' AND strategy = 'DEREK_ENTRY_POLICY_V2'
   AND 'STRATEGY_ENTRIES_DISABLED' = ANY(refusals)
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== D3 . ENTRY orders expired on THE_OBSERVED_BOOK_WAS_UNREADABLE 24 h: the observation''s own error text =='
WITH x AS (
    SELECT po.order_id, po.strategy, e.detail->>'book_obs_id' AS obs
      FROM paper_orders po JOIN paper_order_events e ON e.order_id = po.order_id AND e.kind = 'EXPIRED'
     WHERE po.role = 'ENTRY' AND po.created_at > now() - interval '24 hours'
       AND po.terminal_reason = 'THE_OBSERVED_BOOK_WAS_UNREADABLE')
SELECT x.strategy, left(coalesce(b.error, '<no error text>'), 140) AS observation_error,
       count(DISTINCT x.order_id) AS orders, count(*) FILTER (WHERE x.obs IS NULL) AS no_obs_id_on_event
  FROM x LEFT JOIN paper_book_observations b
         ON b.obs_id = CASE WHEN x.obs ~ '^[0-9]+$' THEN x.obs::bigint END
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
SELECT left(coalesce(error, '<none>'), 120) AS observation_error, count(*) AS observations,
       count(DISTINCT us_market_slug) AS markets
  FROM paper_book_observations WHERE observed_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 15;

\echo '== D4 . NCAAF (venue token cfb) ET 2026-10-03: venue full-game winner events vs provider events in the ledger =='
WITH v AS (
    SELECT DISTINCT market_slug, event_slug, min(game_start) OVER (PARTITION BY market_slug) AS gs
      FROM us_premap
     WHERE lower(split_part(coalesce(event_slug,''),'-',1)) = 'cfb'
       AND sports_type LIKE '%full_game_winner'
       AND game_start >= timestamptz '2026-10-03 04:00Z' AND game_start < timestamptz '2026-10-04 04:00Z'),
l AS (
    SELECT DISTINCT us_market_slug FROM ext_candidate_outcomes
     WHERE sport_key = 'americanfootball_ncaaf' AND us_market_slug IS NOT NULL
       AND cycle_at >= timestamptz '2026-10-02 04:00Z' AND cycle_at < timestamptz '2026-10-04 04:00Z'),
p AS (
    SELECT count(DISTINCT provider_event_id) AS provider_events
      FROM ext_candidate_outcomes
     WHERE sport_key = 'americanfootball_ncaaf'
       AND cycle_at >= timestamptz '2026-10-02 04:00Z' AND cycle_at < timestamptz '2026-10-04 04:00Z'
       AND commence_time >= '2026-10-03T04' AND commence_time < '2026-10-04T04')
SELECT (SELECT count(DISTINCT event_slug) FROM v) AS venue_events,
       (SELECT count(DISTINCT market_slug) FROM v) AS venue_markets,
       (SELECT count(DISTINCT v.market_slug) FROM v JOIN l ON l.us_market_slug = v.market_slug) AS venue_markets_mapped_by_the_ledger,
       (SELECT provider_events FROM p) AS provider_events_starting_that_day;
