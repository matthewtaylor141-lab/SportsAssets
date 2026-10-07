-- READ-ONLY. KALSHI CANONICAL VENUE V1 -- the PMUS moneyline catalogue rows a
-- Kalshi game fixture would map to: league, both sides' venue team identity
-- (abbr / id), start, by league, next 48 h. SELECT only.
SELECT team_league, count(DISTINCT market_slug) markets, count(DISTINCT event_slug) events,
       min(market_slug) sample_slug,
       string_agg(DISTINCT team_abbr, ',' ORDER BY team_abbr) FILTER (WHERE team_abbr IS NOT NULL) abbrs
  FROM us_premap
 WHERE market_slug LIKE 'aec-%' AND game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
SELECT market_slug, event_slug, side_norm, intent, team_abbr, team_name, team_id, team_league,
       game_start, sports_type, listing_state
  FROM us_premap
 WHERE market_slug LIKE 'aec-mlb-%' AND game_start BETWEEN now() - interval '6 hours' AND now() + interval '48 hours'
 ORDER BY game_start, market_slug, side_norm LIMIT 40;
