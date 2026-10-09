-- RC6 lane D2, read only. (1) The real registry rows and the venue's own
-- published rules text of a handful of line contracts (full game and period),
-- so the lane's tests run on production rows, not invented ones. (2) The
-- venue league token (event slug's first segment) against the sport the
-- typed rows of that token carry, and the futures tokens, so the league ->
-- sport table is checked against the venue's own typed rows.
\echo === F1. fixture rows: registry identity + full rules text ===
SELECT g.contract_id, g.market_type, g.event_id, g.competition, g.sport,
       g.family, g.period, g.ontology->'meaning'->>'line' AS meaning_line,
       r.rules_sha256, r.parse_status,
       regexp_replace(r.rules_text, '\s+', ' ', 'g') AS rules_text
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.contract_id IN (
   'asc-cfb-airf-nill-2026-10-10-neg-10pt5',
   'tsc-cfb-airf-nill-2026-10-10-total-19pt5',
   'tsc-cfb-airf-nill-2026-10-10-tt-airf-16pt5',
   'asc-nhl-ana-cgy-2026-10-10-neg-1pt5',
   'tsc-nhl-ana-cgy-2026-10-10-2pt5',
   'tsc-nhl-ana-cgy-2026-10-10-tt-ana-0pt5',
   'asc-nba-atl-orl-2026-10-21-pos-2pt5',
   'tsc-nba-atl-orl-2026-10-21-233pt5',
   'asc-mlb-cle-cws-2026-10-08-neg-1pt5',
   'tsc-mlb-cle-cws-2026-10-08-6pt5',
   'tsc-mlb-cle-cws-2026-10-08-tt-cle-2pt5',
   'asc-bun-aug-fcb-2026-10-10-neg-1pt5',
   'asc-cfb-airf-nill-2026-10-10-1h-neg-4pt5',
   'asc-cfb-airf-nill-2026-10-10-2h-neg-4pt5',
   'tsc-cfb-airf-nill-2026-10-10-1q-10pt5',
   'tsc-cfb-airf-nill-2026-10-10-2h-21pt5',
   'tsc-cfb-airf-nill-2026-10-10-tt1h-airf-11pt5',
   'asc-nhl-ana-cgy-2026-10-10-p3-neg-0pt5',
   'tsc-nhl-ana-cgy-2026-10-10-p1-0pt5')
 ORDER BY 1;
\echo === F2. one tennis games spread, one whole-number line of each full-game line type (if any) ===
SELECT DISTINCT ON (g.market_type) g.contract_id, g.market_type, g.event_id,
       g.competition, regexp_replace(r.rules_text, '\s+', ' ', 'g') AS rules_text
  FROM market_plane_registry g JOIN market_plane_rules r USING (contract_id)
 WHERE g.active AND g.venue = 'POLYMARKET_US'
   AND (g.market_type = 'tennis_match_games_spread'
        OR (g.market_type IN ('football_team_full_game_spread',
                              'football_team_full_game_total',
                              'hockey_team_full_game_total',
                              'basketball_team_full_game_total')
            AND g.contract_id !~ 'pt[0-9]+$'))
 ORDER BY g.market_type, g.contract_id;
\echo === F3. typed rows: venue league token x sport (from the market type) ===
SELECT split_part(event_id, '-', 1) AS tok, coalesce(sport, '<null>') AS sport,
       count(*) AS n
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND market_type IS NOT NULL
   AND market_type <> 'futures'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;
\echo === F4. futures rows: venue league token (count, two sample event ids) ===
SELECT split_part(event_id, '-', 1) AS tok, count(*) AS n,
       min(event_id) AS sample_a, max(event_id) AS sample_b
  FROM market_plane_registry
 WHERE active AND venue = 'POLYMARKET_US' AND market_type = 'futures'
 GROUP BY 1 ORDER BY 2 DESC;
\echo === F5. us_premap: event slug vs market slug first segment for the fixture events ===
SELECT DISTINCT event_slug, split_part(market_slug, '-', 1) AS market_kind,
       team_league, sports_type
  FROM us_premap
 WHERE event_slug IN ('cfb-airf-nill-2026-10-10', 'nhl-ana-cgy-2026-10-10',
                      'bun-2027-05-22-relegation', 'btc-range-hr-2026-10-09-0200z')
 ORDER BY 1, 2 LIMIT 40;
