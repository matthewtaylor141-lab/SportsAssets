-- R30A NFL STREAM (review fixes), read-only. Three questions the first pass
-- answered too broadly or not at all:
--
-- W1  Is the venue's NFL rules wording the SAME on EVERY production NFL row?
--     The first read (r30a_nfl_measured_outcome_set.sql, M4) grouped by the md5
--     of the UNMASKED text with LIMIT 10, so it showed 10 of 15 rows, one per
--     contract. This groups by the text with the game name and date MASKED, with
--     NO limit, so every row is counted in exactly one wording group.
-- W2  What does the venue catalogue row of an NFL contract carry (sports_type,
--     team_league, identifier vs market_slug)? pinnapi_census.family_of admits
--     football_team_full_game_winner for the NFL ONLY from the row's team_league
--     and its venue-native identifier, which must agree.
-- W3  Where do the NFL catalogue rows' game dates fall (America/New_York day)
--     against the cited 2026 regular season (2026-09-09 .. 2027-01-10)?
\echo '== W0 · read instant =='
SELECT now() AS read_at, (SELECT max(version) FROM schema_migrations) AS schema_head;

\echo '== W1a · every NFL valuation row: rules-text presence =='
SELECT count(*) AS rows,
       count(DISTINCT us_market_slug) AS contracts,
       count(*) FILTER (WHERE settlement_comparison ? 'venue_rules_text') AS with_text,
       count(*) FILTER (WHERE NOT coalesce(settlement_comparison ? 'venue_rules_text', false)) AS without_text
  FROM external_valuations
 WHERE sport_family = 'football' AND us_market_slug LIKE 'aec-nfl-%';

\echo '== W1b · every NFL valuation row, grouped by MASKED wording (no limit) =='
SELECT md5(masked) AS masked_md5, count(*) AS rows,
       count(DISTINCT us_market_slug) AS contracts,
       min(decided_at) AS first_at, max(decided_at) AS last_at,
       masked AS masked_text
  FROM (SELECT us_market_slug, decided_at,
               regexp_replace(settlement_comparison->>'venue_rules_text',
                              'This market will settle to the winner of the .* NFL game scheduled for [A-Za-z]+ [0-9]+, [0-9]{4}\.',
                              '<WINNER-OF-NAMED-GAME-ON-DATE>.') AS masked
          FROM external_valuations
         WHERE sport_family = 'football' AND us_market_slug LIKE 'aec-nfl-%'
           AND settlement_comparison ? 'venue_rules_text') t
 GROUP BY masked ORDER BY 2 DESC;

\echo '== W2 · NFL catalogue rows (us_premap): the fields the census reads =='
SELECT sports_type, lower(coalesce(team_league, '<null>')) AS team_league,
       split_part(identifier, '-', 1) || '-' || split_part(identifier, '-', 2) AS identifier_prefix,
       (identifier = market_slug) AS identifier_is_market_slug,
       kind, (line IS NULL OR line::text = '') AS line_blank,
       count(*) AS rows, count(DISTINCT market_slug) AS contracts
  FROM us_premap
 WHERE market_slug LIKE 'aec-nfl-%' AND game_start > now() - interval '6 hours'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 7 DESC;

\echo '== W3 · NFL catalogue game dates (America/New_York day) vs the cited 2026 regular season =='
SELECT count(*) FILTER (WHERE d < date '2026-09-09') AS before_regular_season,
       count(*) FILTER (WHERE d BETWEEN date '2026-09-09' AND date '2027-01-10') AS in_regular_season,
       count(*) FILTER (WHERE d > date '2027-01-10') AS after_regular_season,
       min(d) AS first_day, max(d) AS last_day,
       count(*) FILTER (WHERE substring(market_slug FROM '([0-9]{4}-[0-9]{2}-[0-9]{2})$')::date = d) AS slug_date_equals_et_day,
       count(*) FILTER (WHERE substring(market_slug FROM '([0-9]{4}-[0-9]{2}-[0-9]{2})$')::date
                              = (game_start AT TIME ZONE 'UTC')::date) AS slug_date_equals_utc_day,
       count(*) AS rows
  FROM (SELECT market_slug, game_start,
               (game_start AT TIME ZONE 'America/New_York')::date AS d
          FROM us_premap
         WHERE market_slug LIKE 'aec-nfl-%' AND game_start > now() - interval '6 hours') t;
